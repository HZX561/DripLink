"""Fine-tune the selected YOLO detector on frozen full-pointer training data."""
import argparse
from pathlib import Path
import shutil
from watermeter.runtime import ROOT
import numpy as np
import yaml
from watermeter.data import DATA,NAMES,load_manifest,image_path,recover_images,sha256,write_json


def prepare_detector_data(directory=DATA):
    directory=Path(directory);manifest=load_manifest(directory);recover_images(directory)
    labels=directory/'labels';labels.mkdir(exist_ok=True);lists=directory/'detector';lists.mkdir(exist_ok=True)
    for split in ('train','val'):
        paths=[]
        for index in manifest['splits'][split]:
            r=manifest['records'][index];w,h=r['width'],r['height'];rows=[]
            for region in r['regions']:
                q=np.asarray(region['quad']);lo=np.maximum(q.min(0),0);hi=np.minimum(q.max(0),[w,h])
                if (hi<=lo).any():raise ValueError('Out-of-frame region: '+r['name'])
                values=[region['cls'],*((lo+hi)/2/[w,h]),*((hi-lo)/[w,h])]
                for x,y in q:values.extend([x/w,y/h,2] if 0<=x<=w and 0<=y<=h else [0,0,0])
                rows.append(' '.join(f'{v:.8f}' for v in values))
            (labels/Path(r['name']).with_suffix('.txt')).write_text('\n'.join(rows)+'\n',encoding='utf-8')
            paths.append(image_path(r,directory).resolve().as_posix())
        (lists/f'{split}.txt').write_text('\n'.join(paths)+'\n',encoding='utf-8')
    config={'path':directory.resolve().as_posix(),'train':(lists/'train.txt').resolve().as_posix(),
            'val':(lists/'val.txt').resolve().as_posix(),'kpt_shape':[4,3],'names':dict(enumerate(NAMES))}
    path=lists/'data.yaml';path.write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf-8');return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=DATA);parser.add_argument('--initial',type=Path,default=ROOT/'models/detector.pt')
    parser.add_argument('--name',required=True);parser.add_argument('--project',type=Path,default=ROOT/'runs/detector')
    parser.add_argument('--epochs',type=int,default=30);parser.add_argument('--batch',type=int,default=8)
    parser.add_argument('--device',default='0');parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();dataset=prepare_detector_data(args.data)
    if args.prepare_only:print(dataset);return
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(4);output=args.project/args.name
    if output.exists():raise FileExistsError(output)
    model=YOLO(str(args.initial));output.mkdir(parents=True)
    shutil.copy2(__file__,output/'train_detector.py')
    write_json(output/'provenance.json',{'scope':'full_pointer','manifest_sha256':sha256(args.data/'manifest.json'),
        'initial_sha256':sha256(args.initial),'test_used':False,'torch':str(torch.__version__),
        'arguments':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}})
    model.train(data=str(dataset),epochs=args.epochs,imgsz=512,batch=args.batch,device=args.device,workers=0,
                cache=False,seed=42,deterministic=True,optimizer='AdamW',lr0=.002,lrf=.03,weight_decay=.0005,
                warmup_epochs=2,patience=12,amp=False,plots=False,save=True,project=str(args.project),name=args.name,
                exist_ok=True,degrees=180,translate=.05,scale=.15,shear=0,perspective=0,fliplr=0,flipud=0,
                mosaic=0,mixup=0,copy_paste=0,close_mosaic=0,hsv_h=.015,hsv_s=.3,hsv_v=.3,verbose=False)


if __name__=='__main__':main()
