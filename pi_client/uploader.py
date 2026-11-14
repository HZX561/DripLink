#!/usr/bin/env python3
"""Standard-library Raspberry Pi JPEG uploader; credentials only come from env."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib import error, parse, request
import uuid

MAX_QUEUE = 100
MAX_JPEG_BYTES = 9 * 1024 * 1024
MAX_ATTEMPTS = 3
DEFAULT_OUTBOX = Path.home() / '.local/share/driplink/outbox'


@dataclass(frozen=True)
class Config:
    server_url: str
    meter_id: str
    token: str = field(repr=False)

    @classmethod
    def from_env(cls):
        values = [os.environ.get(name, '').strip() for name in
                  ('DRIPLINK_SERVER_URL', 'DRIPLINK_METER_ID', 'DRIPLINK_DEVICE_TOKEN')]
        if not all(values):
            raise ValueError('Set DRIPLINK_SERVER_URL, DRIPLINK_METER_ID and DRIPLINK_DEVICE_TOKEN.')
        url = parse.urlsplit(values[0])
        if (url.scheme not in ('http', 'https') or not url.hostname or
                url.username or url.password or url.query or url.fragment):
            raise ValueError('Server URL must be an HTTP(S) base URL without credentials/query/fragment.')
        # Reject control characters before building HTTP headers; never echo values.
        if any(ord(ch) < 32 or ord(ch) == 127 for value in values for ch in value):
            raise ValueError('Environment values must not contain control characters.')
        return cls(values[0].rstrip('/'), values[1], values[2])


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization or a JPEG to another location.
        return None


def validate_jpeg(path):
    path = Path(path)
    size = path.stat().st_size
    if not 4 <= size <= MAX_JPEG_BYTES:
        raise ValueError('JPEG must be between 4 bytes and 9 MiB.')
    with path.open('rb') as stream:
        start = stream.read(2)
        stream.seek(-2, 2)
        end = stream.read(2)
    if start != b'\xff\xd8' or end != b'\xff\xd9':
        raise ValueError('Only JPEG images with JPEG start/end markers are accepted.')


def capture_image(path):
    camera = shutil.which('rpicam-still') or shutil.which('libcamera-still')
    if not camera:
        raise ValueError('No rpicam-still/libcamera-still found; use --image to test an existing JPEG.')
    # Capture output is intentionally not printed: keep diagnostics credential-free.
    subprocess.run([camera, '--nopreview', '--timeout', '2000', '--output', str(path)],
                   shell=False, timeout=45, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def save_metadata(entry, metadata):
    temporary = entry / 'metadata.json.tmp'
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(metadata, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(entry / 'metadata.json')


def queue_entries(outbox):
    if not outbox.exists():
        return []
    # Retain incomplete/corrupt directories for inspection; they also count toward the cap.
    return sorted((p for p in outbox.iterdir() if p.is_dir()), key=lambda p: p.name)


def enqueue(outbox, meter_id, image=None):
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    if len(queue_entries(outbox)) >= MAX_QUEUE:
        raise ValueError('Outbox limit (100) reached; inspect/remove old entries before capturing.')
    if image is not None:
        validate_jpeg(image)
    upload_id = str(uuid.uuid4())
    entry = outbox / upload_id
    entry.mkdir(mode=0o700)
    try:
        target = entry / 'image.jpg'
        if image is None:
            capture_image(target)
        else:
            shutil.copyfile(image, target)
        validate_jpeg(target)
        metadata = {'version': 1, 'upload_id': upload_id, 'meter_id': meter_id,
                    'captured_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'state': 'queued'}
        save_metadata(entry, metadata)
    except BaseException:
        # No completed capture/metadata exists yet. Never delete a queued upload.
        shutil.rmtree(entry, ignore_errors=True)
        raise
    return entry


def load_metadata(entry):
    with (entry / 'metadata.json').open(encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('Invalid metadata.')
    if str(uuid.UUID(data['upload_id'])) != entry.name:
        raise ValueError('Invalid upload id.')
    if not isinstance(data['meter_id'], str) or not data['meter_id']:
        raise ValueError('Invalid meter id.')
    captured = datetime.fromisoformat(data['captured_at'].replace('Z', '+00:00'))
    if captured.utcoffset() is None:
        raise ValueError('Capture time must include an offset.')
    if data.get('state') not in ('queued', 'blocked'):
        raise ValueError('Invalid queue state.')
    return data


def multipart(entry, metadata):
    validate_jpeg(entry / 'image.jpg')
    boundary = 'driplink-' + uuid.uuid4().hex
    body = bytearray()
    for key in ('meter_id', 'captured_at', 'upload_id'):
        body.extend(('--' + boundary + '\r\nContent-Disposition: form-data; name="' +
                     key + '"\r\n\r\n' + metadata[key] + '\r\n').encode('utf-8'))
    body.extend(('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; '
                 'filename="image.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode('ascii'))
    body.extend((entry / 'image.jpg').read_bytes())
    body.extend(('\r\n--' + boundary + '--\r\n').encode('ascii'))
    return boundary, bytes(body)


def safe_display(value, token):
    # Server-controlled response fields must not leak a reflected credential or terminal escapes.
    return json.dumps(str(value).replace(token, '[REDACTED]')[:200], ensure_ascii=True)


def success_receipt(raw):
    # A proxy login page or malformed 2xx must never acknowledge a durable upload.
    if len(raw) > 65536:
        return None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(result, dict):
        return None
    record = result.get('record')
    if not isinstance(record, dict):
        record = {}
    record_id = result.get('record_id', record.get('id'))
    valid_id = ((type(record_id) is int and record_id > 0) or
                (isinstance(record_id, str) and 0 < len(record_id) <= 200 and
                 bool(record_id.strip()) and
                 all(ord(ch) >= 32 and ord(ch) != 127 for ch in record_id)))
    status = result.get('status', record.get('status'))
    if not valid_id or status not in ('pending', 'confirmed', 'sample'):
        return None
    return record_id, status


def upload_entry(entry, metadata, config):
    try:
        boundary, body = multipart(entry, metadata)
    except (OSError, ValueError):
        metadata['state'] = 'blocked'
        metadata['error'] = 'local_corrupt'
        try:
            save_metadata(entry, metadata)
        except OSError:
            # A read-only/damaged entry must not block other durable uploads.
            print('Could not persist blocked marker; corrupt entry retained.', flush=True)
        print('Upload blocked: local_corrupt; retained for manual inspection.', flush=True)
        return 'blocked'
    req = request.Request(config.server_url + '/api/device/upload', data=body, method='POST',
                          headers={'Authorization': 'Bearer ' + config.token,
                                   'Content-Type': 'multipart/form-data; boundary=' + boundary})
    opener = request.build_opener(NoRedirect())
    for attempt in range(MAX_ATTEMPTS):
        code = None
        try:
            with opener.open(req, timeout=45) as response:
                code = response.getcode()
                raw = response.read(65537) if 200 <= code < 300 else b''
            if 200 <= code < 300:
                receipt = success_receipt(raw)
                if receipt is not None:
                    record_id, status = receipt
                    print('Uploaded record_id=' + safe_display(record_id, config.token) +
                          ' status=' + safe_display(status, config.token), flush=True)
                    if status == 'pending':
                        print('Pending: manual review required; upload does not imply billing.', flush=True)
                    shutil.rmtree(entry)
                    return 'ok'
                print('Invalid upload acknowledgment; queued image retained pending retry.', flush=True)
        except error.HTTPError as exc:
            code = exc.code
            exc.close()
        except (error.URLError, OSError, TimeoutError, http.client.HTTPException):
            pass
        # 408/429 and server/network failures are transient. Redirects are not followed.
        if code is not None and ((300 <= code < 500 and code not in (408, 429)) or code < 200):
            metadata['state'] = 'blocked'
            metadata['http_status'] = code
            save_metadata(entry, metadata)
            print('Upload blocked: HTTP %d; retained for manual inspection.' % code, flush=True)
            return 'auth' if code in (401, 403) else 'blocked'
        if attempt + 1 < MAX_ATTEMPTS:
            print('Temporary upload failure; retry %d/%d.' % (attempt + 2, MAX_ATTEMPTS), flush=True)
            time.sleep(2 ** attempt)
    print('Upload deferred after three attempts; durable outbox retained.', flush=True)
    return 'deferred'


def flush_outbox(outbox, config):
    queued = []
    for entry in queue_entries(outbox):
        try:
            metadata = load_metadata(entry)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            print('Incomplete/corrupt outbox entry retained; manual inspection required.', flush=True)
            continue
        if metadata['state'] == 'blocked':
            print('Blocked outbox entry retained; no automatic retry.', flush=True)
            # Do not create a new capture every timer tick with known invalid credentials.
            if metadata.get('http_status') in (401, 403):
                return 'auth'
            continue
        queued.append((metadata['captured_at'], entry, metadata))
    for _, entry, metadata in sorted(queued):
        result = upload_entry(entry, metadata, config)
        if result in ('auth', 'deferred'):
            return result
    return 'ok'


def run_once(config, outbox, image=None):
    previous = flush_outbox(outbox, config)
    if previous == 'auth':
        return 1
    entry = enqueue(outbox, config.meter_id, image)
    if previous == 'deferred':
        print('New capture queued behind older deferred uploads.', flush=True)
        return 1
    return 0 if upload_entry(entry, load_metadata(entry), config) == 'ok' else 1


def interval_seconds(value):
    seconds = int(value)
    if seconds < 60:
        raise argparse.ArgumentTypeError('--interval must be at least 60 seconds')
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='capture/upload once (default)')
    mode.add_argument('--interval', type=interval_seconds, help='repeat at least 60 seconds apart; prefer systemd timer')
    parser.add_argument('--image', type=Path, help='use an existing JPEG instead of the camera')
    parser.add_argument('--outbox', type=Path, default=DEFAULT_OUTBOX)
    args = parser.parse_args(argv)
    try:
        config = Config.from_env()
        if parse.urlsplit(config.server_url).scheme == 'http':
            print('LAN HTTP enabled: use HTTPS or WireGuard for production.', flush=True)
        while True:
            result = run_once(config, args.outbox.expanduser(), args.image)
            if args.interval is None:
                return result
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('Stopped; completed outbox entries remain available for the next run.', flush=True)
        return 130
    except (OSError, ValueError, subprocess.SubprocessError):
        # Never log exception objects: they may contain URLs, headers or camera output.
        print('Client error: verify environment, JPEG, camera and outbox permissions/capacity.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
