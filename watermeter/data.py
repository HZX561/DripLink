"""Frozen pointer-only dataset, geometry and rebuildable training caches."""
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np

from .runtime import ROOT

DATA=ROOT/'data'
POWERS=tuple(range(5,-5,-1))
NAMES=[f'power_{p}' for p in POWERS]+['wheel']  # Existing detector's immutable class order.


def sha256(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def load_manifest(directory=DATA):
    directory=Path(directory)
    manifest=read_json(directory/'manifest.json')
    expected=(directory/'manifest.sha256').read_text().strip()
    if sha256(directory/'manifest.json')!=expected:raise ValueError('Frozen dataset manifest changed')
    if manifest.get('scope')!='full_pointer':raise ValueError('Expected a full-pointer dataset')
    for record in manifest['records']:
        if Path(record['name']).name!=record['name'] or any(r['cls']==10 for r in record['regions']):
            raise ValueError('Invalid pointer-only record')
    return manifest


def image_path(record,directory=DATA):
    local=Path(directory)/'images'/record['name']
    if local.is_file():return local
    # Independent datasets can explicitly name their own image paths.
    explicit=Path(record.get('image',''))
    return explicit if explicit.is_absolute() else Path(directory)/explicit


def read_image(path):
    image=cv2.imdecode(np.frombuffer(Path(path).read_bytes(),dtype=np.uint8),cv2.IMREAD_COLOR)
    if image is None:raise ValueError(f'Cannot decode {path}')
    return image


def value_digits(reading):
    scaled=Decimal(str(reading))*10000
    if scaled!=scaled.to_integral_value() or not 0<=scaled<10**10:raise ValueError('Unsupported meter reading')
    return [int(char) for char in f'{int(scaled):010d}']


def rectify(image,quad):
    source=np.asarray(quad,np.float32).reshape(4,2)
    if not np.isfinite(source).all() or abs(cv2.contourArea(source))<2:raise ValueError('Degenerate pointer region')
    dest=np.array([[63,0],[63,63],[0,63],[0,0]],np.float32)
    crop=cv2.warpPerspective(image,cv2.getPerspectiveTransform(source,dest),(64,64),
                             flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=(220,220,220))
    return cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)


def pack_regions(image,regions):
    patches=np.zeros((10,64,64),np.uint8);mask=np.zeros(10,bool);states=np.full(10,-100,np.int64)
    for region in regions:
        slot=region['cls']
        if not 0<=slot<10:continue
        patches[slot]=rectify(image,region['quad']);mask[slot]=True;states[slot]=region.get('state',-100)
    return patches,mask,states


def recover_images(directory=DATA):
    directory=Path(directory);manifest=load_manifest(directory)
    missing=[r for r in manifest['records'] if not image_path(r,directory).is_file()]
    if not missing:return 0
    (directory/'images').mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(directory/'raw/WMeter5K_source.zip') as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read('WMeter5K.zip'))) as archive:
            for record in missing:
                raw=archive.read('WMeter5K/images/'+record['name'])
                if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Source image checksum mismatch')
                (directory/'images'/record['name']).write_bytes(raw)
    return len(missing)


def prepare_cache(directory=DATA,detector=None,force=False,device='cuda:0'):
    """Rebuild crops from original photos without changing the frozen split."""
    directory=Path(directory);manifest=load_manifest(directory);cache=directory/'cache'
    cache.mkdir(parents=True,exist_ok=True);recover_images(directory)
    n=len(manifest['records'])
    gt_files=('gt_patches.npy','gt_masks.npy','gt_states.npy','targets.npy')
    if force or not all((cache/name).is_file() for name in gt_files):
        patches=np.lib.format.open_memmap(cache/'gt_patches.npy',mode='w+',dtype=np.uint8,shape=(n,10,64,64))
        masks=np.zeros((n,10),bool);states=np.full((n,10),-100,np.int64)
        for i,record in enumerate(manifest['records']):
            patches[i],masks[i],states[i]=pack_regions(read_image(image_path(record,directory)),record['regions'])
        patches.flush();np.save(cache/'gt_masks.npy',masks);np.save(cache/'gt_states.npy',states)
        np.save(cache/'targets.npy',np.asarray([value_digits(r['reading']) for r in manifest['records']],np.int64))
    if detector is None:return
    from .pipeline import load_detector,regions_from_result
    indices=sorted(manifest['splits']['train']+manifest['splits']['val'])
    metadata={'manifest_sha256':sha256(directory/'manifest.json'),'detector_sha256':sha256(detector),
              'indices':indices,'confidence':.15,'imgsz':512,'test_processed':False}
    pred_files=('pred_patches.npy','pred_masks.npy','pred_metadata.json')
    if not force and all((cache/name).is_file() for name in pred_files):
        previous=read_json(cache/'pred_metadata.json')
        if any(previous.get(k)!=v for k,v in metadata.items()):raise ValueError('Prediction cache configuration changed')
        return
    model=load_detector(detector)
    patches=np.lib.format.open_memmap(cache/'pred_patches.npy',mode='w+',dtype=np.uint8,shape=(n,10,64,64))
    masks=np.zeros((n,10),bool)
    for start in range(0,len(indices),16):
        ids=indices[start:start+16]
        images=[read_image(image_path(manifest['records'][i],directory)) for i in ids]
        results=model.predict(images,device=device,imgsz=512,conf=.15,iou=.5,agnostic_nms=True,
                              verbose=False,save=False,half=device!='cpu',rect=False)
        for i,image,result in zip(ids,images,results):
            patches[i],masks[i],_=pack_regions(image,regions_from_result(result))
    patches.flush();np.save(cache/'pred_masks.npy',masks);write_json(cache/'pred_metadata.json',metadata)
