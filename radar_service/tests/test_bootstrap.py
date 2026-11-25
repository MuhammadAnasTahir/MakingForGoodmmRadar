import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstrap', SOURCE / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)   # safe: the start-up flow only runs under __main__


class SensorPositionTests(unittest.TestCase):
    def test_match(self):
        self.assertEqual(bootstrap.check_sensor_position('sensorStop\nsensorPosition 1.6 0 15\n', [1.6, 0, 15]), [])

    def test_five_value_form_and_comment(self):
        self.assertEqual(bootstrap.check_sensor_position('sensorPosition 0 0 1.6 0 15 % installed\n', [1.6, 0, 15]), [])

    def test_mismatch_warns_in_chinese(self):
        warnings = bootstrap.check_sensor_position('sensorPosition 2 0 15\n', [1.6, 0, 15])
        self.assertTrue(warnings)
        self.assertIn('不一致', warnings[0])
        self.assertIn('安装高度', warnings[0])
        self.assertNotIn('下倾角', warnings[0])

    def test_missing_line_warns(self):
        self.assertIn('没有 sensorPosition', bootstrap.check_sensor_position('sensorStop\n', [1.6, 0, 15])[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
