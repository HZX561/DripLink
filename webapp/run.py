"""Run the local dashboard; explicit --host 0.0.0.0 enables device ingestion."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'webapp/vendor'))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=5001)
    parser.add_argument('--data-dir',type=Path)
    args=parser.parse_args()
    if args.port==5000:parser.error('Port5000 reserved for original project; use5001')
    from webapp.app import create_app
    app=create_app({'INSTANCE_DIR':str(args.data_dir.resolve())} if args.data_dir else None)
    try:
        from waitress import serve
    except ImportError:
        parser.exit(1,'Install optional web dependencies: python -m pip install -r webapp/requirements.txt\n')
    print('DripLink dashboard: http://127.0.0.1:'+str(args.port),flush=True)
    print('Admin UI is loopback-only. Device upload needs Bearer token from webapp/instance/device_token.txt or DRIPLINK_DEVICE_TOKEN. Never expose this service directly to the public internet.',flush=True)
    serve(app,host=args.host,port=args.port,threads=4,max_request_body_size=10*1024*1024,channel_timeout=180)

if __name__=='__main__':main()
