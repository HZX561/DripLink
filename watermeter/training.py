"""Reproducible pointer-only fine-tuning on the frozen training/validation splits."""
import argparse
import json
import random
import shutil
import time
from pathlib import Path
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import Dataset,DataLoader

from .runtime import ROOT
from .data import DATA,load_manifest,read_json,write_json,sha256
from .model import load_reader


class PackedDataset(Dataset):
    def __init__(self,directory,indices,prefix='pred',mix_gt=0.):
        cache=Path(directory)/'cache';self.indices=indices;self.mix_gt=mix_gt
        self.patches=np.load(cache/f'{prefix}_patches.npy',mmap_mode='r')
        self.mask=np.load(cache/f'{prefix}_masks.npy',mmap_mode='r')
        self.states=np.load(cache/'gt_states.npy',mmap_mode='r');self.targets=np.load(cache/'targets.npy',mmap_mode='r')
        self.gt=None
        if prefix!='gt' and mix_gt>0:
            self.gt=(np.load(cache/'gt_patches.npy',mmap_mode='r'),np.load(cache/'gt_masks.npy',mmap_mode='r'))

    def __len__(self):return len(self.indices)

    def __getitem__(self,index):
        i=self.indices[index]
        patches,mask=self.gt if self.gt is not None and np.random.random()<self.mix_gt else (self.patches,self.mask)
        return {k:torch.from_numpy(np.array(v,copy=True)) for k,v in (
            ('patches',patches[i]),('mask',mask[i]),('states',self.states[i]),('target',self.targets[i]))}


def move_batch(batch,device,augment=False):
    result={key:value.to(device) for key,value in batch.items()}
    result['patches']=result['patches'].float()/127.5-1
    if augment:
        images=result['patches'];b=images.shape[0]
        contrast=torch.empty((b,1,1,1),device=device).uniform_(.85,1.15)
        brightness=torch.empty((b,1,1,1),device=device).uniform_(-.12,.12)
        images=(images*contrast+brightness).clamp(-1,1);shape=images.shape;images=images.view(-1,1,64,64)
        theta=torch.zeros((images.shape[0],2,3),device=device)
        angle=torch.empty(images.shape[0],device=device).uniform_(-.09,.09)
        scale=torch.empty(images.shape[0],device=device).uniform_(.95,1.05)
        theta[:,0,0]=theta[:,1,1]=angle.cos()*scale
        theta[:,0,1]=-angle.sin()*scale;theta[:,1,0]=angle.sin()*scale;theta[:,:,2].uniform_(-.025,.025)
        grid=F.affine_grid(theta,images.shape,align_corners=False)
        result['patches']=F.grid_sample(images,grid,align_corners=False,padding_mode='border').view(shape)
    return result


def reader_loss(output,batch):
    ce=F.cross_entropy(output['logits'].transpose(1,2),batch['target'],reduction='none')
    weights=.2+.8*batch['mask'].float();loss=(ce*weights).sum()/weights.sum()
    valid=(batch['states']>=0)&batch['mask']
    if valid.any():
        loss+=.3*F.cross_entropy(output['states'][valid],batch['states'][valid])
        loss+=.3*F.cross_entropy(output['single_logits'][valid],batch['target'][valid])
    return loss


