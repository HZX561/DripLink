"""Offline uploader contract tests: no camera, network or non-temporary data writes."""
import contextlib
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib import error
import uuid

SPEC = importlib.util.spec_from_file_location(
    'driplink_pi_uploader', Path(__file__).resolve().parents[1] / 'pi_client/uploader.py')
uploader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = uploader
SPEC.loader.exec_module(uploader)
JPEG = b'\xff\xd8offline-test\xff\xd9'


class Response:
    def __init__(self, code=200, payload=None):
        self.code = code
        self.payload = payload if payload is not None else {'record_id': 7, 'status': 'pending'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.code

    def read(self, size):
        if isinstance(self.payload, bytes):
            return self.payload[:size]
        return json.dumps(self.payload).encode()[:size]


class UploaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.outbox = self.root / 'outbox'
        self.image = self.root / 'sample.jpg'
        self.image.write_bytes(JPEG)
        self.config = uploader.Config('http://server:5001', 'meter-01', 'private-test-token')
        self.console = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.console)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        # A missed HTTP mock is a failing test, never an accidental network call.
        self.net_guard = mock.patch.object(uploader.request, 'build_opener',
                                           side_effect=AssertionError('Unexpected HTTP call'))
        self.net_guard.start()
        self.addCleanup(self.net_guard.stop)
        self.cam_guard = mock.patch.object(uploader.subprocess, 'run',
                                           side_effect=AssertionError('Unexpected camera call'))
        self.cam_guard.start()
        self.addCleanup(self.cam_guard.stop)

    def entry(self):
        path = uploader.enqueue(self.outbox, self.config.meter_id, self.image)
        return path, uploader.load_metadata(path)

    def opener(self, effects):
        opener = mock.MagicMock()
        opener.open.side_effect = effects
        patcher = mock.patch.object(uploader.request, 'build_opener', return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        return opener

    def test_environment_required_and_token_not_in_repr(self):
        values = {'DRIPLINK_SERVER_URL': 'http://server:5001/',
                  'DRIPLINK_METER_ID': 'meter-01', 'DRIPLINK_DEVICE_TOKEN': 'secret'}
        with mock.patch.dict(uploader.os.environ, values, clear=True):
            config = uploader.Config.from_env()
        self.assertEqual(config.server_url, 'http://server:5001')
        self.assertNotIn('secret', repr(config))
        with mock.patch.dict(uploader.os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                uploader.Config.from_env()
        values['DRIPLINK_SERVER_URL'] = 'http://user:pass@server:5001'
        with mock.patch.dict(uploader.os.environ, values, clear=True):
            with self.assertRaises(ValueError):
                uploader.Config.from_env()

    def test_camera_argv_and_timeout(self):
        for available, selected in [(['/bin/rpicam-still'], '/bin/rpicam-still'),
                                    ([None, '/bin/libcamera-still'], '/bin/libcamera-still')]:
            with self.subTest(selected=selected):
                with mock.patch.object(uploader.shutil, 'which', side_effect=available), \
                        mock.patch.object(uploader.subprocess, 'run') as run:
                    uploader.capture_image(self.image)
                run.assert_called_once_with(
                    [selected, '--nopreview', '--timeout', '2000', '--output', str(self.image)],
                    shell=False, timeout=45, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_capture_failure_removes_only_incomplete_entry(self):
        with mock.patch.object(uploader, 'capture_image', side_effect=subprocess.TimeoutExpired('camera', 45)):
            with self.assertRaises(subprocess.TimeoutExpired):
                uploader.enqueue(self.outbox, 'meter-01')
        self.assertEqual(uploader.queue_entries(self.outbox), [])

    def test_queue_uuid_utc_and_no_token(self):
        entry, metadata = self.entry()
        self.assertEqual(str(uuid.UUID(metadata['upload_id'])), entry.name)
        self.assertEqual(datetime.fromisoformat(metadata['captured_at']).utcoffset().total_seconds(), 0)
        self.assertEqual((entry / 'image.jpg').read_bytes(), JPEG)
        self.assertNotIn(self.config.token, (entry / 'metadata.json').read_text())
        self.assertEqual(uploader.load_metadata(entry)['upload_id'], metadata['upload_id'])

    def test_success_multipart_and_pending_review(self):
        entry, metadata = self.entry()
        opener = self.opener([Response()])
        self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'ok')
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'http://server:5001/api/device/upload')
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + self.config.token)
        for field in (b'name="file"', b'name="meter_id"', b'name="captured_at"', b'name="upload_id"',
                      b'Content-Type: image/jpeg', JPEG, metadata['upload_id'].encode()):
            self.assertIn(field, req.data)
        self.assertFalse(entry.exists())
        self.assertIn('record_id="7"', self.console.getvalue())
        self.assertIn('manual review required', self.console.getvalue())

    def test_network_retry_same_id_and_backoff_then_success(self):
        entry, metadata = self.entry()
        opener = self.opener([error.URLError('offline'), error.URLError('offline'), Response()])
        with mock.patch.object(uploader.time, 'sleep') as sleep:
            self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'ok')
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertTrue(all(req is requests[0] for req in requests))
        self.assertIn(metadata['upload_id'].encode(), requests[0].data)

    def test_interrupted_http_response_retries_same_entry(self):
        entry, metadata = self.entry()
        self.opener([uploader.http.client.IncompleteRead(b'partial'), Response()])
        with mock.patch.object(uploader.time, 'sleep') as sleep:
            self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'ok')
        sleep.assert_called_once_with(1)
        self.assertFalse(entry.exists())

    def test_network_exhaustion_retains_queue_for_next_run(self):
        entry, metadata = self.entry()
        self.opener([error.URLError('offline')] * 3)
        with mock.patch.object(uploader.time, 'sleep'):
            self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'deferred')
        self.assertEqual(uploader.load_metadata(entry)['state'], 'queued')
        self.opener([Response()])
        self.assertEqual(uploader.flush_outbox(self.outbox, self.config), 'ok')
        self.assertFalse(entry.exists())

    def test_401_blocked_without_retry_or_new_capture(self):
        entry, metadata = self.entry()
        opener = self.opener([error.HTTPError('http://server', 401, 'Unauthorized', {}, None)])
        with mock.patch.object(uploader.time, 'sleep') as sleep:
            self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'auth')
        sleep.assert_not_called()
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(uploader.load_metadata(entry)['http_status'], 401)
        with mock.patch.object(uploader, 'enqueue') as enqueue:
            self.assertEqual(uploader.run_once(self.config, self.outbox, self.image), 1)
        enqueue.assert_not_called()
        self.assertEqual(opener.open.call_count, 1)

    def test_http_permanent_and_transient_classification(self):
        for code in (400, 404, 413, 422, 302, 408, 429, 500, 503):
            with self.subTest(code=code):
                entry, metadata = self.entry()
                self.opener([error.HTTPError('http://server', code, 'error', {}, None)] * 3)
                with mock.patch.object(uploader.time, 'sleep') as sleep:
                    result = uploader.upload_entry(entry, metadata, self.config)
                transient = code in (408, 429, 500, 503)
                self.assertEqual(result, 'deferred' if transient else 'blocked')
                self.assertEqual(sleep.call_count, 2 if transient else 0)
                self.assertTrue(entry.exists())

    def test_old_queue_flushed_before_new_capture(self):
        entry, metadata = self.entry()
        events = []
        original = uploader.enqueue

        def enqueue(*args):
            events.append('capture')
            return original(*args)

        def upload(path, data, config):
            events.append('old' if path == entry else 'new')
            uploader.shutil.rmtree(path)
            return 'ok'

        with mock.patch.object(uploader, 'upload_entry', side_effect=upload), \
                mock.patch.object(uploader, 'enqueue', side_effect=enqueue):
            self.assertEqual(uploader.run_once(self.config, self.outbox, self.image), 0)
        self.assertEqual(events, ['old', 'capture', 'new'])

    def test_missing_or_bad_jpeg_does_not_block_good_upload_or_new_capture(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                bad, metadata = self.entry()
                metadata['captured_at'] = '2020-01-01T00:00:00+00:00'
                uploader.save_metadata(bad, metadata)
                if missing:
                    (bad / 'image.jpg').unlink()
                else:
                    (bad / 'image.jpg').write_bytes(b'corrupt-image')
                good, _ = self.entry()
                opener = self.opener([Response(), Response()])
                self.assertEqual(uploader.run_once(self.config, self.outbox, self.image), 0)
                saved = uploader.load_metadata(bad)
                self.assertEqual(saved['state'], 'blocked')
                self.assertEqual(saved['error'], 'local_corrupt')
                self.assertEqual(saved['upload_id'], metadata['upload_id'])
                self.assertTrue(bad.exists())
                if not missing:
                    self.assertEqual((bad / 'image.jpg').read_bytes(), b'corrupt-image')
                self.assertFalse(good.exists())
                self.assertEqual(opener.open.call_count, 2)
                self.assertEqual(uploader.flush_outbox(self.outbox, self.config), 'ok')
                self.assertEqual(opener.open.call_count, 2)

    def test_cap_invalid_jpeg_and_incomplete_entry(self):
        self.outbox.mkdir()
        for i in range(100):
            (self.outbox / str(i)).mkdir()
        with mock.patch.object(uploader, 'capture_image') as camera:
            with self.assertRaises(ValueError):
                uploader.enqueue(self.outbox, 'meter-01')
        camera.assert_not_called()
        self.image.write_bytes(b'not-a-jpeg')
        with self.assertRaises(ValueError):
            uploader.validate_jpeg(self.image)
        self.assertEqual(uploader.flush_outbox(self.outbox, self.config), 'ok')
        self.assertEqual(len(uploader.queue_entries(self.outbox)), 100)

    def test_oversize_jpeg_rejected_before_queue_or_network(self):
        self.assertEqual(uploader.MAX_JPEG_BYTES, 9 * 1024 * 1024)
        with self.image.open('wb') as stream:
            stream.write(b'\xff\xd8')
            stream.seek(uploader.MAX_JPEG_BYTES - 1)
            stream.write(b'\xff\xd9')
        with self.assertRaises(ValueError):
            uploader.enqueue(self.outbox, self.config.meter_id, self.image)
        self.assertEqual(uploader.queue_entries(self.outbox), [])

    def test_200_html_retried_and_retained_without_body_or_token_logging(self):
        entry, metadata = self.entry()
        body = ('<html>private-body ' + self.config.token + '</html>').encode()
        opener = self.opener([Response(payload=body)] * 3)
        with mock.patch.object(uploader.time, 'sleep') as sleep:
            self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'deferred')
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(uploader.load_metadata(entry)['state'], 'queued')
        self.assertTrue((entry / 'image.jpg').exists())
        self.assertNotIn('private-body', self.console.getvalue())
        self.assertNotIn(self.config.token, self.console.getvalue())

    def test_200_missing_or_invalid_receipt_retains_same_upload_id(self):
        for payload in ({'status': 'pending'}, {'id': 7, 'status': 'pending'},
                        {'record_id': None, 'status': 'pending'},
                        {'record_id': True, 'status': 'pending'},
                        {'record_id': 0, 'status': 'pending'},
                        {'record_id': ' ', 'status': 'pending'},
                        {'record_id': 7}, {'record_id': 7, 'status': 'unknown'}, []):
            with self.subTest(payload=payload):
                entry, metadata = self.entry()
                opener = self.opener([Response(payload=payload)] * 3)
                with mock.patch.object(uploader.time, 'sleep'):
                    self.assertEqual(uploader.upload_entry(entry, metadata, self.config), 'deferred')
                self.assertEqual(uploader.load_metadata(entry)['upload_id'], metadata['upload_id'])
                self.assertTrue((entry / 'image.jpg').exists())
                requests = [call.args[0] for call in opener.open.call_args_list]
                self.assertEqual(len(requests), 3)
                self.assertTrue(all(req is requests[0] for req in requests))
        for status in ('pending', 'confirmed', 'sample'):
            self.assertEqual(uploader.success_receipt(json.dumps(
                {'record': {'id': 9, 'status': status}}).encode()), (9, status))

    def test_response_cannot_reflect_token_to_logs(self):
        entry, metadata = self.entry()
        self.opener([Response(payload={'record_id': self.config.token, 'status': 'pending'})])
        uploader.upload_entry(entry, metadata, self.config)
        self.assertNotIn(self.config.token, self.console.getvalue())

    def test_interval_and_ctrl_c(self):
        with self.assertRaises(Exception):
            uploader.interval_seconds('59')
        self.assertEqual(uploader.interval_seconds('60'), 60)
        with mock.patch.object(uploader.Config, 'from_env', return_value=self.config), \
                mock.patch.object(uploader, 'run_once', side_effect=KeyboardInterrupt):
            self.assertEqual(uploader.main(['--once', '--outbox', str(self.outbox)]), 130)

    def test_redirect_handler_never_forwards_request(self):
        self.assertIsNone(uploader.NoRedirect().redirect_request(None, None, 307, '', {}, 'https://other'))


if __name__ == '__main__':
    unittest.main()
