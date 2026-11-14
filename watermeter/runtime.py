import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure():
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
    for key, folder in (('YOLO_CONFIG_DIR','ultralytics'),('MPLCONFIGDIR','matplotlib')):
        path=Path(os.environ.setdefault(key,str(ROOT/'.runtime'/folder)))
        path.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('YOLO_OFFLINE','true')
    os.environ.setdefault('YOLO_AUTOINSTALL','false')