@torch.inference_mode()
def validate(model,loader,device):
    model.eval();n=integer=full=0
    for original in loader:
        batch=move_batch(original,device)
        with torch.autocast(device_type=device.type,enabled=device.type=='cuda'):
            output=model(batch['patches'],batch['mask'])
        prediction=output['logits'].argmax(-1);target=batch['target'];n+=target.shape[0]
        integer+=int((prediction[:,:6]==target[:,:6]).all(1).sum());full+=int((prediction==target).all(1).sum())
    return {'count':n,'integer_exact':integer/n,'full_exact':full/n}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=DATA)
    parser.add_argument('--initial',type=Path,default=ROOT/'models/reader.pt')
    parser.add_argument('--project',type=Path,default=ROOT/'runs')
    parser.add_argument('--name',required=True)
    parser.add_argument('--epochs',type=int,default=25);parser.add_argument('--batch',type=int,default=24)
    parser.add_argument('--lr',type=float,default=.0001);parser.add_argument('--patience',type=int,default=8)
    parser.add_argument('--mix-gt',type=float,default=.3);parser.add_argument('--device',default='cuda:0')
    args=parser.parse_args();manifest=load_manifest(args.data);fingerprint=sha256(args.data/'manifest.json')
    cache_meta=read_json(args.data/'cache/pred_metadata.json')
    if cache_meta['manifest_sha256']!=fingerprint or cache_meta['test_processed'] or set(cache_meta['indices'])!=set(manifest['splits']['train']+manifest['splits']['val']):
        raise ValueError('Prediction cache must contain exactly train/validation, never test')
    if not 0<=args.mix_gt<=1 or args.epochs<1:raise ValueError('Invalid training arguments')
    device=torch.device(args.device);torch.set_num_threads(4)
    random.seed(42);np.random.seed(42);torch.manual_seed(42)
    if device.type=='cuda':
        if not torch.cuda.is_available():raise RuntimeError('Use .venv-wmeter for CUDA training')
        torch.cuda.manual_seed_all(42);torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True
        torch.backends.cuda.enable_flash_sdp(False);torch.backends.cuda.enable_mem_efficient_sdp(False)
    torch.use_deterministic_algorithms(True,warn_only=True)
    model,checkpoint=load_reader(args.initial,device)
    if checkpoint['manifest_sha256']!=fingerprint:raise ValueError('Initial reader uses a different split')
    output=args.project/args.name
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);shutil.copytree(ROOT/'watermeter',output/'source',ignore=shutil.ignore_patterns('__pycache__'))
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=.0005)
    schedule=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,args.epochs,eta_min=args.lr*.05)
    scaler=torch.amp.GradScaler('cuda',enabled=device.type=='cuda')
    train=DataLoader(PackedDataset(args.data,manifest['splits']['train'],'pred',args.mix_gt),batch_size=args.batch,
                     shuffle=True,num_workers=0,generator=torch.Generator().manual_seed(42))
    val=DataLoader(PackedDataset(args.data,manifest['splits']['val']),batch_size=args.batch,shuffle=False,num_workers=0)
    write_json(output/'provenance.json',{'manifest_sha256':fingerprint,'initial_sha256':sha256(args.initial),
        'cache_detector_sha256':cache_meta['detector_sha256'],'cache_confidence':cache_meta['confidence'],
        'arguments':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},'torch':str(torch.__version__),
        'scope':'full_pointer','test_used':False,'selection':'Validation integer exact, then full exact'})
    best=(-1.,-1.);idle=0
    for epoch in range(1,args.epochs+1):
        started=time.perf_counter();model.train();total=0.;count=0
        for original in train:
            batch=move_batch(original,device,True);optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type,enabled=device.type=='cuda'):
                loss=reader_loss(model(batch['patches'],batch['mask']),batch)
            scaler.scale(loss).backward();scaler.unscale_(optimizer);nn.utils.clip_grad_norm_(model.parameters(),5)
            scaler.step(optimizer);scaler.update();total+=float(loss.detach())*batch['target'].shape[0];count+=batch['target'].shape[0]
        metrics=validate(model,val,device);schedule.step()
        row={'epoch':epoch,'loss':total/count,'val':metrics,'seconds':time.perf_counter()-started}
        with (output/'history.jsonl').open('a',encoding='utf-8') as handle:handle.write(json.dumps(row)+'\n')
        saved={'model':model.state_dict(),'config':model.config,'epoch':epoch,'val':metrics,
               'manifest_sha256':fingerprint,'input_prefix':'pred','test_used':False}
        score=(metrics['integer_exact'],metrics['full_exact'])
        if score>best:
            best=score;idle=0;torch.save(saved,output/'best.tmp');(output/'best.tmp').replace(output/'best.pt')
        else:idle+=1
        torch.save(saved,output/'last.tmp');(output/'last.tmp').replace(output/'last.pt')
        print(json.dumps(row),flush=True)
        if idle>=args.patience:break
