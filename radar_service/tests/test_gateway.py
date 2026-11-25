import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gateway', SOURCE / 'service.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = gateway.ROOT
        gateway.ROOT = Path(self.tmp.name)
        (gateway.ROOT / 'web').mkdir()
        for name in ['index.html','app.js','theme.css','data.js','styles.css']:
            (gateway.ROOT / 'web' / name).write_bytes((SOURCE / 'web' / name).read_bytes())
        (gateway.ROOT / 'config.json').write_text('{"secret":"private"}')
        gateway.STATE.update(status='streaming', tracks=[], points=[], last_frame_at=time.time(),error=None)

    def tearDown(self):
        gateway.ROOT=self.original
        self.tmp.cleanup()

    def track(self, height=1.8, x=0):
        return dict(track_id=1,x_m=x,y_m=1,z_m=0,vx_mps=0,vy_mps=0,vz_mps=0,height_max_m=height,height_min_m=0)

    def test_filters_and_motion(self):
        f=gateway.validate_filters(dict(enabled=True,roi=[-1,1,0,2],exclusions=[[0.5,0.9,0.5,1.5]]))
        self.assertEqual(len(gateway.filter_tracks([self.track(x=0),self.track(x=0.6)],f)),1)
        m=gateway.MotionClassifier()
        self.assertEqual(len(m.update([self.track()],0)),1)
        for second in range(1,30):
            self.assertEqual(len(m.update([self.track()],second)),1)
        # Keep seeing the same target so that absence timeout does not reset it.
        self.assertEqual(len(m.update([self.track()],30)),0)
        self.assertEqual(len(m.update([self.track(x=0.3)],31)),1)
        self.assertEqual(len(m.update([self.track(x=0.3)],32)),1)

    def test_fall_confirmation_and_dedup(self):
        d=gateway.FallDetector(dict(window_seconds=2.5,height_ratio=0.6,confirm_seconds=1,cooldown_seconds=30,local_sound=False))
        for i in range(26):d.update([self.track()],i/10)
        d.update([self.track(height=0.5)],2.6)  # new rule: low height must be <= max_low_height_m (0.7 m)
        with gateway.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        for i in range(27,40):d.update([self.track(height=0.5)],i/10)
        with gateway.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    def test_real_points_count_cap_and_nan(self):
        points,count=gateway.prepare_points(dict(pointCloud=[[0,1,2,0]]*4001,numDetectedPoints=4001))
        self.assertEqual(count,4001);self.assertLessEqual(len(points),2000)
        points,_=gateway.prepare_points(dict(pointCloud=[[0,float('nan'),0,0]],numDetectedPoints=1))
        self.assertEqual(points,[])

    def test_http_assets_events_filters_and_stale(self):
        server=gateway.ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def get(path):
            with urllib.request.urlopen(base+path) as r:return r.read()
        try:
            self.assertIn(b'RadarSense',get('/'))
            self.assertIn(b'use strict',get('/app.js?v=9'))
            for path in ['/config.json','/service.py','/events.sqlite3','/%2e%2e/config.json']:
                with self.assertRaises(urllib.error.HTTPError) as err:get(path)
                self.assertEqual(err.exception.code,404)
            with gateway.database() as db:
                db.execute('INSERT INTO events(timestamp,track_id,details) VALUES(?,?,?)',(time.time(),1,json.dumps(self.track())))
            rows=json.loads(get('/api/events'));self.assertEqual(rows[0]['details']['y_m'],1)
            req=urllib.request.Request(base+'/api/events/1/ack',data=b'',headers={'X-Radar-Action':'ack'})
            with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['acknowledged'])
            settings=dict(enabled=True,roi=[-1,1,0,2],exclusions=[])
            req=urllib.request.Request(base+'/api/filters',data=json.dumps(settings).encode(),headers={'X-Radar-Action':'configure'})
            with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['saved'])
            self.assertEqual(json.loads((gateway.ROOT/'filters.json').read_text()),settings)
            gateway.STATE.update(last_frame_at=time.time()-10,tracks=[self.track()],points=[[0,1,0,0]])
            stale=json.loads(get('/api/state'));self.assertEqual(stale['status'],'stale');self.assertEqual(stale['tracks'],[]);self.assertEqual(stale['points'],[])
        finally:server.shutdown();server.server_close();worker.join()


if __name__=='__main__':unittest.main(verbosity=2)
