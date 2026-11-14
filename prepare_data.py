"""Verify/recover original pointer images and optionally rebuild training crops."""
import argparse
from watermeter.data import load_manifest,recover_images,prepare_cache
from watermeter.runtime import ROOT

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rebuild-cache',action='store_true');parser.add_argument('--device',default='cuda:0')
    args=parser.parse_args();manifest=load_manifest();recovered=recover_images()
    prepare_cache(detector=ROOT/'models/detector.pt',force=args.rebuild_cache,device=args.device)
    print({'scope':manifest['scope'],'images':len(manifest['records']),'recovered_images':recovered,
           'splits':{key:len(value) for key,value in manifest['splits'].items()}})
