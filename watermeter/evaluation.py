"""Reading metrics and transparent internal evaluation."""
import argparse
from pathlib import Path
import numpy as np

from .runtime import ROOT
from .data import DATA,load_manifest,read_json,write_json,read_image,image_path,sha256,pack_regions
from .pipeline import MeterRecognizer,load_selection


def summarize(rows,key):
    n=len(rows)
    if not n:raise ValueError('No evaluation samples')
    integer=full=covered=0;errors=[]
    for row in rows:
        p=row.get(key)
        if p is None:continue
        covered+=1;target=row['target_scaled']
        integer+=int(p['scaled_value']//10000==target//10000);full+=int(p['scaled_value']==target)
        errors.append(abs(p['scaled_value']-target)/10000)
    return {'images':n,'returned_readings':covered,'coverage':covered/n,'integer_exact':integer/n,
            'integer_correct':integer,'full_exact':full/n,'full_correct':full,
            'mae_m3_on_returned':float(np.mean(errors)) if errors else None,
            'median_absolute_error_m3_on_returned':float(np.median(errors)) if errors else None,
            'failed_images_count_as_incorrect':True}


def evaluate(directory,selection,split,output,device='cuda:0',oracle=False,acknowledge_retest=False,reader=None,detector=None):
    directory,output=Path(directory),Path(output);manifest=load_manifest(directory)
    config=load_selection(selection);fingerprint=sha256(directory/'manifest.json')
    if config['manifest_sha256']!=fingerprint:raise ValueError('Selected model and evaluation split differ')
    if split=='test' and not acknowledge_retest:raise ValueError('Use --acknowledge-retest: this is an already inspected internal holdout')
    for key,path in (('reader',reader),('detector',detector)):
        if path is not None:
            if split!='val':raise ValueError('Candidate models must be compared on validation only')
            config[key]=Path(path);config[key+'_sha256']=sha256(path)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    recognizer=MeterRecognizer(config['detector'],config['reader'],device,config['confidence']);rows=[]
    if recognizer.checkpoint['manifest_sha256']!=fingerprint:raise ValueError('Reader split binding changed')
    for count,index in enumerate(manifest['splits'][split],1):
        record=manifest['records'][index];image=read_image(image_path(record,directory))
        row={'name':record['name'],'index':index,'kind':'single_regions'}
        try:
            result=recognizer.predict(image)
            row.update(automatic_context=result['context'],automatic_independent=result['independent'],detected_regions=result['regions'])
        except (ValueError,RuntimeError) as exc:row['error']=str(exc)
        if oracle:
            result=recognizer.read_packed(pack_regions(image,record['regions']))
            row.update(oracle_context=result['context'],oracle_independent=result['independent'])
        row.update(target_scaled=int(''.join(str(x) for x in record['target'])),target_reading=record['reading'])
        rows.append(row)
        if count%100==0:print(f'{split}: {count}/{len(manifest["splits"][split])}',flush=True)
    keys=['automatic_context','automatic_independent']+(['oracle_context','oracle_independent'] if oracle else [])
    report={'scope':'full_pointer','split':split,'data_directory':str(directory.resolve()),'manifest_sha256':fingerprint,
            'detector':str(config['detector'].resolve()),'reader':str(config['reader'].resolve()),
            'detector_sha256':config['detector_sha256'],'reader_sha256':config['reader_sha256'],'confidence':config['confidence'],
            'metrics':{key:summarize(rows,key) for key in keys},'test_previously_evaluated':split=='test',
            'limitations':['Dataset-provided reading labels; not newly human verified.',
                           'Internal grouped holdout; physical meter independence unverified.',
                           'This holdout has previously been evaluated and inspected; not a fresh blind test.']}
    write_json(output/'predictions.json',rows);write_json(output/'report.json',report)
    print(report['metrics']['automatic_context'],flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=DATA);parser.add_argument('--selection',type=Path,default=ROOT/'models/selection.json')
    parser.add_argument('--split',choices=['val','test'],default='val');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',default='cuda:0');parser.add_argument('--oracle',action='store_true')
    parser.add_argument('--reader',type=Path,help='Candidate checkpoint: validation only, selected weights stay unchanged')
    parser.add_argument('--detector',type=Path,help='Candidate detector: validation only')
    parser.add_argument('--acknowledge-retest',action='store_true')
    args=parser.parse_args();evaluate(args.data,args.selection,args.split,args.output,args.device,args.oracle,args.acknowledge_retest,args.reader,args.detector)
