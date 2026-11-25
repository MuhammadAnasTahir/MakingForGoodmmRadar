import unittest
from test_gateway import GatewayTests,gateway

class EpisodeTests(GatewayTests):
    def count_events(self):
        with gateway.database() as db:
            return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def test_long_hold_recovery_and_second_episode(self):
        d=gateway.FallDetector(dict(window_seconds=2.5,height_ratio=.6,confirm_seconds=1,cooldown_seconds=30,local_sound=False))
        for i in range(50): d.update([self.track()],i*.1)
        for i in range(50,500): d.update([self.track(.5)],i*.1)
        self.assertEqual(self.count_events(),1)
        d.update([],50)
        for i in range(501,530):d.update([self.track(.5)],i*.1)
        self.assertEqual(self.count_events(),1)
        for i in range(530,590): d.update([self.track()],i*.1)
        self.assertNotIn(1,d.latched)
        for i in range(590,630):d.update([self.track(.5)],i*.1)
        self.assertEqual(self.count_events(),2)

    def test_invalid_height_interrupts_recovery(self):
        d=gateway.FallDetector(dict(window_seconds=2.5,height_ratio=.6,confirm_seconds=1,cooldown_seconds=30,local_sound=False))
        for i in range(40):d.update([self.track()],i*.1)
        for i in range(40,80):d.update([self.track(.5)],i*.1)
        for i in range(80,91):d.update([self.track()],i*.1)
        d.update([self.track(None)],9.1)
        d.update([self.track()],9.2)
        self.assertIn(1,d.latched)
