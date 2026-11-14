"""Write a new selection file using validation integer/full accuracy only."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from watermeter.runtime import ROOT
from watermeter.data import read_json,write_json,sha256,load_manifest
from watermeter.pipeline import load_selection


def select(reports,output,previous=ROOT/'models/selection.json'):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    baseline=load_selection(previous);count=len(load_manifest()['splits']['val']);candidates=[]
    for path in reports:
        report=read_json(path);metrics=report['metrics']['automatic_context']
        if report['split']!='val' or report.get('scope')!='full_pointer' or metrics['images']!=count:
            raise ValueError('Model selection requires full-pointer validation reports only')
        if report['manifest_sha256']!=baseline['manifest_sha256']:raise ValueError('Validation split changed')
        candidates.append((metrics['integer_exact'],metrics['full_exact'],Path(path),report))
    _,_,path,report=max(candidates,key=lambda row:row[:2]);config=dict(baseline)
    for key in ('reader','detector'):
        weight=baseline[key] if report[key+'_sha256']==baseline[key+'_sha256'] else Path(report[key])
        if sha256(weight)!=report[key+'_sha256']:raise ValueError('Candidate weights changed')
        try:relative=weight.resolve().relative_to(ROOT).as_posix()
        except ValueError:relative=str(weight.resolve())
        config[key]=relative;config[key+'_sha256']=report[key+'_sha256']
    config.update(confidence=report['confidence'],decoder='context',validation_metrics=report['metrics']['automatic_context'],
                  selected_validation_report=str(path),candidate_reports=[str(p) for p in reports],test_used=False)
    write_json(output,config);print('Selected validation report:',path);return config


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',type=Path,nargs='+',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--previous',type=Path,default=ROOT/'models/selection.json')
    args=parser.parse_args();select(args.reports,args.output,args.previous)
