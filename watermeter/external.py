"""Evaluation against newly collected photographs and human-verified readings."""
import argparse
from pathlib import Path
import numpy as np

from .data import DATA,read_json,write_json,sha256,read_image,value_digits
from .pipeline import MeterRecognizer,load_selection
from .evaluation import summarize
from .runtime import ROOT


def validate_collection(path):
    path=Path(path);collection=read_json(path);samples=collection.get('samples',[])
    if not samples:raise ValueError('No independent photographs yet; fill the collection template first')
    if not (collection.get('unseen_physical_meters_confirmed') is True and collection.get('frozen_before_evaluation') is True
            and collection.get('confirmed_by')):
        raise ValueError('A collector must confirm unseen physical meters and freeze the collection before evaluation')
    known={r['sha256'] for r in read_json(DATA/'provenance/parent_manifest.json')['records']}
    seen=set();prepared=[]
    for sample in samples:
        reviewers=sample.get('verified_by',[])
        if not isinstance(reviewers,list) or not sample.get('meter_id') or len({str(x).strip() for x in reviewers if str(x).strip()})<2:
            raise ValueError('Every photo requires a physical meter ID and two human reviewers')
        if not isinstance(sample.get('reading'),str):raise ValueError('Use decimal strings for human readings')
        target=int(''.join(map(str,value_digits(sample['reading']))))
        image=Path(sample['image']);image=image if image.is_absolute() else path.parent/image
        fingerprint=sha256(image)
        if fingerprint in known or fingerprint in seen:raise ValueError('Duplicate/previously exposed image: '+str(image))
        seen.add(fingerprint);prepared.append((sample,image,fingerprint,target))
    return collection,prepared


def evaluate_external(path,output,device='cpu',selection=ROOT/'models/selection.json'):
    collection,prepared=validate_collection(path);config=load_selection(selection);output=Path(output)
    if output.exists():raise FileExistsError(output)
    recognizer=MeterRecognizer(config['detector'],config['reader'],device,config['confidence']);rows=[]
    for sample,image,fingerprint,target in prepared:
        row={'name':image.name,'meter_id':sample['meter_id'],'sha256':fingerprint}
        try:row['automatic_context']=recognizer.predict(read_image(image))['context']
        except (ValueError,RuntimeError) as exc:row['error']=str(exc)
        row.update(target_scaled=target,target_reading=sample['reading']);rows.append(row)
    meters=sorted({r['meter_id'] for r in rows})
    by_meter={m:summarize([r for r in rows if r['meter_id']==m],'automatic_context') for m in meters}
    report={'scope':'full_pointer','evaluation':'human_verified_external','collection_sha256':sha256(path),
            'detector_sha256':config['detector_sha256'],'reader_sha256':config['reader_sha256'],
            'metrics':summarize(rows,'automatic_context'),'by_meter':by_meter,
            'meter_macro_integer_exact':float(np.mean([v['integer_exact'] for v in by_meter.values()])),
            'meter_macro_full_exact':float(np.mean([v['full_exact'] for v in by_meter.values()])),
            'limitations':['Physical meter independence is a collector declaration, not automatically proven.',
                           'Byte-identical exposed photos are rejected; re-encoded/near-duplicate images require manual checking.']}
    output.mkdir(parents=True);write_json(output/'input_collection.json',collection)
    write_json(output/'predictions.json',rows);write_json(output/'report.json',report)
    print(report['metrics']);return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('collection',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',default='cpu');parser.add_argument('--selection',type=Path,default=ROOT/'models/selection.json')
    args=parser.parse_args();evaluate_external(args.collection,args.output,args.device,args.selection)
