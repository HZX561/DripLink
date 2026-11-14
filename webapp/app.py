"""Local-first web UI plus authenticated Raspberry Pi image ingestion."""
import hashlib,hmac,ipaddress,json,os,re,secrets,sqlite3,warnings
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import cv2
import numpy as np
from PIL import Image,ImageOps,UnidentifiedImageError
from flask import Flask,request,jsonify,session,render_template,send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash,check_password_hash
from .adapter import ReaderAdapter,ModelUnavailable
from .analytics import analyze

BASE=Path(__file__).resolve().parent
ID_PATTERN=re.compile(r'^[A-Za-z0-9_-]{1,64}$')

def utc_time(text=None):
    if not text:return datetime.now(timezone.utc).isoformat(timespec='microseconds')
    if not isinstance(text,str) or len(text)>64:raise ValueError('拍摄时间格式错误')
    try:dt=datetime.fromisoformat(text.replace('Z','+00:00'))
    except (ValueError,TypeError):raise ValueError('拍摄时间必须为带时区的ISO时间')
    if dt.tzinfo is None:raise ValueError('拍摄时间必须带时区')
    dt=dt.astimezone(timezone.utc)
    if dt>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('拍摄时间不能在未来，请同步设备时钟')
    return dt.isoformat(timespec='microseconds')

def reading_text(value):
    if not isinstance(value,(str,int,float)) or isinstance(value,bool) or len(str(value))>32:raise ValueError('读数格式错误')
    try:d=Decimal(str(value))
    except InvalidOperation:raise ValueError('读数格式错误')
    if not d.is_finite() or d<0 or d>=1000000 or d*10000!=(d*10000).to_integral_value():raise ValueError('读数需在0至999999.9999之间，最多4位小数')
    return format(d,'.4f')

