"""Predict a full-pointer meter photograph using the selected local weights."""
import argparse
from pathlib import Path
import json
from watermeter.runtime import ROOT
import cv2
import numpy as np
from watermeter.data import read_image
from watermeter.pipeline import MeterRecognizer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path);parser.add_argument('--device',default='cpu')
    parser.add_argument('--selection',type=Path,default=ROOT/'models/selection.json');parser.add_argument('--output',type=Path)
    parser.add_argument('--details',action='store_true');args=parser.parse_args()
    recognizer,selection=MeterRecognizer.selected(args.selection,args.device);image=read_image(args.image)
    result=recognizer.predict(image);prediction=result[selection.get('decoder','context')]
    if args.output:
        for region in result['regions']:cv2.polylines(image,[np.asarray(region['quad'],np.int32)],True,(50,200,50),1)
        cv2.putText(image,'Reading: '+prediction['reading']+' m3',(5,22),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,0,255),2)
        args.output.parent.mkdir(parents=True,exist_ok=True);ok,buffer=cv2.imencode(args.output.suffix or '.jpg',image)
        if not ok:raise ValueError('Cannot encode output image')
        args.output.write_bytes(buffer.tobytes())
    print(json.dumps(result if args.details else {'reading_m3':prediction['reading'],'integer_m3':prediction['integer_m3'],
                     'detected_regions':len(result['regions'])},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
