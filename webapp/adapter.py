"""Web-only model adapter. Does not alter the upstream model or training."""
import json
import threading
from pathlib import Path
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DETECT_CONF=.15
# Retry zooms used only when the first pass leaves a hole in the multiplier sequence.
RETRY_SCALES=(1.6,2.2)

class ModelUnavailable(RuntimeError):pass

def has_gap(regions):
    slots=sorted({int(r['cls']) for r in regions})
    return bool(slots) and len(slots)!=(slots[-1]-slots[0]+1)

# Thresholds calibrated on the released 320x240 pointer dataset (n=60):
# sharpness p1=178, mean p1=125, std p1=54, clipped-white p99=0.
BLUR_BELOW=90.0
DARK_BELOW=60.0
OVEREXPOSED_CLIP=0.10
WASHED_ABOVE=225.0
LOW_CONTRAST_BELOW=25.0
MIN_SIDE=200

def _roi(image,regions):
    height,width=image.shape[:2]
    if not regions:return image
    points=np.concatenate([np.asarray(r['quad'],np.float32).reshape(4,2) for r in regions])
    x0,y0=points.min(0);x1,y1=points.max(0)
    pad=.08*max(width,height)
    left=int(max(0,x0-pad));top=int(max(0,y0-pad))
    right=int(min(width,x1+pad));bottom=int(min(height,y1+pad))
    if right-left<32 or bottom-top<32:return image
    return image[top:bottom,left:right]

def assess_image(image,regions):
    """Photo-quality warnings. These describe the picture, never the model's reading.

    Sharpness is measured on the whole frame because that is how the reference
    dataset was calibrated; a tight dial crop keeps high local contrast even when
    the photo is soft. Exposure/contrast use the detected dial area so a dark
    background does not get reported as an underexposed meter.
    """
    height,width=image.shape[:2];issues=[]
    frame=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    long_side=max(frame.shape)
    sample=cv2.resize(frame,(max(1,round(frame.shape[1]*512/long_side)),max(1,round(frame.shape[0]*512/long_side))),
                      interpolation=cv2.INTER_AREA) if long_side>512 else frame
    sharpness=float(cv2.Laplacian(sample,cv2.CV_64F).var())
    subject=cv2.cvtColor(_roi(image,regions),cv2.COLOR_BGR2GRAY)
    mean=float(subject.mean());contrast=float(subject.std())
    clipped=float((subject>=250).mean())
    if sharpness<BLUR_BELOW:
        issues.append({'code':'blur','message':f'照片偏模糊（清晰度{sharpness:.0f}，参考正常≈180以上）；指针位置可能判错，请对焦后重拍。'})
    if mean<DARK_BELOW:
        issues.append({'code':'dark','message':f'画面偏暗（平均亮度{mean:.0f}）；容易看不清指针，请补光后重拍。'})
    if clipped>OVEREXPOSED_CLIP or mean>WASHED_ABOVE:
        issues.append({'code':'overexposed','message':f'存在强反光或过曝区域（高光占比{clipped*100:.0f}%）；表盘可能被反光盖住，请改变拍摄角度避开反光。'})
    if contrast<LOW_CONTRAST_BELOW:
        issues.append({'code':'low_contrast','message':f'画面对比度过低（{contrast:.0f}）；表盘与指针不易区分，请靠近并避开阴影。'})
    if min(width,height)<MIN_SIDE:
        issues.append({'code':'low_resolution','message':f'图片分辨率偏低（{width}×{height}）；小表盘会糊成一片，建议用更高分辨率拍摄。'})
    return issues

def merge_regions(primary,secondary):
    """One detection per multiplier; keep the more confident box."""
    best={}
    for region in list(primary)+list(secondary):
        slot=int(region['cls']);current=best.get(slot)
        if current is None or float(region.get('confidence',0))>float(current.get('confidence',0)):best[slot]=region
    return [best[k] for k in sorted(best)]

def crop_around(regions,shape,factor):
    height,width=shape[:2]
    points=np.concatenate([np.asarray(r['quad'],np.float32).reshape(4,2) for r in regions])
    x0,y0=points.min(0);x1,y1=points.max(0)
    cx,cy=(x0+x1)/2,(y0+y1)/2
    want_w=max((x1-x0)*factor,60);want_h=max((y1-y0)*factor,60)
    left=int(max(0,cx-want_w/2));top=int(max(0,cy-want_h/2))
    right=int(min(width,cx+want_w/2));bottom=int(min(height,cy+want_h/2))
    if right-left<40 or bottom-top<40:return None
    return left,top,right,bottom

