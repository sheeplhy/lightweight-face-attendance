import json
import tempfile
import unittest
from pathlib import Path

from face_attendance.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_default_config_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = AppConfig.load(path)
            self.assertTrue(path.exists())
            self.assertEqual(config.camera_indices, (0, 1))

    def test_unknown_option_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"surprise": True}), encoding="utf-8")
            with self.assertRaises(ValueError):
                AppConfig.load(path)


if __name__ == "__main__":
    unittest.main()
