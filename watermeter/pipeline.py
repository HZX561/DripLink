"""Pixels to numeric readings. No filenames or ground-truth geometry enter inference."""
import time
from pathlib import Path
import cv2
import numpy as np
import torch

from .runtime import ROOT
from .data import NAMES,pack_regions,read_json,sha256
from .model import load_reader


def load_detector(path):
    from ultralytics import YOLO
    model=YOLO(str(path))
    if [model.names[i] for i in range(len(model.names))]!=NAMES:raise ValueError('Detector class order mismatch')
    return model


def regions_from_result(result):
    if result.boxes is None or result.keypoints is None:return []
    boxes=result.boxes.xyxy.detach().cpu().numpy()
    classes=result.boxes.cls.detach().cpu().numpy().astype(int)
    confidence=result.boxes.conf.detach().cpu().numpy()
    corners=result.keypoints.xy.detach().cpu().numpy();selected={}
    for i in np.argsort(-confidence):
        cls=int(classes[i])
        if not 0<=cls<10 or cls in selected:continue
        x0,y0,x1,y1=boxes[i];quad=corners[i].astype(np.float32)
        area=abs(cv2.contourArea(quad));box_area=max(float((x1-x0)*(y1-y0)),1)
        sides=np.linalg.norm(quad-np.roll(quad,1,axis=0),axis=1)
        fallback=(not np.isfinite(quad).all() or not cv2.isContourConvex(quad)
                  or sides.min()<2 or area<.15*box_area or area>3*box_area)
        if fallback:quad=np.array([[x1,y0],[x1,y1],[x0,y1],[x0,y0]],np.float32)
        selected[cls]={'cls':cls,'quad':quad.tolist(),'confidence':float(confidence[i]),'geometry_fallback':bool(fallback)}
    return list(selected.values())


def load_selection(path=ROOT/'models/selection.json'):
    config=read_json(path)
    for key in ('detector','reader'):
        p=Path(config[key]);p=p if p.is_absolute() else ROOT/p
        if sha256(p)!=config[key+'_sha256']:raise ValueError('Selected weights changed: '+key)
        config[key]=p
    return config


class MeterRecognizer:
    def __init__(self,detector,reader,device='cpu',confidence=.15):
        self.device=torch.device(device);torch.set_num_threads(4)
        self.detector=load_detector(detector);self.reader,self.checkpoint=load_reader(reader,self.device)
        self.confidence=confidence

    @classmethod
    def selected(cls,path=ROOT/'models/selection.json',device='cpu'):
        config=load_selection(path)
        return cls(config['detector'],config['reader'],device,config['confidence']),config

    @torch.inference_mode()
    def read_packed(self,packed):
        patches,mask,_=packed
        if not mask.any():raise ValueError('No pointer regions detected')
        p=torch.from_numpy(np.ascontiguousarray(patches)).to(self.device).float()[None]/127.5-1
        m=torch.from_numpy(np.ascontiguousarray(mask)).to(self.device)[None]
        with torch.autocast(device_type=self.device.type,enabled=self.device.type=='cuda'):
            output=self.reader(p,m)
        def decode(logits):
            digits=logits.argmax(-1)[0].cpu().tolist();value=int(''.join(str(d) for d in digits))
            return {'digits':digits,'scaled_value':value,'integer_m3':value//10000,
                    'reading':f'{value//10000}.{value%10000:04d}'}
        return {'context':decode(output['logits']),'independent':decode(output['base_logits'])}

    def predict(self,image):
        started=time.perf_counter()
        result=self.detector.predict(image,device=str(self.device),imgsz=512,conf=self.confidence,iou=.5,
                                     agnostic_nms=True,verbose=False,save=False,half=self.device.type=='cuda',rect=False)[0]
        regions=regions_from_result(result)
        if not any(r['cls']<6 for r in regions):raise ValueError('No integer pointer dials detected; full-pointer meters only')
        prediction=self.read_packed(pack_regions(image,regions))
        prediction.update(regions=regions,seconds=time.perf_counter()-started)
        return prediction