def assess_regions(regions,context,independent,recovered=False):
    """Describe detection completeness. A gap means the model missed dials, not that the photo is defective."""
    slots=sorted({int(r['cls']) for r in regions})
    gaps=[s for s in range(slots[0],slots[-1]+1) if s not in slots] if slots else []
    integer=[s for s in slots if s<6]
    disagreements=sum(a!=b for a,b in zip(context,independent))
    if gaps:
        names='、'.join(f'10^{5-s}' for s in gaps)
        return {'quality':'incomplete','missing_powers':gaps,
                'warnings':[f'模型只检出 {len(slots)} 个表盘，漏掉了 {names}；漏掉的位会被当成 0，读数可能整体错位，本次结果不可采信。']}
    if len(integer)<3 or disagreements>=3:
        return {'quality':'ambiguous','missing_powers':gaps,
                'warnings':['表盘数量偏少或两条解码路径差异过大，本次读数不可靠，请人工核对后再决定是否采用。']}
    if len(slots)>=9:
        return {'quality':'ambiguous','missing_powers':gaps,
                'warnings':[f'检出 {len(slots)} 个倍率表盘，多于常见水表数量，可能有误检混入；请对照标注图确认每个框都落在真实表盘上。']}
    if recovered:
        return {'quality':'ok','missing_powers':gaps,
                'warnings':['整图直接检测有漏检，已自动放大表盘区域重新识别，本次采用重检结果。']}
    return {'quality':'ok','missing_powers':gaps,'warnings':[]}

class ReaderAdapter:
    def __init__(self,device='cpu'):
        self.device=device;self._reader=None;self._lock=threading.Lock();self._error=None

    def availability(self):
        missing=[name for name in ('detector.pt','reader.pt','selection.json') if not (ROOT/'models'/name).is_file()]
        return {'model_ready':not missing and self._error is None,'model_error':self._error or ('缺少模型资产：'+', '.join(missing) if missing else None),'model_name':'DripLink YOLO11 Pose + CNN + Transformer','device':self.device}

    def _load(self):
        if self._reader is None:
            try:
                from watermeter.pipeline import MeterRecognizer
                self._reader,self._selection=MeterRecognizer.selected(device=self.device)
            except Exception as exc:
                self._error='模型加载失败：'+type(exc).__name__
                raise ModelUnavailable(self._error) from exc
        return self._reader

    def _detect(self,reader,image):
        from watermeter.pipeline import regions_from_result
        result=reader.detector.predict(image,device=str(reader.device),imgsz=512,conf=DETECT_CONF,iou=.5,
                                         agnostic_nms=True,verbose=False,save=False,half=reader.device.type=='cuda',rect=False)[0]
        return regions_from_result(result)

    def detect_with_retry(self,reader,image):
        """Detect, and if the multiplier sequence has holes, retry on a zoomed crop."""
        regions=self._detect(reader,image)
        recovered=False
        if has_gap(regions):
            best=regions
            for factor in RETRY_SCALES:
                box=crop_around(regions,image.shape,factor)
                if box is None:break
                left,top,right,bottom=box
                zoomed=self._detect(reader,image[top:bottom,left:right])
                for region in zoomed:
                    region['quad']=(np.asarray(region['quad'],np.float32)+np.array([left,top],np.float32)).tolist()
                merged=merge_regions(best,zoomed)
                if len(merged)>len(best):
                    best=merged;recovered=True
                if not has_gap(best):break
            regions=best
        return regions,recovered

    def predict(self,image):
        with self._lock:
            reader=self._load()
            from watermeter.data import pack_regions
            regions,recovered=self.detect_with_retry(reader,image)
            if not any(int(r['cls'])<6 for r in regions):
                issues=assess_image(image,regions)
                hint='；'.join(issue['message'] for issue in issues[:2])
                raise ValueError('没有识别到整数位表盘，无法给出读数。'+(hint or '请把表盘拍正、拍全、拍清楚后重试。'))
            result=reader.read_packed(pack_regions(image,regions))
            context=result['context']['reading'];independent=result['independent']['reading']
            warnings=[]
            check=assess_regions(regions,result['context']['digits'],result['independent']['digits'],recovered)
            warnings.extend(check['warnings'])
            image_issues=assess_image(image,regions)
            warnings.extend(issue['message'] for issue in image_issues)
            if context!=independent:warnings.append('联合识读与独立位识读不同，请核对原图和倍率。')
            if any(r.get('geometry_fallback') for r in regions):warnings.append('部分角点无效，已回退矩形裁剪；倾斜图片结果可能不可靠。')
            slots={int(r['cls']) for r in regions}
            warnings.append('本次检出 '+str(len(slots))+' 个倍率表盘；未检出的倍率无法自动区分“表上没有”和“模型漏看”，请人工核对。')
            if any(float(r.get('confidence',0))<0.5 for r in regions):warnings.append('存在较低检测置信度的表盘；检测置信度不等于读数正确概率。')
            warnings.append('仅支持单块全指针式水表；字轮混合、多表、强反光和大透视不在可靠使用范围。')
            canvas=image.copy();height,width=canvas.shape[:2];thick=max(1,round(max(height,width)/800))
            for region in regions:
                quad=np.asarray(region['quad'],np.int32);cv2.polylines(canvas,[quad],True,(60,195,65),thick)
                point=tuple(quad.min(axis=0).astype(int));cv2.putText(canvas,'10^'+str(5-int(region['cls'])),point,cv2.FONT_HERSHEY_SIMPLEX,max(.4,max(height,width)/2200),(0,70,230),thick)
            return {'context_reading':context,'independent_reading':independent,
                    'predicted_reading':result[self._selection.get('decoder','context')]['reading'],
                    'detected_regions':len(regions),'quality':check['quality'],'missing_powers':check['missing_powers'],
                    'recovered':recovered,'image_flags':[i['code'] for i in image_issues],'warnings':warnings,'annotated':canvas}
