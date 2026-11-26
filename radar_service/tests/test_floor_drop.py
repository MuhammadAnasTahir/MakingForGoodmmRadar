"""Demo fall rule (fall.rule = floor_drop) on synthetic TI heights, using the shipped config.json."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from test_gateway import gateway

RATE = 18.0
STAND = 1.70
FLOOR = 0.20


def profile(segments, start=STAND):
    heights, h = [], start
    for seconds, target in segments:
        n = max(1, int(round(seconds * RATE)))
        for i in range(1, n + 1):
            heights.append(h + (target - h) * i / n)
        h = target
    return heights


class FloorDropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = gateway.ROOT
        gateway.ROOT = Path(self.tmp.name)
        shipped = json.loads((Path(gateway.__file__).parent / 'config.json').read_text(encoding='utf-8-sig'))['fall']
        self.config = gateway.load_fall_config(dict(shipped, local_sound=False))
        self.assertEqual(self.config['rule'], 'floor_drop')
        self.rng = random.Random(7)

    def tearDown(self):
        gateway.ROOT = self.original
        self.tmp.cleanup()

    def run_frames(self, frames):
        """frames: list of lists of (track_id, x, y, height or None)."""
        detector = gateway.FloorDropDetector(self.config)
        self.times = {}
        for i, tracks in enumerate(frames):
            now = i / RATE
            rows = [dict(track_id=tid, x_m=x, y_m=y, z_m=0, vx_mps=0, vy_mps=0, vz_mps=0, height_min_m=0,
                         height_max_m=None if h is None else h + self.rng.gauss(0, 0.03)) for tid, x, y, h in tracks]
            before = len(self.kinds())
            detector.update(rows, now)
            for kind in self.kinds()[before:]:
                self.times.setdefault(kind, now)
        return detector

    def run_heights(self, heights, tid=1):
        return self.run_frames([[(tid, 0.0, 2.0, h)] for h in heights])

    def kinds(self):
        with gateway.database() as db:
            return [r[0] for r in db.execute('SELECT kind FROM events ORDER BY id')]

    def alerts(self):
        return [k for k in self.kinds() if k in gateway.ALERT_KINDS]

    def test_fast_fall_alerts_about_3_s_after_landing(self):
        heights = profile([(8, STAND), (0.8, FLOOR), (10, FLOOR)])
        self.run_heights(heights)
        self.assertEqual(self.alerts(), ['fall'])
        landed = 8.6   # reaches 0.85 m about here
        self.assertLess(self.times['fall'] - landed, 3.5)

    def test_slower_fall_up_to_3_s_alerts(self):
        self.run_heights(profile([(8, STAND), (2.5, FLOOR), (10, FLOOR)]))
        self.assertEqual(self.alerts(), ['fall'])

    def test_smooth_slow_lie_down_does_not_alert(self):
        self.run_heights(profile([(8, STAND), (8, FLOOR), (15, FLOOR), (3, STAND)]))
        self.assertEqual(self.alerts(), [])
        self.assertIn('lie_down_rejected', self.kinds())

    def test_kneel_then_lie_down_does_not_alert(self):
        self.run_heights(profile([(8, STAND), (1.5, 1.0), (2, 1.0), (1.5, FLOOR), (15, FLOOR), (3, STAND)]))
        self.assertEqual(self.alerts(), [])

    def test_staying_on_the_floor_after_slow_lie_down_gives_prolonged_low(self):
        self.run_heights(profile([(8, STAND), (8, FLOOR), (35, FLOOR)]))
        self.assertEqual(self.alerts(), ['prolonged_low'])

    def test_sitting_on_chair_or_at_desk_gives_nothing(self):
        self.run_heights(profile([(8, STAND), (0.8, 1.25), (20, 1.25), (1, STAND), (3, STAND), (0.8, 1.05), (20, 1.05)]))
        self.assertEqual(self.kinds(), [])

    def test_bending_to_pick_up_charger_does_not_alert(self):
        self.run_heights(profile([(8, STAND), (0.7, 0.55), (1.3, 0.55), (0.7, STAND), (5, STAND)]))
        self.assertEqual(self.alerts(), [])

    def test_radar_loses_person_during_fall_and_new_id_continues(self):
        # Person 29 starts falling, the radar loses them for 2 s, person 3 appears on the floor 1.5 m away.
        frames = [[(29, -0.5, 1.0, h)] for h in profile([(8, STAND), (1.0, 1.1)])]
        frames += [[] for _ in range(int(2.0 * RATE))]
        frames += [[(3, -1.5, 2.1, FLOOR)] for _ in range(int(6 * RATE))]
        self.run_frames(frames)
        self.assertEqual(self.alerts(), ['fall'])

    def test_someone_already_low_in_background_never_alerts(self):
        self.run_frames([[(6, -2.8, 1.4, 0.3)] for _ in range(int(45 * RATE))])
        self.assertEqual(self.kinds(), [])

    def test_two_falls_with_getting_up_between_give_two_alerts(self):
        self.run_heights(profile([(8, STAND), (0.8, FLOOR), (6, FLOOR), (1.5, STAND), (6, STAND), (0.8, FLOOR), (6, FLOOR)]))
        self.assertEqual(self.alerts(), ['fall', 'fall'])

    def test_staying_down_long_after_fall_gives_one_alert(self):
        detector = self.run_heights(profile([(8, STAND), (0.8, FLOOR), (60, FLOOR)]))
        self.assertEqual(self.alerts(), ['fall'])
        self.assertIn(1, detector.latched)


if __name__ == '__main__':
    unittest.main(verbosity=2)
