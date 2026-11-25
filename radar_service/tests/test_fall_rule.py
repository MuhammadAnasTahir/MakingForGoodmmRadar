"""Synthetic 10 Hz tracks with small noise for the fall vs. lying-down rule."""
import json
import random
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from test_gateway import gateway

RATE = 10.0          # frames per second
STAND = 1.75         # height_max_m of a standing person
NOISE = 0.02         # metres, gaussian


def profile(segments, start=STAND):
    """segments: [(seconds, target_height), ...] -> list of heights, linear ramps at 10 Hz."""
    heights, h = [], start
    for seconds, target in segments:
        n = max(1, int(round(seconds * RATE)))
        for i in range(1, n + 1):
            heights.append(h + (target - h) * i / n)
        h = target
    return heights


class FallRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = gateway.ROOT
        gateway.ROOT = Path(self.tmp.name)
        self.rng = random.Random(42)

    def tearDown(self):
        gateway.ROOT = self.original
        self.tmp.cleanup()

    def run_profile(self, heights, x=0.0, y=2.0, noise=NOISE, **config):
        cfg = dict(local_sound=False)
        cfg.update(config)
        detector = gateway.FallDetector(cfg)
        for i, h in enumerate(heights):
            track = dict(track_id=1, x_m=x, y_m=y, z_m=1.0, vx_mps=0, vy_mps=0, vz_mps=0,
                         height_max_m=h + self.rng.gauss(0, noise), height_min_m=0)
            detector.update([track], i / RATE)
        return detector

    def events(self):
        with gateway.database() as db:
            rows = db.execute('SELECT kind, details FROM events ORDER BY id').fetchall()
        return [(kind, json.loads(details)) for kind, details in rows]

    def kinds(self):
        return [kind for kind, _ in self.events()]

    def alerts(self):
        return [k for k in self.kinds() if k in gateway.ALERT_KINDS]

    # ------------------------------------------------------------------ scenarios
    def test_fast_fall_gives_exactly_one_fall(self):
        self.run_profile(profile([(6, STAND), (0.6, 0.3), (10, 0.3)]))
        self.assertEqual(self.alerts(), ['fall'])
        kind, details = self.events()[0]
        for key in ('drop_m', 'duration_s', 'peak_speed_mps', 'low_h_m', 'reference_m'):
            self.assertIn(key, details)
        self.assertGreaterEqual(details['drop_m'], 0.6)
        self.assertLessEqual(details['duration_s'], 1.2)
        self.assertGreaterEqual(details['peak_speed_mps'], 1.0)

    def test_slow_lie_down_on_floor_is_rejected_without_alert(self):
        # stand -> kneel (2 s) -> pause -> lie down (2.5 s) -> lie 15 s -> get up
        self.run_profile(profile([(6, STAND), (2, 1.0), (1.5, 1.0), (2.5, 0.3), (15, 0.3), (3, STAND), (5, STAND)]))
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.kinds().count('lie_down_rejected'), 1)
        details = [d for k, d in self.events() if k == 'lie_down_rejected'][0]
        self.assertEqual(details['reason'], 'slow')
        self.assertGreater(details['duration_s'], 1.2)

    def test_lie_down_on_bed_safe_zone_never_alerts(self):
        # Fast flop onto the bed and stay there longer than floor_alert_s.
        self.run_profile(profile([(6, STAND), (0.7, 0.6), (40, 0.6)]), x=-1.5, y=3.0,
                         safe_zones=[[-2.0, -1.0, 2.0, 4.0]])
        self.assertEqual(self.alerts(), [])

    def test_crouch_and_stand_is_rejected(self):
        self.run_profile(profile([(6, STAND), (0.4, 0.8), (0.5, 0.8), (0.5, STAND), (5, STAND)]))
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.kinds(), ['crouch_rejected'])

    def test_crouch_to_floor_level_and_stand_is_rejected(self):
        # Picking something up from the floor: big and fast, but back up within confirm_seconds.
        self.run_profile(profile([(6, STAND), (0.4, 0.5), (0.3, 0.5), (0.4, STAND), (5, STAND)]))
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.kinds(), ['crouch_rejected'])

    def test_sit_on_chair_gives_nothing(self):
        self.run_profile(profile([(6, STAND), (1, 1.25), (30, 1.25), (1, STAND), (5, STAND)]))
        self.assertEqual(self.kinds(), [])

    def test_slow_slide_and_stay_low_gives_prolonged_low(self):
        self.run_profile(profile([(6, STAND), (4, 0.4), (35, 0.4)]))
        self.assertEqual(self.alerts(), ['prolonged_low'])
        self.assertEqual(self.kinds(), ['lie_down_rejected', 'prolonged_low'])
        details = [d for k, d in self.events() if k == 'prolonged_low'][0]
        self.assertGreaterEqual(details['low_duration_s'], 30)

    def test_slow_slide_in_safe_zone_gives_no_prolonged_low(self):
        self.run_profile(profile([(6, STAND), (4, 0.4), (35, 0.4)]), x=-1.5, y=3.0,
                         safe_zones=[[-2.0, -1.0, 2.0, 4.0]])
        self.assertEqual(self.alerts(), [])

    def test_fall_and_stay_down_60s_gives_one_alert_total(self):
        detector = self.run_profile(profile([(6, STAND), (0.5, 0.3), (60, 0.3)]))
        self.assertEqual(self.alerts(), ['fall'])
        self.assertEqual(self.kinds(), ['fall'])
        self.assertIn(1, detector.latched)

    def test_fall_after_recovery_alerts_again(self):
        heights = profile([(6, STAND), (0.5, 0.3), (5, 0.3), (1, STAND), (8, STAND), (0.5, 0.3), (5, 0.3)])
        self.run_profile(heights, cooldown_seconds=5)
        self.assertEqual(self.alerts(), ['fall', 'fall'])

    def test_one_motion_lie_down_vs_fall_across_noise_seeds(self):
        # A lie-down in one smooth 2 s motion must not look fast because it was measured mid-way.
        lie = profile([(6, STAND), (2, 0.3), (15, 0.3), (3, STAND)])
        fall = profile([(6, STAND), (0.6, 0.3), (10, 0.3)])
        for seed in range(15):
            for heights, expected in ((lie, []), (fall, ['fall'])):
                gateway.ROOT = Path(self.tmp.name) / ('%d-%d' % (seed, len(expected)))
                gateway.ROOT.mkdir()
                self.rng = random.Random(seed)
                self.run_profile(heights, noise=0.05)
                self.assertEqual(self.alerts(), expected, 'seed %d' % seed)

    def test_one_missing_height_does_not_break_detection(self):
        heights = profile([(6, STAND), (0.6, 0.3), (10, 0.3)])
        heights[62] = None
        cfg = dict(local_sound=False)
        detector = gateway.FallDetector(cfg)
        for i, h in enumerate(heights):
            detector.update([dict(track_id=1, x_m=0, y_m=2, z_m=1, vx_mps=0, vy_mps=0, vz_mps=0,
                                  height_max_m=h, height_min_m=0)], i / RATE)
        self.assertEqual(self.alerts(), ['fall'])

    # ------------------------------------------------------------------ config, database, API
    def test_fall_config_defaults_and_validation(self):
        merged = gateway.load_fall_config(dict(window_seconds=2.5, height_ratio=0.6))
        for key in ('min_drop_m', 'max_low_height_m', 'fall_max_duration_s', 'fall_min_speed_mps',
                    'floor_alert_s', 'safe_zones'):
            self.assertIn(key, merged)
        self.assertEqual(merged['min_drop_m'], 0.6)
        with self.assertRaises(ValueError):
            gateway.load_fall_config(dict(safe_zones=[[1, 0, 0, 1]]))
        with self.assertRaises(ValueError):
            gateway.load_fall_config(dict(min_drop_m=-1))
        config = json.loads((Path(gateway.__file__).parent / 'config.json').read_text(encoding='utf-8-sig'))
        shipped = gateway.load_fall_config(config['fall'])
        self.assertEqual(shipped['floor_alert_s'], 30)
        self.assertEqual(config['expected_sensor_position'], [1.6, 0, 15])

    def test_old_database_is_migrated(self):
        path = gateway.ROOT / 'events.sqlite3'
        db = sqlite3.connect(str(path))
        db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp REAL, track_id INTEGER, details TEXT, acknowledged INTEGER DEFAULT 0)')
        db.execute('INSERT INTO events(timestamp,track_id,details) VALUES(1,5,?)', (json.dumps({'x_m': 1}),))
        db.commit()
        db.close()
        with gateway.database() as db:
            self.assertEqual(db.execute('SELECT kind, track_id FROM events').fetchall(), [('fall', 5)])
        with gateway.database() as db:   # second open must not fail
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_api_events_returns_kind_and_keeps_alerts_visible(self):
        with gateway.database() as db:
            db.execute("INSERT INTO events(timestamp,track_id,kind,details) VALUES(1,1,'fall','{}')")
            for _ in range(120):
                db.execute("INSERT INTO events(timestamp,track_id,kind,details) VALUES(2,1,'crouch_rejected','{}')")
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/events' % server.server_port) as r:
                rows = json.load(r)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        self.assertEqual([r['kind'] for r in rows].count('fall'), 1)
        self.assertEqual([r['kind'] for r in rows].count('crouch_rejected'), 50)
        self.assertEqual(rows, sorted(rows, key=lambda r: -r['id']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
