"""Package the selected full-pointer research assets without changing their bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TAG='v0.1.0'


def sha256(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def build(output,tag=TAG):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((ROOT/'data/manifest.json').read_text(encoding='utf-8'))
    selection=json.loads((ROOT/'models/selection.json').read_text(encoding='utf-8'))
    fingerprint=sha256(ROOT/'data/manifest.json')
    assert fingerprint==selection['manifest_sha256']==(ROOT/'data/manifest.sha256').read_text().strip()
    assert manifest['scope']=='full_pointer' and len(manifest['records'])==3780
    for key in ('detector','reader'):assert sha256(ROOT/selection[key])==selection[key+'_sha256']
    images=[]
    for record in manifest['records']:
        path=ROOT/'data/images'/record['name']
        assert path.parent==ROOT/'data/images' and sha256(path)==record['sha256'],record['name']
        images.append(path)
    cache_meta=json.loads((ROOT/'data/cache/pred_metadata.json').read_text(encoding='utf-8'))
    assert cache_meta['manifest_sha256']==fingerprint and cache_meta['detector_sha256']==selection['detector_sha256']
    assert not cache_meta['test_processed'] and set(cache_meta['indices'])==set(manifest['splits']['train']+manifest['splits']['val'])
    def files(folder):return sorted(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='ASSET_SOURCE.txt')
    groups={
        'models':[ROOT/'models/detector.pt',ROOT/'models/reader.pt',ROOT/'models/selection.json']+files('models/provenance'),
        'data':[ROOT/'data/manifest.json',ROOT/'data/manifest.sha256',ROOT/'data/dataset.json']+images+files('data/provenance'),
        'cache':files('data/cache'),
        'reports':[ROOT/'reports/metrics.json']+sum((files(f'reports/{name}') for name in ('validation','historical_test','training','review','examples')),[])
    }
    note=('DripLink research assets, supplied for the selected full-pointer experiment.\n'
          'Data source: https://github.com/ZZZHANG-jx/WMeter-Reader\n'
          'Photographs and original annotations retain their original authorship and applicable terms; '
          'the source-code AGPL license does not relicense third-party data.\n'
          'Project checkpoints are distributed with the DripLink AGPL-3.0-only source release.\n'
          'The authors\' im.pkl/tr.pkl and the paused legacy 100-photo project are not included.\n'
          'Test scores are historical internal retests, not new blind/physical-meter-independent results.\n')
    prefixes={'models':'models/','data':'data/','cache':'data/cache/','reports':'reports/'}
    catalog={'repository':'HZX561/DripLink','tag':tag,'scope':'full_pointer','manifest_sha256':fingerprint,
             'image_count':3780,'split_counts':{k:len(v) for k,v in manifest['splits'].items()},'bundles':{}}
    for kind,paths in groups.items():
        name=f'driplink-{kind}-{tag}.zip';archive=output/name
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as target:
            for path in paths:
                assert path.resolve().is_relative_to(ROOT.resolve()) and not path.is_symlink(),path
                target.write(path,path.relative_to(ROOT).as_posix())
            target.writestr(prefixes[kind]+'ASSET_SOURCE.txt',note)
        with zipfile.ZipFile(archive) as target:assert target.testzip() is None
        info={'name':name,'sha256':sha256(archive),'bytes':archive.stat().st_size,'files':len(paths)+1,
              'unpacked_bytes':sum(p.stat().st_size for p in paths)+len(note.encode()),
              'url':f'https://github.com/HZX561/DripLink/releases/download/{tag}/{name}'}
        if kind=='cache' and info['bytes']>64*2**20:
            info.pop('url');info['parts']=[]
            with archive.open('rb') as source:
                number=0
                while chunk:=source.read(64*2**20):
                    number+=1;part=output/f'{name}.{number:03d}';part.write_bytes(chunk)
                    info['parts'].append({'name':part.name,'sha256':sha256(part),'bytes':part.stat().st_size,
                        'url':f'https://github.com/HZX561/DripLink/releases/download/{tag}/{part.name}'})
        catalog['bundles'][kind]=info
        print(f'{kind}: {info["files"]} files, {info["bytes"]/2**20:.2f} MiB; CRC passed',flush=True)
    preview=output/'prediction_vs_gt.jpg';preview.write_bytes((ROOT/'reports/examples/prediction_vs_gt.jpg').read_bytes())
    catalog['preview']={'name':preview.name,'sha256':sha256(preview),'bytes':preview.stat().st_size,
                        'url':f'https://github.com/HZX561/DripLink/releases/download/{tag}/{preview.name}'}
    (output/'release.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    checks=[output/info['name'] for info in catalog['bundles'].values()]
    checks += [output/p['name'] for info in catalog['bundles'].values() for p in info.get('parts',[])]
    checks += [preview,output/'release.json']
    (output/'SHA256SUMS.txt').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in checks),encoding='utf-8')
    return catalog


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/releases'/TAG)
    parser.add_argument('--tag',default=TAG);args=parser.parse_args();build(args.output,args.tag)
