import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import hmac
import hashlib

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'server/bridge.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        b.DB = self.tmp.name + '/db.sqlite3'
        b.CONFIG = self.tmp.name + '/devices.json'
        self.settings = {'senders': ['TEST-FINES'], 'keywords': ['fine'],
            'token_sha256': b.digest('device-secret'), 'owner_label': 'Test owner',
            'admin_phone': '233200000000', 'admin_opt_in': True}
        Path(b.CONFIG).write_text(json.dumps({'owner-1': self.settings}))
        self.event = {'event_id': 'notice-1', 'sender': 'TEST-FINES', 'body': 'Fine notice TEST-001', 'received_at': 1000}
        self.old_env = dict(os.environ)
        os.environ['FINEFLOW_MODE'] = 'test'
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old_env)
        self.tmp.cleanup()
    def test_filters_require_sender_and_keyword(self):
        self.assertTrue(b.matches(self.settings,'test-fines','FINE 100'))
        self.assertFalse(b.matches(self.settings,'BANK','fine'))
        self.assertFalse(b.matches(self.settings,'TEST-FINES','Login code 123'))
        self.assertFalse(b.matches({'senders':[''], 'keywords':['']},'','anything'))
    def test_auth(self):
        self.assertIsNone(b.authenticate(b.config(),''))
        self.assertIsNone(b.authenticate(b.config(),'wrong'))
        self.assertEqual(b.authenticate(b.config(),'device-secret')[0],'owner-1')
    def test_idempotency(self):
        calls=[]
        def send(s,e): calls.append(e); return 'accepted','wamid.1'
        self.assertEqual(b.ingest('owner-1',self.settings,self.event,send)['status'],'accepted')
        self.assertTrue(b.ingest('owner-1',self.settings,self.event,send)['duplicate'])
        self.assertEqual(len(calls),1)
    def test_parallel_duplicates_only_send_once(self):
        b.database().close()
        calls=[]
        def send(s,e): calls.append(1); return 'accepted','wamid.1'
        threads=[threading.Thread(target=b.ingest,args=('owner-1',self.settings,self.event,send)) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(calls),1)
    def test_filtered_not_stored(self):
        self.event['sender']='BANK'
        self.assertEqual(b.ingest('owner-1',self.settings,self.event)['status'],'filtered')
        with b.database() as con: self.assertEqual(con.execute('SELECT count(*) FROM notices').fetchone()[0],0)
    def test_uncertain_not_resent(self):
        calls=[]
        def send(s,e): calls.append(1); raise OSError('Timeout')
        self.assertEqual(b.ingest('owner-1',self.settings,self.event,send)['status'],'uncertain')
        b.ingest('owner-1',self.settings,self.event,send)
        self.assertEqual(len(calls),1)
    def test_template_routes_server_side(self):
        self.event['admin_phone']='233999999999'
        payload=b.template_payload(self.settings,self.event)
        self.assertEqual(payload['to'],'233200000000')
        self.assertEqual(payload['type'],'template')
        self.assertEqual(len(payload['template']['components'][0]['parameters']),4)
    def test_webhook_status_cannot_regress(self):
        b.ingest('owner-1',self.settings,self.event,lambda s,e:('accepted','wamid.1'))
        for status in ['read','sent','failed']:
            raw=json.dumps({'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.1','status':status}]}}]}]}).encode()
            b.apply_webhook(raw)
        with b.database() as con: self.assertEqual(con.execute('SELECT status FROM notices').fetchone()[0],'read')
    def test_live_needs_configuration(self):
        os.environ['FINEFLOW_MODE']='live'
        for k in ['WA_ACCESS_TOKEN','WA_PHONE_NUMBER_ID','WA_GRAPH_VERSION']: os.environ.pop(k,None)
        self.assertEqual(b.ingest('owner-1',self.settings,self.event)['status'],'configuration_error')
    def test_invalid_event(self):
        for value in [None, [], {}, dict(self.event,body='x'*3001), dict(self.event,received_at=True)]:
            with self.assertRaises(ValueError): b.validate_event(value)
    def test_http_flow_and_signature(self):
        server=b.ThreadingHTTPServer(('127.0.0.1',0),b.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            def post(path,data,headers={}):
                return urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**headers}))
            with self.assertRaises(urllib.error.HTTPError) as error: post('/v1/notices',self.event)
            self.assertEqual(error.exception.code,401)
            with post('/v1/notices',self.event,{'Authorization':'Bearer device-secret'}) as response:
                self.assertEqual(json.load(response)['status'],'simulated')
            with self.assertRaises(urllib.error.HTTPError) as error: post('/webhook',{})
            self.assertEqual(error.exception.code,403)
            os.environ['WA_APP_SECRET']='test-secret'
            raw=json.dumps({}).encode()
            signature='sha256='+hmac.new(b'test-secret',raw,hashlib.sha256).hexdigest()
            with post('/webhook',{}, {'X-Hub-Signature-256':signature}) as response: self.assertEqual(response.status,200)
        finally: server.shutdown(); server.server_close(); thread.join()

if __name__ == '__main__': unittest.main()
