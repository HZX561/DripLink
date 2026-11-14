"""Run a Python experiment with an exact UTF-8 log and propagated exit status."""
from pathlib import Path
import os
import subprocess
import sys

if __name__ == '__main__':
    log_path=Path(sys.argv[1])
    log_path.parent.mkdir(parents=True,exist_ok=True)
    print('Experiment log:',log_path.resolve(),flush=True)
    environment=os.environ.copy()
    environment.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
    with log_path.open('xb') as log:
        result=subprocess.run([sys.executable,'-u',*sys.argv[2:]],stdout=log,stderr=subprocess.STDOUT,env=environment)
    print('Experiment exit code:',result.returncode,flush=True)
    raise SystemExit(result.returncode)
