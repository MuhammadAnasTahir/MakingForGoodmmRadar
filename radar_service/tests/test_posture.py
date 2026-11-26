"""Posture labels, room limit and labelled test recordings."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from test_gateway import gateway

RATE = 18.0


class PostureTests(unittest.TestCase):
    def run_heights(self, segments, ref=1.78, speed=0.0, latched_after=None, seed=1):
        """segments: [(seconds, height)] held flat; returns the label at the end of each segment."""
        rng = random.Random(seed)
        tracker = gateway.PostureTracker()
        labels, t, x = [], 0.0, 0.0
        for seconds, h in segments:
            for _ in range(int(seconds * RATE)):
                t += 1 / RATE
                x += speed / RATE
                track = dict(track_id=1, x_m=x, y_m=2.0, height_max_m=h + rng.gauss(0, 0.02))
                latched = {1: {}} if latched_after is not None and t >= latched_after else {}
                tracker.update([track], t, latched)
            labels.append(track['posture'])
        return labels

    def test_stand_sit_stand_lie_for_tall_and_short_visitors(self):
        for stand in (1.78, 1.45, 1.30):          # radar heights of a 1.92 m, 1.60 m and 1.45 m visitor
            sit = stand * 0.68
            labels = self.run_heights([(8, stand), (5, sit), (5, stand), (5, 0.35)])
            self.assertEqual(labels, ['standing', 'sitting', 'standing', 'lying'], stand)

    def test_walking_needs_movement(self):
        self.assertEqual(self.run_heights([(6, 1.78)], speed=0.8), ['walking'])
        self.assertEqual(self.run_heights([(6, 1.78)], speed=0.1), ['standing'])

    def test_fallen_shows_when_detector_latched(self):
        self.assertEqual(self.run_heights([(6, 1.78), (3, 0.3)], latched_after=7.0), ['standing', 'fallen'])

    def test_short_dip_does_not_change_label(self):
        # 0.3 s noise dip must not flip the label (hold time).
        self.assertEqual(self.run_heights([(6, 1.78), (0.3, 1.0), (2, 1.78)]), ['standing', 'standing', 'standing'])

    def test_room_limit_drops_far_ghosts(self):
        tracks = [dict(track_id=1, x_m=0.0, y_m=2.0), dict(track_id=6, x_m=-3.2, y_m=5.35)]
        self.assertEqual([t['track_id'] for t in gateway.limit_room(tracks, [-3, 3, 0, 4.5])], [1])
        self.assertEqual(len(gateway.limit_room(tracks, None)), 2)


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = gateway.ROOT
        gateway.ROOT = Path(self.tmp.name)

    def tearDown(self):
        gateway.stop_recording()
        gateway.ROOT = self.original
        self.tmp.cleanup()

    def test_numbered_files_with_summary(self):
        with self.assertRaises(ValueError):
            gateway.start_recording('not_a_label')
        for expected in ('fall_01', 'fall_02'):
            status = gateway.start_recording('fall')
            self.assertEqual(status['name'], expected)
            gateway.record_frame([dict(track_id=3, x_m=0, y_m=2, posture='standing')], dict(frame=1))
            gateway.record_frame([dict(track_id=3, x_m=0, y_m=2, posture='fallen')], dict(frame=2))
            self.assertFalse(gateway.stop_recording()['active'])
        lines = [json.loads(l) for l in (gateway.ROOT / 'recordings' / 'fall_02.jsonl').read_text().splitlines()]
        self.assertEqual([l['type'] for l in lines], ['start', 'frame', 'frame', 'summary'])
        self.assertEqual([c[2] for c in lines[-1]['posture_changes']], ['standing', 'fallen'])
        self.assertEqual(lines[-1]['frames'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
