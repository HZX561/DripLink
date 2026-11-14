"""Check the selected model, frozen splits, local images and training caches."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import watermeter
import numpy as np
import torch
from watermeter.data import DATA,load_manifest,image_path,read_image,sha256,read_json,write_json,value_digits
from watermeter.model import load_reader
from watermeter.pipeline import load_selection,MeterRecognizer
from watermeter.runtime import ROOT


def verify_environment():
    if sys.version_info[:2]!=(3,11):raise RuntimeError('Python 3.11 is required')
    packages=('torch','torchvision','ultralytics','numpy','Pillow','opencv-python','PyYAML')
    return {'python':platform.python_version(),'executable':sys.executable,
            'packages':{p:importlib.metadata.version(p) for p in packages},
            'cuda_available':torch.cuda.is_available(),'research_assets_checked':False}


def verify(inference=False,device='cpu'):
    if sys.version_info[:2]!=(3,11):raise RuntimeError('Python 3.11 is required')
    config=load_selection();manifest=load_manifest();records=manifest['records'];splits=manifest['splits']
    assert config['manifest_sha256']==sha256(DATA/'manifest.json')
    ids=[i for key in ('train','val','test') for i in splits[key]]
    assert len(ids)==len(set(ids))==len(records)==3780
    assert set(ids)==set(range(len(records)))
    ownership={}
    for split,indices in splits.items():
        for i in indices:
            record=records[i];group=record['group']
            assert ownership.setdefault(group,split)==split,'Near-duplicate group crossed splits'
            assert sha256(image_path(record))==record['sha256'],record['name']
            assert value_digits(record['reading'])==record['target']
    meta=read_json(DATA/'cache/pred_metadata.json')
    assert meta['manifest_sha256']==config['manifest_sha256']
    assert meta['detector_sha256']==config['detector_sha256'] and not meta['test_processed']
    assert set(meta['indices'])==set(splits['train']+splits['val'])
    for prefix in ('gt','pred'):
        patches=np.load(DATA/f'cache/{prefix}_patches.npy',mmap_mode='r')
        mask=np.load(DATA/f'cache/{prefix}_masks.npy',mmap_mode='r')
        assert patches.shape==(3780,10,64,64) and patches.dtype==np.uint8
        assert mask.shape==(3780,10) and mask.dtype==np.bool_
        if prefix=='pred':assert not mask[splits['test']].any() and not patches[splits['test']].any()
    targets=np.load(DATA/'cache/targets.npy');states=np.load(DATA/'cache/gt_states.npy')
    assert np.array_equal(targets,np.asarray([r['target'] for r in records]))
    assert states.shape==(3780,10) and np.all((states==-100)|((states>=0)&(states<30)))
    model,checkpoint=load_reader(config['reader'])
    assert checkpoint['manifest_sha256']==config['manifest_sha256']
    parameters=sum(p.numel() for p in model.parameters());assert parameters==953194
    result={'python':platform.python_version(),'executable':sys.executable,'torch':str(torch.__version__),
            'ultralytics':importlib.metadata.version('ultralytics'),'cuda_available':torch.cuda.is_available(),
            'images_verified':len(records),'groups':len(ownership),'split_counts':{k:len(v) for k,v in splits.items()},
            'reader_parameters':parameters,'weight_checksums':'passed','cache_checks':'passed'}
    if inference:
        recognizer,_=MeterRecognizer.selected(device=device)
        image=read_image(image_path(records[splits['val'][0]]))
        result['inference']=recognizer.predict(image)['context']
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment-only',action='store_true',help='Check installed packages without local datasets or weights')
    parser.add_argument('--inference',action='store_true');parser.add_argument('--device',default='cpu')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.environment_only and args.inference:parser.error('--inference requires research assets')
    result=verify_environment() if args.environment_only else verify(args.inference,args.device)
    if args.output:write_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
