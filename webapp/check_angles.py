"""Rotation smoke test on one existing image, NOT angle accuracy benchmark."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image,ImageOps
import numpy as np,cv2
from webapp.adapter import ReaderAdapter

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('image',type=Path);args=p.parse_args()
    with Image.open(args.image) as im:rgb=np.asarray(ImageOps.exif_transpose(im).convert('RGB'))
    h,w=rgb.shape[:2];rgb=cv2.resize(rgb,(round(w*1000/max(h,w)),round(h*1000/max(h,w))),interpolation=cv2.INTER_AREA)
    h,w=rgb.shape[:2];side=int(np.ceil(np.hypot(h,w)))+4
    canvas=np.full((side,side,3),220,np.uint8);y=(side-h)//2;x=(side-w)//2;canvas[y:y+h,x:x+w]=rgb
    reader=ReaderAdapter();rows=[]
    for angle in [0,30,60,90,180,270]:
        matrix=cv2.getRotationMatrix2D(((side-1)/2,(side-1)/2),angle,1)
        image=cv2.warpAffine(canvas,matrix,(side,side),borderValue=(220,220,220))
        try:
            result=reader.predict(cv2.cvtColor(image,cv2.COLOR_RGB2BGR));result.pop('annotated');rows.append({'degrees_ccw':angle,**result})
        except (ValueError,RuntimeError) as exc:rows.append({'degrees_ccw':angle,'error':str(exc)})
    out=ROOT/'webapp/evidence';out.mkdir(exist_ok=True)
    report={'image':str(args.image),'warning':'One synthetically rotated, uniformly padded image only. No verified true reading. Neither real viewpoint/perspective coverage nor accuracy evidence.','results':rows}
    (out/'angle_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