def decode_upload(file):
    formats={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.webp':'WEBP'}
    ext=Path(file.filename or '').suffix.lower()
    if ext not in formats:raise ValueError('只支持JPG、PNG和WebP静态图片')
    raw=file.read()
    if not raw:raise ValueError('图片为空')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as im:
                if im.format!=formats[ext] or getattr(im,'n_frames',1)!=1:raise ValueError('图片内容与格式不匹配或含动画')
                if min(im.size)<16 or im.width*im.height>24000000:raise ValueError('图片尺寸需至少16像素且不超过2400万像素')
                im.verify()
            with Image.open(BytesIO(raw)) as im:rgb=np.asarray(ImageOps.exif_transpose(im).convert('RGB'))
    except (OSError,SyntaxError,UnidentifiedImageError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:raise ValueError('图片损坏或无法解码') from exc
    return cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),hashlib.sha256(raw).hexdigest()

def create_app(config=None,recognizer=None):
    app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'))
    app.config.update(MAX_CONTENT_LENGTH=10*1024*1024,INSTANCE_DIR=str(BASE/'instance'),SECRET_KEY=None,DEVICE_TOKEN=os.environ.get('DRIPLINK_DEVICE_TOKEN'),DAILY_LIMIT_M3=30.0,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict')
    if config:app.config.update(config)
    instance=Path(app.config['INSTANCE_DIR']).resolve();instance.mkdir(parents=True,exist_ok=True);media=instance/'media';media.mkdir(exist_ok=True)
    def stored_secret(name):
        path=instance/name
        if not path.exists():
            try:
                with path.open('x',encoding='utf-8') as f:f.write(secrets.token_urlsafe(32))
                try:path.chmod(0o600)
                except OSError:pass
            except FileExistsError:pass
        return path.read_text(encoding='utf-8').strip()
    if not app.config['SECRET_KEY']:app.config['SECRET_KEY']=stored_secret('session_key.txt')
    if not app.config['DEVICE_TOKEN']:app.config['DEVICE_TOKEN']=stored_secret('device_token.txt')
    if len(app.config['DEVICE_TOKEN'])<24:raise ValueError('设备Token至少24字符')
    db_path=instance/'records.sqlite3'
    @contextmanager
    def connect():
        conn=sqlite3.connect(db_path,timeout=20);conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT,meter_id TEXT NOT NULL,captured_at TEXT NOT NULL,received_at TEXT NOT NULL,source TEXT NOT NULL,predicted_reading TEXT NOT NULL,context_reading TEXT NOT NULL,independent_reading TEXT NOT NULL,confirmed_reading TEXT,status TEXT NOT NULL,warnings TEXT NOT NULL,detected_regions INTEGER NOT NULL,original_name TEXT NOT NULL,result_name TEXT NOT NULL,upload_id TEXT UNIQUE,content_hash TEXT NOT NULL)')
        columns={c[1] for c in conn.execute('PRAGMA table_info(records)')}
        if 'quality' not in columns:conn.execute("ALTER TABLE records ADD COLUMN quality TEXT NOT NULL DEFAULT 'ok'")
        if 'image_flags' not in columns:conn.execute("ALTER TABLE records ADD COLUMN image_flags TEXT NOT NULL DEFAULT '[]'")
        conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT \'user\',display_name TEXT,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS meters (id TEXT PRIMARY KEY,display_name TEXT,timezone TEXT NOT NULL DEFAULT \'Asia/Shanghai\',expected_interval_seconds INTEGER NOT NULL DEFAULT 3600,offline_grace_multiplier REAL NOT NULL DEFAULT 2.0,daily_limit_m3 REAL,created_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS meter_memberships (meter_id TEXT NOT NULL,user_id INTEGER NOT NULL,role TEXT NOT NULL DEFAULT \'viewer\',UNIQUE(meter_id,user_id))')
        conn.execute('CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY,meter_id TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,display_name TEXT,active INTEGER NOT NULL DEFAULT 1,last_seen_at TEXT,last_upload_at TEXT,created_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,meter_id TEXT NOT NULL,type TEXT NOT NULL,level TEXT NOT NULL,dedupe_key TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,resolved_at TEXT,payload TEXT,UNIQUE(meter_id,dedupe_key))')
    reader=recognizer or ReaderAdapter(device='cpu');app.extensions['meter_reader']=reader
    def record_json(row):
        if row is None:return None
        item=dict(row);item['warnings']=json.loads(item['warnings']);item['image_flags']=json.loads(item.get('image_flags') or '[]');item['original_url']='/media/'+item.pop('original_name');item['result_url']='/media/'+item.pop('result_name');item.pop('content_hash',None);return item
    def lookup(record_id):
        with connect() as conn:return record_json(conn.execute('SELECT * FROM records WHERE id=?',(record_id,)).fetchone())
    def meter_id(value):
        if not isinstance(value,str) or not ID_PATTERN.fullmatch(value):raise ValueError('水表编号限1至64位英文字母、数字、下划线或短横线')
        return value
    ALLOW_REMOTE=os.environ.get('DRIPLINK_ALLOW_REMOTE','').lower() in ('1','true','yes','on')
    def is_loopback():
        try:return ipaddress.ip_address(request.remote_addr or '').is_loopback
        except ValueError:return False
    def current_user():
        uid=session.get('user_id')
        if uid is not None:
            with connect() as conn:row=conn.execute('SELECT * FROM users WHERE id=? AND active=1',(uid,)).fetchone()
            if row:return dict(row)
            session.clear()
        if app.testing or is_loopback():return {'id':None,'username':None,'role':'admin','display_name':'本机管理员','legacy':True}
        return None
    def require_auth():
        user=current_user()
        if user is None:raise PermissionError('请先登录')
        return user
    def require_admin():
        user=require_auth()
        if user.get('role')!='admin':raise PermissionError('需要管理员权限')
        return user
    def meter_row(key):
        with connect() as conn:return conn.execute('SELECT * FROM meters WHERE id=?',(key,)).fetchone()
    def all_meter_rows():
        with connect() as conn:rows=conn.execute('SELECT * FROM meters ORDER BY id').fetchall()
        ids={r['id'] for r in rows}
        with connect() as conn:rec=[r[0] for r in conn.execute('SELECT DISTINCT meter_id FROM records ORDER BY meter_id')]
        for mid in rec:
            if mid not in ids:
                with connect() as conn:
                    conn.execute('INSERT OR IGNORE INTO meters (id,timezone,expected_interval_seconds,offline_grace_multiplier,created_at) VALUES (?,?,?,?,?)',(mid,'Asia/Shanghai',3600,2.0,utc_time()))
                    row=conn.execute('SELECT * FROM meters WHERE id=?',(mid,)).fetchone()
                rows.append(row);ids.add(mid)
        return rows
    def visible_meter_ids(user=None):
        user=user or current_user()
        if user.get('role')=='admin':
            with connect() as conn:return [r[0] for r in conn.execute('SELECT id FROM meters ORDER BY id')]
        if user.get('id') is None:return []
        with connect() as conn:return [r[0] for r in conn.execute('SELECT meter_id FROM meter_memberships WHERE user_id=? ORDER BY meter_id',(user['id'],))]
    def can_access_meter(key,user=None,write=False):
        user=user or current_user()
        if user.get('role')=='admin':return True
        with connect() as conn:
            row=conn.execute('SELECT role FROM meter_memberships WHERE meter_id=? AND user_id=?',(key,user.get('id'))).fetchone()
        if row is None:return False
        if write and row['role']=='viewer':return False
        return True
    def meter_json(row,user=None):
        if row is None:return None
        user=user or current_user()
        item=dict(row)
        item['meter_id']=row['id']
        with connect() as conn:
            devices=conn.execute('SELECT id,display_name,active,last_seen_at,last_upload_at,created_at FROM devices WHERE meter_id=? ORDER BY id',(row['id'],)).fetchall()
            item['devices']=[dict(d) for d in devices]
        item['online_status']=meter_online_status(row)
        if user.get('role')=='admin' or user.get('legacy'):
            with connect() as conn:
                members=conn.execute('SELECT u.id,u.username,u.display_name,m.role FROM meter_memberships m JOIN users u ON u.id=m.user_id WHERE m.meter_id=? ORDER BY u.username',(row['id'],)).fetchall()
                item['members']=[dict(m) for m in members]
        return item
    def meter_online_status(row):
        interval=row['expected_interval_seconds'] if row['expected_interval_seconds'] else None
        with connect() as conn:devices=conn.execute('SELECT last_seen_at,last_upload_at,active FROM devices WHERE meter_id=? AND active=1',(row['id'],)).fetchall()
        if not devices or interval is None:return 'unknown'
        now=datetime.now(timezone.utc)
        latest=None
        for d in devices:
            for field in ('last_seen_at','last_upload_at'):
                v=d[field]
                if v:
                    try:ts=datetime.fromisoformat(v)
                    except (ValueError,TypeError):continue
                    if latest is None or ts>latest:latest=ts
        if latest is None:return 'unknown'
        grace=interval*(row['offline_grace_multiplier'] if row['offline_grace_multiplier'] else 2.0)
        return 'offline' if (now-latest).total_seconds()>grace else 'online'
    def upsert_alert(meter,type_,level,message,dedupe_key,payload=None):
        now=utc_time()
        with connect() as conn:
            existing=conn.execute('SELECT id,resolved_at FROM alerts WHERE meter_id=? AND dedupe_key=?',(meter,dedupe_key)).fetchone()
            if existing:
                conn.execute("UPDATE alerts SET last_seen_at=?,level=?,payload=?,resolved_at=NULL WHERE id=?",(now,level,json.dumps(payload,ensure_ascii=False) if payload is not None else None,existing['id']))
                return False
            conn.execute('INSERT INTO alerts (meter_id,type,level,dedupe_key,first_seen_at,last_seen_at,payload) VALUES (?,?,?,?,?,?,?)',(meter,type_,level,dedupe_key,now,now,json.dumps(payload,ensure_ascii=False) if payload is not None else None))
            return True
    def meter_alerts(key,user=None):
        user=user or current_user()
        with connect() as conn:
            if user.get('role')=='admin':rows=conn.execute('SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY last_seen_at DESC,id DESC LIMIT 200').fetchall()
            else:
                rows=conn.execute('SELECT * FROM alerts WHERE resolved_at IS NULL AND meter_id IN (SELECT meter_id FROM meter_memberships WHERE user_id=?) ORDER BY last_seen_at DESC,id DESC LIMIT 200',(user.get('id'),)).fetchall()
        out=[]
        for r in rows:
            item=dict(r);item['payload']=json.loads(item['payload']) if item.get('payload') else None
            out.append(item)
        return out
    def compute_and_sync_alerts(key):
        row=meter_row(key)
        with connect() as conn:items=[dict(r) for r in conn.execute('SELECT * FROM records WHERE meter_id=? ORDER BY captured_at,id',(key,))]
        daily=float(row['daily_limit_m3']) if row and row['daily_limit_m3'] else float(app.config['DAILY_LIMIT_M3'])
        result=analyze(items,daily_limit=daily)
        active={}
        offline=meter_online_status(row) if row else 'unknown'
        if offline=='offline':
            active['device_offline']=('error','设备离线','最近一次采集/心跳已超过预期间隔，请检查设备供电、网络与定时任务。')
        for a in result['alerts']:
            title=a.get('title');message=a.get('message','')
            if title=='读数回退':active['reading_rollback']=('warning',title,message)
            elif title=='区间用水偏高':active['usage_high']=('warning',title,message)
            elif title=='记录超过24小时未更新':active['data_stale']=('info',title,message)
        now=utc_time()
        with connect() as conn:
            for type_,(level,title,message) in active.items():
                upsert_alert(key,type_,level,message,type_)
            active_types=list(active.keys())
            if active_types:
                marks=','.join('?'*len(active_types))
                conn.execute(f'UPDATE alerts SET resolved_at=? WHERE meter_id=? AND resolved_at IS NULL AND type NOT IN ({marks})',[now,key]+active_types)
            else:
                conn.execute('UPDATE alerts SET resolved_at=? WHERE meter_id=? AND resolved_at IS NULL',[now,key])
        return result,offline
    def bearer_token():
        header=request.headers.get('Authorization','')
        return header[7:] if header.startswith('Bearer ') else None
    @app.before_request
    def security():
        path=request.path
        if path in ('/api/device/upload','/api/device/heartbeat'):
            raw=bearer_token()
            if not raw:return jsonify(error='设备鉴权失败'),401
            if path=='/api/device/heartbeat':
                with connect() as conn:device=conn.execute('SELECT * FROM devices WHERE token_hash=?',(hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
                if not device or not device['active']:return jsonify(error='设备鉴权失败'),401
                request.environ['drip_link_device']=dict(device);return None
            device_id=request.form.get('device_id')
            if device_id:
                if not ID_PATTERN.fullmatch(device_id):return jsonify(error='设备编号格式错误'),401
                with connect() as conn:device=conn.execute('SELECT * FROM devices WHERE id=?',(device_id,)).fetchone()
                if not device or not device['active'] or not hmac.compare_digest(hashlib.sha256(raw.encode()).hexdigest(),device['token_hash']):return jsonify(error='设备鉴权失败'),401
                request.environ['drip_link_device']=dict(device)
            elif not hmac.compare_digest(raw,app.config['DEVICE_TOKEN']):return jsonify(error='设备鉴权失败'),401
            return None
        if not app.testing and not ALLOW_REMOTE:
            if not is_loopback():return jsonify(error='管理界面仅允许本机访问；远程管理请使用SSH隧道'),403
            if request.host.split(':')[0].lower() not in ('127.0.0.1','localhost'):return jsonify(error='管理页面请使用localhost或127.0.0.1访问'),403
        if not app.testing and ALLOW_REMOTE and not is_loopback():
            public=path in ('/','/user','/api/status','/api/auth/login','/api/auth/logout','/api/me')
            if not public and session.get('user_id') is None:return jsonify(error='请先登录'),401
        if request.method in ('POST','PUT','PATCH','DELETE'):
            expected=session.get('csrf_token','');provided=request.headers.get('X-CSRF-Token','')
            if not expected or not hmac.compare_digest(expected,provided):return jsonify(error='页面会话失效，请刷新后重试'),403
    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY';response.headers['Referrer-Policy']='same-origin';response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response
    @app.errorhandler(HTTPException)
    def http_error(exc):return jsonify(error='上传内容超过10 MiB' if exc.code==413 else exc.description),exc.code
    @app.errorhandler(ValueError)
    def value_error(exc):return jsonify(error=str(exc)),400
    @app.errorhandler(PermissionError)
    def permission_error(exc):return jsonify(error=str(exc)),401 if str(exc)=='请先登录' else 403
    @app.get('/')
    def index():return render_template('index.html',mode='admin')
    @app.get('/user')
    def user_index():return render_template('index.html',mode='user')
    @app.get('/media/<filename>')
    def get_media(filename):
        if not re.fullmatch(r'[a-f0-9]{32}_(original|result)\.jpg',filename):return jsonify(error='图片不存在'),404
        return send_from_directory(media,filename)
    @app.get('/api/status')
    def status():
        session.setdefault('csrf_token',secrets.token_urlsafe(24))
        user=current_user()
        with connect() as conn:all_meters=[r[0] for r in conn.execute('SELECT DISTINCT meter_id FROM records ORDER BY meter_id')]
        with connect() as conn:registered=[r[0] for r in conn.execute('SELECT id FROM meters ORDER BY id')]
        visible=visible_meter_ids(user)
        meters=sorted(set(all_meters)|set(registered)|set(visible))
        if user.get('role')!='admin':meters=visible
        me={'role':user.get('role'),'username':user.get('username'),'display_name':user.get('display_name'),'legacy':bool(user.get('legacy'))} if user else None
        return jsonify(**reader.availability(),device_upload_auth_required=True,advice_mode='rules',csrf_token=session['csrf_token'],meters=meters,me=me,allow_remote=ALLOW_REMOTE)
    @app.get('/api/device-info')
    def device_info():return jsonify(endpoint='/api/device/upload',required_fields=['file','meter_id','captured_at','upload_id'],interval_example_seconds=3600,note='Bearer鉴权；采集结果为待审核；通过服务器局域网IP上传，不是树莓派自身127.0.0.1')
    @app.get('/api/records')
    def records():
        user=require_auth()
        key=meter_id(request.args.get('meter_id','demo'))
        if not can_access_meter(key,user):raise PermissionError('无权查看该水表')
        try:offset=int(request.args.get('offset','0'));limit=int(request.args.get('limit','100'))
        except ValueError:raise ValueError('分页参数应为整数')
        if offset<0 or not 1<=limit<=500:raise ValueError('分页范围无效')
        with connect() as conn:
            total=conn.execute('SELECT COUNT(*) FROM records WHERE meter_id=?',(key,)).fetchone()[0]
            items=conn.execute('SELECT * FROM records WHERE meter_id=? ORDER BY captured_at DESC,id DESC LIMIT ? OFFSET ?',(key,limit,offset)).fetchall()
        return jsonify(records=[record_json(r) for r in items],total=total,next_offset=offset+len(items) if offset+len(items)<total else None)
    @app.get('/api/analytics')
    def analytics():
        user=require_auth()
        key=meter_id(request.args.get('meter_id','demo'))
        if not can_access_meter(key,user):raise PermissionError('无权查看该水表')
        result,offline=compute_and_sync_alerts(key)
        result['meter_online_status']=offline
        if offline=='offline':result['alerts'].append({'level':'error','title':'设备离线','message':'最近一次采集/心跳已超过预期间隔，请检查设备供电、网络与定时任务。'})
        return jsonify(result)
    def ingest(device=False):
        bound_device=request.environ.get('drip_link_device') if device else None
        key=meter_id(bound_device['meter_id'] if bound_device else request.form.get('meter_id','demo'))
        if not device:
            require_admin()
        if device and not request.form.get('captured_at'):raise ValueError('设备必须提交captured_at')
        captured=utc_time(request.form.get('captured_at') if device else None)
        upload_id=request.form.get('upload_id') if device else None
        if device and (not upload_id or not ID_PATTERN.fullmatch(upload_id)):raise ValueError('upload_id限64位字母数字下划线短横线')
        if 'file' not in request.files:raise ValueError('未收到图片file')
        image,digest=decode_upload(request.files['file'])
        if device:
            if bound_device:
                with connect() as conn:conn.execute('UPDATE devices SET last_seen_at=?,last_upload_at=? WHERE id=?',(utc_time(),captured,bound_device['id']))
            with connect() as conn:previous=conn.execute('SELECT * FROM records WHERE upload_id=?',(upload_id,)).fetchone()
            if previous:
                if previous['content_hash']!=digest or previous['meter_id']!=key or previous['captured_at']!=captured:return jsonify(error='upload_id与原请求内容冲突'),409
                return jsonify(record=record_json(previous),record_id=previous['id'],status=previous['status'],duplicate=True)
        try:prediction=reader.predict(image)
        except ModelUnavailable as exc:return jsonify(error=str(exc)),503
        except (ValueError,RuntimeError) as exc:return jsonify(error='未能可靠识读：'+str(exc)),422
        names=[]
        try:
            predicted=reading_text(prediction['predicted_reading']);context=reading_text(prediction['context_reading']);independent=reading_text(prediction['independent_reading'])
            stem=uuid4().hex;original_name=stem+'_original.jpg';result_name=stem+'_result.jpg'
            for name,pixels in [(original_name,image),(result_name,prediction['annotated'])]:
                ok,encoded=cv2.imencode('.jpg',pixels)
                if not ok:raise OSError('图片保存失败')
                target=media/name;names.append(target);target.write_bytes(encoded.tobytes())
            state='confirmed' if device else 'sample'
            confirmed_value=predicted if device else None
            alerts=list(prediction['warnings'])
            alerts.append('设备读数已自动计入用水统计，请按需人工核查并删除异常记录。' if device else '手动上传按测试样本处理，用于检验识别模型，不计入用水统计。')
            quality=prediction.get('quality','ok');image_flags=json.dumps(prediction.get('image_flags',[]))
            with connect() as conn:
                cur=conn.execute('INSERT INTO records (meter_id,captured_at,received_at,source,predicted_reading,context_reading,independent_reading,confirmed_reading,status,warnings,detected_regions,original_name,result_name,upload_id,content_hash,quality,image_flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,captured,utc_time(),'device' if device else 'manual',predicted,context,independent,confirmed_value,state,json.dumps(alerts,ensure_ascii=False),prediction['detected_regions'],original_name,result_name,upload_id,digest,quality,image_flags));new_id=cur.lastrowid
            return jsonify(record=lookup(new_id),record_id=new_id,status=state),201
        except sqlite3.IntegrityError:
            for p in names:p.unlink(missing_ok=True)
            if device:
                with connect() as conn:previous=conn.execute('SELECT * FROM records WHERE upload_id=?',(upload_id,)).fetchone()
                if previous and previous['content_hash']==digest and previous['meter_id']==key and previous['captured_at']==captured:return jsonify(record=record_json(previous),record_id=previous['id'],status=previous['status'],duplicate=True)
            return jsonify(error='重复请求冲突'),409
        except Exception:
            for p in names:p.unlink(missing_ok=True)
            app.logger.exception('Could not persist recognition result')
            return jsonify(error='识读结果保存失败，未登记读数'),500
    @app.post('/api/predict')
    def predict():return ingest()
    @app.post('/api/device/upload')
    def device_upload():return ingest(True)
    @app.post('/api/records/<int:record_id>/delete')
    def delete(record_id):
        user=require_auth()
        with connect() as conn:
            row=conn.execute('SELECT * FROM records WHERE id=?',(record_id,)).fetchone()
            if row is None:return jsonify(error='记录不存在'),404
            if not can_access_meter(row['meter_id'],user,write=True):raise PermissionError('无权删除该水表的记录')
            conn.execute('DELETE FROM records WHERE id=?',(record_id,))
        for name in ('original_name','result_name'):(media/row[name]).unlink(missing_ok=True)
        return jsonify(ok=True)
    @app.post('/api/auth/login')
    def login():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('需要JSON对象')
        username=(data.get('username') or '').strip();password=data.get('password') or ''
        if not username or not password:raise ValueError('请输入用户名与密码')
        with connect() as conn:row=conn.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
        if not row or not check_password_hash(row['password_hash'],password):
            return jsonify(error='用户名或密码错误'),401
        session.clear();session['user_id']=row['id'];session.setdefault('csrf_token',secrets.token_urlsafe(24))
        return jsonify(me={'id':row['id'],'username':row['username'],'role':row['role'],'display_name':row['display_name'],'legacy':False},csrf_token=session['csrf_token'])
    @app.post('/api/auth/logout')
    def logout():
        session.clear();return jsonify(ok=True)
    @app.get('/api/me')
    def me():
        user=current_user()
        if user is None:return jsonify(me=None)
        return jsonify(me={'id':user.get('id'),'username':user.get('username'),'role':user.get('role'),'display_name':user.get('display_name'),'legacy':bool(user.get('legacy'))})
    @app.get('/api/meters')
    def meters_list():
        user=require_auth()
        if user.get('role')=='admin':rows=all_meter_rows()
        else:
            with connect() as conn:rows=conn.execute('SELECT * FROM meters WHERE id IN (SELECT meter_id FROM meter_memberships WHERE user_id=?) ORDER BY id',(user['id'],)).fetchall()
        return jsonify(meters=[meter_json(r,user) for r in rows])
    @app.post('/api/meters')
    def meters_create():
        user=require_admin()
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('需要JSON对象')
        key=meter_id(data.get('id') or data.get('meter_id') or '')
        now=utc_time()
        def pos_int(v,default,label):
            if v in (None,''):return default
            try:n=int(v)
            except (ValueError,TypeError):raise ValueError(label+'应为整数')
            if n<=0:raise ValueError(label+'应大于0')
            return n
        def pos_float(v,default,label):
            if v in (None,''):return default
            try:f=float(v)
            except (ValueError,TypeError):raise ValueError(label+'应为数字')
            if f<=0:raise ValueError(label+'应大于0')
            return f
        interval=pos_int(data.get('expected_interval_seconds'),3600,'采样间隔');grace=pos_float(data.get('offline_grace_multiplier'),2.0,'离线宽限倍数')
        daily=None
        if data.get('daily_limit_m3') not in (None,''):daily=pos_float(data.get('daily_limit_m3'),None,'日用水阈值')
        with connect() as conn:
            conn.execute('INSERT INTO meters (id,display_name,timezone,expected_interval_seconds,offline_grace_multiplier,daily_limit_m3,created_at) VALUES (?,?,?,?,?,?,?)',(key,(data.get('display_name') or '').strip() or None,data.get('timezone') or 'Asia/Shanghai',interval,grace,daily,now))
        return jsonify(meter=meter_json(meter_row(key),user)),201
    @app.patch('/api/meters/<key>')
    def meters_update(key):
        user=require_auth();key=meter_id(key)
        if not can_access_meter(key,user,write=True):raise PermissionError('无权修改该水表')
        row=meter_row(key)
        if row is None:return jsonify(error='水表不存在'),404
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('需要JSON对象')
        fields={}
        for name,label in (('display_name','名称'),('timezone','时区')):
            if name in data:fields[name]=(str(data[name]).strip() or None) if name=='display_name' else (str(data[name]).strip() or 'Asia/Shanghai')
        for name,label,cast in (('expected_interval_seconds','采样间隔',int),('offline_grace_multiplier','离线宽限倍数',float),('daily_limit_m3','日用水阈值',float)):
            if name in data and data[name] not in (None,''):
                try:v=cast(data[name])
                except (ValueError,TypeError):raise ValueError(label+'应为数字')
                if v<=0:raise ValueError(label+'应大于0')
                fields[name]=v
            elif name=='daily_limit_m3' and data.get(name) is None:fields[name]=None
        if fields:
            sets=','.join(f'{k}=?' for k in fields);vals=list(fields.values())+[key]
            with connect() as conn:conn.execute(f'UPDATE meters SET {sets} WHERE id=?',vals)
        return jsonify(meter=meter_json(meter_row(key),user))
    @app.post('/api/meters/<key>/members')
    def meters_members(key):
        user=require_auth();key=meter_id(key)
        if user.get('role')!='admin' and not can_access_meter(key,user,write=True):raise PermissionError('无权管理该水表成员')
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('需要JSON对象')
        username=(data.get('username') or '').strip();role=data.get('role') or 'viewer'
        if role not in ('admin','operator','viewer'):raise ValueError('成员角色无效')
        with connect() as conn:u=conn.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
        if u is None:return jsonify(error='用户不存在'),404
        with connect() as conn:
            conn.execute('INSERT INTO meter_memberships (meter_id,user_id,role) VALUES (?,?,?) ON CONFLICT(meter_id,user_id) DO UPDATE SET role=excluded.role',(key,u['id'],role))
        return jsonify(meter=meter_json(meter_row(key),user))
    @app.get('/api/users')
    def users_list():
        require_admin()
        with connect() as conn:rows=conn.execute('SELECT id,username,display_name,role,active,created_at FROM users ORDER BY username').fetchall()
        return jsonify(users=[dict(r) for r in rows])
    @app.get('/api/meters/<key>/devices')
    def meters_devices(key):
        user=require_auth();key=meter_id(key)
        if not can_access_meter(key,user):raise PermissionError('无权查看该水表')
        with connect() as conn:rows=conn.execute('SELECT id,meter_id,display_name,active,last_seen_at,last_upload_at,created_at FROM devices WHERE meter_id=? ORDER BY id',(key,)).fetchall()
        return jsonify(devices=[dict(r) for r in rows])
    @app.post('/api/devices')
    def devices_create():
        user=require_auth()
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('需要JSON对象')
        key=meter_id(data.get('meter_id') or '')
        if not can_access_meter(key,user,write=True):raise PermissionError('无权在该水表下创建设备')
        device_id=data.get('id') or ('dev_'+secrets.token_hex(6))
        if not ID_PATTERN.fullmatch(device_id):raise ValueError('设备编号限1至64位字母数字下划线短横线')
        token=secrets.token_urlsafe(32);token_hash=hashlib.sha256(token.encode()).hexdigest()
        with connect() as conn:
            conn.execute('INSERT INTO devices (id,meter_id,token_hash,display_name,active,created_at) VALUES (?,?,?,?,1,?)',(device_id,key,token_hash,(data.get('display_name') or '').strip() or None,utc_time()))
        return jsonify(device={'id':device_id,'meter_id':key,'display_name':(data.get('display_name') or '').strip() or None,'active':1},token=token,token_note='此凭证仅显示这一次，请立即保存；服务端只保存其摘要。'),201
    @app.post('/api/device/heartbeat')
    def device_heartbeat():
        device=request.environ.get('drip_link_device')
        if not device:return jsonify(error='设备鉴权失败'),401
        with connect() as conn:conn.execute('UPDATE devices SET last_seen_at=? WHERE id=?',(utc_time(),device['id']))
        with connect() as conn:meter=conn.execute('SELECT * FROM meters WHERE id=?',(device['meter_id'],)).fetchone()
        return jsonify(ok=True,meter_id=device['meter_id'],expected_interval_seconds=(meter['expected_interval_seconds'] if meter else 3600))
    @app.get('/api/dashboard')
    def dashboard():
        user=require_auth()
        if user.get('role')=='admin':
            rows=all_meter_rows()
        else:
            with connect() as conn:rows=conn.execute('SELECT * FROM meters WHERE id IN (SELECT meter_id FROM meter_memberships WHERE user_id=?) ORDER BY id',(user['id'],)).fetchall()
        meters=[meter_json(r,user) for r in rows]
        for m in meters:compute_and_sync_alerts(m['meter_id'])
        alerts=meter_alerts(None,user)
        offline=[m['meter_id'] for m in meters if m['online_status']=='offline']
        online=[m['meter_id'] for m in meters if m['online_status']=='online']
        return jsonify(meters=meters,alerts=alerts,offline_count=len(offline),online_count=len(online),total=len(meters))
    return app
