import io,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import numpy as np
from PIL import Image
from webapp.app import create_app,reading_text,utc_time
from webapp.analytics import analyze
from webapp.adapter import ModelUnavailable

class FakeReader:
    calls=0;error=None
    def availability(self):return {'model_ready':True,'model_error':None,'model_name':'test','device':'cpu'}
    def predict(self,image):
        self.calls+=1
        if self.error:raise self.error
        return {'predicted_reading':'123.4567','context_reading':'123.4567','independent_reading':'123.4568','detected_regions':8,'warnings':['test warning'],'annotated':image.copy()}

def photo():
    out=io.BytesIO();Image.new('RGB',(128,128),(230,230,230)).save(out,format='JPEG');return out.getvalue()

class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.reader=FakeReader();self.app=create_app({'TESTING':True,'INSTANCE_DIR':self.tmp.name,'DEVICE_TOKEN':'x'*32,'SECRET_KEY':'test-key'},self.reader)
        self.client=self.app.test_client();self.csrf=self.client.get('/api/status').json['csrf_token']
    def upload(self,sample='1',image=None):
        return self.client.post('/api/predict',data={'meter_id':'demo','sample':sample,'file':(io.BytesIO(image if image is not None else photo()),'meter.jpg')},headers={'X-CSRF-Token':self.csrf})
    def device(self,upload='upload_1',token=None,image=None,meter='demo',time='2026-01-01T00:00:00Z'):
        return self.client.post('/api/device/upload',data={'meter_id':meter,'captured_at':time,'upload_id':upload,'file':(io.BytesIO(image or photo()),'meter.jpg')},headers={'Authorization':'Bearer '+(token if token is not None else 'x'*32)})
    def test_manual_sample_and_media(self):
        response=self.upload();self.assertEqual(response.status_code,201)
        record=response.json['record'];self.assertEqual(record['status'],'sample');self.assertEqual(record['predicted_reading'],'123.4567')
        with self.client.get(record['result_url']) as media_response:self.assertEqual(media_response.status_code,200)
        self.assertEqual(self.client.get('/api/analytics?meter_id=demo').json['confirmed_count'],0)
    def test_record_pagination(self):
        for _ in range(3):self.upload()
        page=self.client.get('/api/records?limit=2').json
        self.assertEqual(len(page['records']),2);self.assertEqual(page['total'],3);self.assertEqual(page['next_offset'],2)
        last=self.client.get('/api/records?limit=2&offset=2').json
        self.assertEqual(len(last['records']),1);self.assertIsNone(last['next_offset'])
        self.assertEqual(self.client.get('/api/records?offset=-1').status_code,400)
        self.assertEqual(self.client.get('/api/records?limit=no').status_code,400)
    def test_csrf_required(self):
        self.assertEqual(self.client.post('/api/predict').status_code,403)
    def test_missing_bad_and_corrupt_file(self):
        r=self.client.post('/api/predict',data={'meter_id':'demo'},headers={'X-CSRF-Token':self.csrf});self.assertEqual(r.status_code,400)
        self.assertEqual(self.upload(image=b'not an image').status_code,400)
        self.assertEqual(self.reader.calls,0)
    def test_manual_is_sample_and_device_auto_confirmed(self):
        row=self.upload('0').json['record'];self.assertEqual(row['status'],'sample')
        self.assertEqual(self.client.get('/api/analytics').json['confirmed_count'],0)
        device_row=self.device().json['record'];self.assertEqual(device_row['status'],'confirmed');self.assertEqual(device_row['confirmed_reading'],'123.4567')
        self.assertEqual(self.client.get('/api/analytics').json['confirmed_count'],1)
        response=self.client.post('/api/records/'+str(device_row['id'])+'/delete',headers={'X-CSRF-Token':self.csrf});self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/analytics').json['confirmed_count'],0)
    def test_device_auth_idempotency_and_conflict(self):
        self.assertEqual(self.device(token='wrong').status_code,401)
        first=self.device();self.assertEqual(first.status_code,201);self.assertEqual(first.json['status'],'confirmed')
        second=self.device();self.assertEqual(second.status_code,200);self.assertTrue(second.json['duplicate']);self.assertEqual(self.reader.calls,1)
        self.assertEqual(self.device(meter='other').status_code,409)
    def test_device_requires_timezone_and_time(self):
        self.assertEqual(self.device(time='2026-01-01T00:00:00').status_code,400)
        self.assertEqual(self.device(time='').status_code,400)
    def test_device_capture_time_equivalent_timezones(self):
        self.assertEqual(self.device().status_code,201)
        self.assertEqual(self.device(time='2026-01-01T08:00:00+08:00').status_code,200)
    def test_flagged_device_readings_wait_for_confirmation(self):
        for index,(quality,flags,recovered) in enumerate([('incomplete',[],False),('ok',['blur'],False),('ok',[],True)]):
            result=FakeReader().predict(np.zeros((128,128,3),np.uint8))
            result.update(quality=quality,image_flags=flags,recovered=recovered)
            with patch.object(self.reader,'predict',return_value=result):response=self.device(upload=f'flagged_{index}')
            self.assertEqual(response.json['status'],'pending')
            self.assertIsNone(response.json['record']['confirmed_reading'])
        self.assertEqual(self.client.get('/api/analytics').json['confirmed_count'],0)
        row=response.json['record']
        confirmed=self.client.post(f'/api/records/{row["id"]}/confirm',json={'reading':'120.1234'},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(confirmed.status_code,200)
        self.assertEqual(confirmed.json['record']['confirmed_reading'],'120.1234')
        self.assertEqual(self.client.get('/api/analytics').json['confirmed_count'],1)
    def test_recognition_failure_does_not_store_fake_zero(self):
        self.reader.error=ValueError('bad geometry');self.assertEqual(self.upload().status_code,422)
        self.assertEqual(self.client.get('/api/records').json['records'],[])
        self.assertEqual(list((Path(self.tmp.name)/'media').iterdir()),[])
    def test_missing_model_503(self):
        self.reader.error=ModelUnavailable('missing');self.assertEqual(self.upload().status_code,503)
    def test_result_save_failure_cleans_files(self):
        with patch('webapp.app.cv2.imencode',return_value=(False,None)):
            self.assertEqual(self.upload().status_code,500)
        self.assertEqual(self.client.get('/api/records').json['records'],[]);self.assertFalse(list((Path(self.tmp.name)/'media').iterdir()))
    def test_media_path_traversal_and_invalid_id(self):
        self.assertEqual(self.client.get('/media/device_token.txt').status_code,404)
        self.assertEqual(self.client.get('/api/records?meter_id=../../foo').status_code,400)
    def test_no_token_exposed(self):
        self.assertNotIn('x'*32,self.client.get('/api/status').text);self.assertNotIn('x'*32,self.client.get('/api/device-info').text)
    def test_large_request_413(self):
        self.app.config['MAX_CONTENT_LENGTH']=1000
        self.assertEqual(self.upload(image=b'x'*2000).status_code,413)
    def test_admin_remote_forbidden_and_device_auth_works(self):
        self.app.testing=False
        self.assertEqual(self.client.get('/api/status',environ_overrides={'REMOTE_ADDR':'192.168.1.2'}).status_code,403)
        self.assertEqual(self.client.post('/api/device/upload',environ_overrides={'REMOTE_ADDR':'192.168.1.2'}).status_code,401)
    def test_decimal_validation(self):
        self.assertEqual(reading_text('001.001'),'1.0010')
        for x in ['nan','inf','-1','1000000','1.00001',None,True]:
            with self.assertRaises(ValueError):reading_text(x)

class AnalyticsTests(unittest.TestCase):
    def row(self,time,value,status='confirmed'):
        return {'status':status,'captured_at':time,'confirmed_reading':value}
    def test_no_fabricated_usage(self):
        result=analyze([]);self.assertIsNone(result['net_usage_m3']);self.assertIsNone(result['average_daily_m3']);self.assertEqual(result['points'],[])
    def test_only_confirmed_records_count(self):
        rows=[self.row('2026-01-01T00:00:00+00:00','1'),self.row('2026-01-02T00:00:00+00:00','3'),self.row('2026-01-03T00:00:00+00:00','999','sample')]
        result=analyze(rows);self.assertEqual(result['confirmed_count'],2);self.assertEqual(result['net_usage_m3'],2);self.assertEqual(result['average_daily_m3'],2)
    def test_decrease_excluded(self):
        rows=[self.row('2026-01-01T00:00:00+00:00','10'),self.row('2026-01-02T00:00:00+00:00','1')]
        result=analyze(rows);self.assertIsNone(result['net_usage_m3']);self.assertTrue(any(a['title']=='读数回退' for a in result['alerts']))
    def test_short_intervals_no_average_and_high_usage_not_leak_claim(self):
        rows=[self.row('2026-01-01T00:00:00+00:00','1'),self.row('2026-01-01T02:00:00+00:00','101')]
        result=analyze(rows);self.assertIsNone(result['average_daily_m3']);self.assertTrue(any(a['title']=='区间用水偏高' for a in result['alerts']));self.assertEqual(result['advice_mode'],'rules')

class QualityTests(unittest.TestCase):
    def regions(self,*slots):return [{'cls':s,'confidence':.8,'geometry_fallback':False} for s in slots]
    def test_gap_in_detected_dials_is_incomplete(self):
        from webapp.adapter import assess_regions
        result=assess_regions(self.regions(2,6,7,8),[0,0,0,0,0,0,2,6,7,0],[0,0,0,0,0,0,3,7,6,0])
        self.assertEqual(result['quality'],'incomplete');self.assertEqual(result['missing_powers'],[3,4,5])
        self.assertIn('10^2',result['warnings'][0])
    def test_contiguous_detection_is_ok(self):
        from webapp.adapter import assess_regions
        result=assess_regions(self.regions(*range(2,10)),[0,0,1,3,0,7,3,1,7,7],[0,0,1,3,0,7,3,2,7,7])
        self.assertEqual(result['quality'],'ok');self.assertEqual(result['missing_powers'],[])
    def test_too_few_integer_dials_flagged(self):
        from webapp.adapter import assess_regions
        result=assess_regions(self.regions(5,6,7,8),[0,0,0,0,0,1,1,1,1,1],[0,0,0,0,0,1,1,1,1,1])
        self.assertEqual(result['quality'],'ambiguous')

class RetryTests(unittest.TestCase):
    def regions(self,*pairs):return [{'cls':c,'confidence':f,'geometry_fallback':False,'quad':[[0,0],[10,0],[10,10],[0,10]]} for c,f in pairs]
    def test_gap_detection(self):
        from webapp.adapter import has_gap
        self.assertTrue(has_gap(self.regions((2,.8),(6,.8),(7,.8),(8,.8))))
        self.assertFalse(has_gap(self.regions(*[(s,.8) for s in range(2,10)])))
        self.assertFalse(has_gap([]))
    def test_merge_prefers_higher_confidence_and_unions_slots(self):
        from webapp.adapter import merge_regions
        merged=merge_regions(self.regions((2,.4),(6,.9)),self.regions((2,.8),(5,.7)))
        self.assertEqual([r['cls'] for r in merged],[2,5,6])
        self.assertAlmostEqual(merged[0]['confidence'],.8)
    def test_recovered_flag_warns_but_stays_ok(self):
        from webapp.adapter import assess_regions
        result=assess_regions(self.regions(*[(s,.8) for s in range(2,10)]),[0,0,1,3,0,7,3,1,7,7],[0,0,1,3,0,7,3,2,7,7],recovered=True)
        self.assertEqual(result['quality'],'ok');self.assertIn('重检',result['warnings'][0])
    def test_all_integer_dials_missing_triggers_high_resolution_retry(self):
        from webapp.adapter import ReaderAdapter
        adapter=ReaderAdapter();initial=self.regions(*[(s,.8) for s in range(6,10)])
        complete=self.regions(*[(s,.8) for s in range(2,10)])
        with patch.object(adapter,'_detect',side_effect=[initial,complete]) as detect:
            result,recovered=adapter.detect_with_retry(None,np.zeros((100,100,3),np.uint8))
        self.assertTrue(recovered);self.assertEqual(len(result),8)
        self.assertEqual(detect.call_args.kwargs,{'imgsz':1024})
    def test_no_detections_retry_without_empty_crop_failure(self):
        from webapp.adapter import ReaderAdapter
        adapter=ReaderAdapter()
        with patch.object(adapter,'_detect',return_value=[]) as detect:
            self.assertEqual(adapter.detect_with_retry(None,np.zeros((100,100,3),np.uint8)),([],False))
        self.assertEqual(detect.call_count,2)

class ImageQualityTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.rng = np.random.default_rng(0)
    def noise(self,size=400):
        return self.rng.integers(0,256,(size,size,3),dtype=self.np.uint8)
    def codes(self,image,regions=None):
        from webapp.adapter import assess_image
        return [i['code'] for i in assess_image(image,regions)]
    def test_sharp_image_has_no_flags(self):
        self.assertEqual(self.codes(self.noise()),[])
    def test_blurred_image_flagged(self):
        import cv2
        self.assertIn('blur',self.codes(cv2.GaussianBlur(self.noise(),(15,15),0)))
    def test_dark_image_flagged(self):
        self.assertIn('dark',self.codes(self.np.full((400,400,3),18,dtype=self.np.uint8)))
    def test_washed_out_image_flagged(self):
        image=self.noise();image[:250,:,:]=255
        self.assertIn('overexposed',self.codes(image))
    def test_low_resolution_flagged(self):
        self.assertIn('low_resolution',self.codes(self.noise(120)))
    def test_flat_image_flagged_low_contrast(self):
        self.assertIn('low_contrast',self.codes(self.np.full((400,400,3),128,dtype=self.np.uint8)))
    def test_interior_dial_not_flagged(self):
        region={'cls':2,'confidence':.8,'geometry_fallback':False,'quad':[[120,120],[220,120],[220,220],[120,220]]}
        self.assertEqual(self.codes(self.noise(),[region]),[])

class AccountsAndDevicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.reader=FakeReader();self.app=create_app({'TESTING':True,'INSTANCE_DIR':self.tmp.name,'DEVICE_TOKEN':'x'*32,'SECRET_KEY':'test-key','DAILY_LIMIT_M3':30.0},self.reader)
        self.client=self.app.test_client();self.db=Path(self.tmp.name)/'records.sqlite3'
        import sqlite3
        from werkzeug.security import generate_password_hash
        conn=sqlite3.connect(self.db)
        conn.execute("INSERT INTO users (username,password_hash,role,active,created_at) VALUES ('boss',?,'admin',1,'2026-01-01T00:00:00Z')",(generate_password_hash('secret123'),))
        conn.execute("INSERT INTO users (username,password_hash,role,active,created_at) VALUES ('bob',?,'user',1,'2026-01-01T00:00:00Z')",(generate_password_hash('secret123'),))
        conn.commit();conn.close()
    def csrf(self):return self.client.get('/api/status').json['csrf_token']
    def hdr(self,t=None):return {'X-CSRF-Token':t or self.csrf(),'Content-Type':'application/json'}
    def login(self,username):
        return self.client.post('/api/auth/login',headers=self.hdr(),json={'username':username,'password':'secret123'})
    def test_login_and_me(self):
        self.assertEqual(self.login('bob').status_code,200)
        self.assertEqual(self.client.get('/api/me').json['me']['role'],'user')
    def test_bad_login(self):
        self.assertEqual(self.client.post('/api/auth/login',headers=self.hdr(),json={'username':'boss','password':'wrong'}).status_code,401)
    def test_admin_creates_meter_and_user_is_isolated(self):
        self.login('boss');t=self.csrf()
        self.assertEqual(self.client.post('/api/meters',headers=self.hdr(t),json={'id':'m1','expected_interval_seconds':3600}).status_code,201)
        self.assertEqual(self.client.post('/api/meters/m1/members',headers=self.hdr(t),json={'username':'bob','role':'viewer'}).status_code,200)
        self.client.post('/api/auth/logout',headers=self.hdr(t))
        self.login('bob');t=self.csrf()
        meters=self.client.get('/api/meters',headers=self.hdr(t)).json['meters']
        self.assertEqual([m['meter_id'] for m in meters],['m1'])
        self.assertEqual(self.client.get('/api/records?meter_id=m1',headers=self.hdr(t)).status_code,200)
        self.assertEqual(self.client.get('/api/records?meter_id=other',headers=self.hdr(t)).status_code,403)
    def test_user_cannot_create_meter_or_device(self):
        self.login('bob');t=self.csrf()
        self.assertEqual(self.client.post('/api/meters',headers=self.hdr(t),json={'id':'m9'}).status_code,403)
        self.assertEqual(self.client.post('/api/devices',headers=self.hdr(t),json={'meter_id':'m9'}).status_code,403)
    def test_device_token_shown_once_and_hash_stored(self):
        self.login('boss');t=self.csrf()
        self.client.post('/api/meters',headers=self.hdr(t),json={'id':'m1'})
        response=self.client.post('/api/devices',headers=self.hdr(t),json={'meter_id':'m1','display_name':'pi1'})
        self.assertEqual(response.status_code,201)
        token=response.json['token']
        devices=self.client.get('/api/meters/m1/devices',headers=self.hdr(t)).json['devices']
        self.assertEqual(len(devices),1);self.assertNotIn('token',devices[0])
        heartbeat=self.client.post('/api/device/heartbeat',headers={'Authorization':'Bearer '+token})
        self.assertEqual(heartbeat.status_code,200);self.assertEqual(heartbeat.json['meter_id'],'m1')
        self.assertEqual(self.client.post('/api/device/heartbeat',headers={'Authorization':'Bearer wrong'}).status_code,401)
    def test_offline_status_from_heartbeat(self):
        self.login('boss');t=self.csrf()
        self.client.post('/api/meters',headers=self.hdr(t),json={'id':'m1','expected_interval_seconds':3600})
        token=self.client.post('/api/devices',headers=self.hdr(t),json={'meter_id':'m1'}).json['token']
        self.client.post('/api/device/heartbeat',headers={'Authorization':'Bearer '+token})
        self.assertEqual(self.client.get('/api/meters',headers=self.hdr(t)).json['meters'][0]['online_status'],'online')
        import sqlite3
        conn=sqlite3.connect(self.db);conn.execute("UPDATE devices SET last_seen_at='2020-01-01T00:00:00Z'");conn.commit();conn.close()
        self.assertEqual(self.client.get('/api/meters',headers=self.hdr(t)).json['meters'][0]['online_status'],'offline')
    def test_remote_anonymous_status_can_bootstrap_login(self):
        with patch.dict('os.environ',{'DRIPLINK_ALLOW_REMOTE':'1'}):
            app=create_app({'INSTANCE_DIR':self.tmp.name,'DEVICE_TOKEN':'x'*32,'SECRET_KEY':'test-key'},FakeReader())
        remote=app.test_client();remote.environ_base['REMOTE_ADDR']='192.168.1.20'
        response=remote.get('/api/status')
        self.assertEqual(response.status_code,200);self.assertIsNone(response.json['me'])
        self.assertEqual(response.json['meters'],[])
        login=remote.post('/api/auth/login',headers={'X-CSRF-Token':response.json['csrf_token']},json={'username':'bob','password':'secret123'})
        self.assertEqual(login.status_code,200)
    def test_registered_device_token_works_without_device_id_and_binds_meter(self):
        self.login('boss');t=self.csrf()
        self.client.post('/api/meters',headers=self.hdr(t),json={'id':'m1'})
        token=self.client.post('/api/devices',headers=self.hdr(t),json={'meter_id':'m1'}).json['token']
        response=self.client.post('/api/device/upload',headers={'Authorization':'Bearer '+token},data={
            'meter_id':'different','captured_at':'2026-01-01T00:00:00Z','upload_id':'per-device',
            'file':(io.BytesIO(photo()),'photo.jpg')})
        self.assertEqual(response.status_code,201);self.assertEqual(response.json['record']['meter_id'],'m1')
        self.client.post('/api/auth/logout',headers=self.hdr(t));self.login('bob')
        with self.client.get(response.json['record']['result_url']) as media_response:
            self.assertEqual(media_response.status_code,403)

if __name__=='__main__':unittest.main()
