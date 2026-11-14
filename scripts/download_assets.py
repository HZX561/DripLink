"""Download, checksum and safely restore DripLink's versioned research bundles."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PREFIXES={'models':'models/','data':'data/','cache':'data/cache/','reports':'reports/'}


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def check_archive(path,info):
    if Path(path).stat().st_size!=info['bytes'] or digest(path)!=info['sha256']:
        raise ValueError('Archive checksum/size mismatch: '+Path(path).name)


def assemble(info,directory):
    """Join independently verified parts and verify the original ZIP checksum."""
    directory=Path(directory);path=directory/info['name']
    if path.is_file():check_archive(path,info);return path
    if sum(p['bytes'] for p in info['parts'])!=info['bytes']:raise ValueError('Part sizes do not match archive size')
    for part in info['parts']:check_archive(directory/part['name'],part)
    temporary=path.with_name(path.name+'.assembling')
    try:
        with temporary.open('wb') as output:
            for part in info['parts']:
                with (directory/part['name']).open('rb') as source:shutil.copyfileobj(source,output,1024*1024)
        check_archive(temporary,info);temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
    return path


def restore(path,info,destination,kind,overwrite=False):
    """Validate every destination before writing; refuse conflicting local files by default."""
    check_archive(path,info);destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    plans=[];seen=set()
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            name=entry.filename;relative=PurePosixPath(name)
            if ('\\' in name or relative.is_absolute() or '..' in relative.parts or name in seen
                    or relative.as_posix()!=name.rstrip('/')
                    or not name.startswith(PREFIXES[kind]) or stat.S_ISLNK(entry.external_attr>>16)):
                raise ValueError('Unsafe archive member: '+name)
            seen.add(name)
            if entry.is_dir():continue
            target=destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(destination):raise ValueError('Destination escapes project: '+name)
            matches=False
            if target.exists():
                if not target.is_file():raise FileExistsError(target)
                with archive.open(entry) as stream:incoming=hashlib.file_digest(stream,'sha256').hexdigest()
                matches=digest(target)==incoming
                if not matches and not overwrite:raise FileExistsError('Different local file; use --overwrite explicitly: '+name)
            plans.append((entry,target,matches))
        temporary=destination/'.runtime/asset-restore'
        if not temporary.resolve().is_relative_to(destination):raise ValueError('Temporary directory escapes project')
        temporary.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as folder:
            staging=Path(folder)
            for number,(entry,target,matches) in enumerate(plans):
                if matches:continue
                staged=staging/str(number)
                with archive.open(entry) as source,staged.open('wb') as output:shutil.copyfileobj(source,output,1024*1024)
            for number,(_,target,matches) in enumerate(plans):
                if matches:continue
                target.parent.mkdir(parents=True,exist_ok=True);(staging/str(number)).replace(target)
    return len(plans)


def download(info,cache,asset_url=None):
    path=Path(cache)/info['name'];path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_file():
        check_archive(path,info);return path
    candidates=([asset_url] if asset_url else [])+[info['url']];failures=[]
    for url in candidates:
        part=path.with_name(path.name+'.part')
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'DripLink-asset-downloader','Accept':'application/octet-stream'})
            with urllib.request.urlopen(request,timeout=180) as response,part.open('wb') as output:
                shutil.copyfileobj(response,output,1024*1024)
            check_archive(part,info);part.replace(path);return path
        except (OSError,ValueError,urllib.error.URLError) as exc:
            if part.exists():part.unlink()
            failures.append(type(exc).__name__)
    raise RuntimeError(f'Could not download {info["name"]}: {", ".join(failures)}. Download it manually from the Release and use --local-archives.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundles',nargs='+',choices=list(PREFIXES),default=list(PREFIXES))
    parser.add_argument('--local-archives',type=Path,help='Use already downloaded ZIPs instead of the network')
    parser.add_argument('--destination',type=Path,default=ROOT);parser.add_argument('--overwrite',action='store_true')
    parser.add_argument('--download-only',action='store_true');args=parser.parse_args()
    index=json.loads((ROOT/'assets/release.json').read_text(encoding='utf-8'));urls={}
    if not args.local_archives:
        address=f'https://api.github.com/repos/{index["repository"]}/releases/tags/{index["tag"]}'
        req=urllib.request.Request(address,headers={'User-Agent':'DripLink-asset-downloader','Accept':'application/vnd.github+json'})
        try:
            with urllib.request.urlopen(req,timeout=30) as response:
                release=json.load(response);urls={a['name']:a['url'] for a in release['assets']}
        except (OSError,urllib.error.URLError):pass
    for kind in args.bundles:
        info=index['bundles'][kind];print(f'{kind}: {info["bytes"]/2**20:.2f} MiB',flush=True)
        if args.local_archives:
            if info.get('parts'):archive=assemble(info,args.local_archives)
            else:archive=args.local_archives/info['name'];check_archive(archive,info)
        else:
            cache=args.destination/'.runtime/downloads'
            if info.get('parts'):
                for part in info['parts']:download(part,cache,urls.get(part['name']))
                archive=assemble(info,cache)
            else:archive=download(info,cache,urls.get(info['name']))
        if args.download_only:print('Verified:',archive,flush=True)
        else:print(f'Restored {restore(archive,info,args.destination,kind,args.overwrite)} files.',flush=True)


if __name__=='__main__':main()
