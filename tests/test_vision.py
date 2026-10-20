import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from face_attendance.config import AppConfig
from face_attendance.database import Database
from face_attendance.vision import FaceEngine


def synthetic_face() -> np.ndarray:
    """生成确定性的灰度纹理，避免测试依赖来源不明的人像文件。"""
    image = np.full((160, 160), 145, dtype=np.uint8)
    cv2.ellipse(image, (80, 82), (55, 68), 0, 0, 360, 105, -1)
    cv2.circle(image, (58, 65), 9, 205, -1)
    cv2.circle(image, (102, 65), 9, 205, -1)
    cv2.circle(image, (58, 65), 3, 30, -1)
    cv2.circle(image, (102, 65), 3, 30, -1)
    cv2.line(image, (80, 72), (72, 100), 185, 4)
    cv2.ellipse(image, (80, 118), (24, 9), 0, 10, 170, 220, 3)
    return image


class VisionTests(unittest.TestCase):
    def test_training_prediction_and_duplicate_check_round_trip(self):
        face = synthetic_face()
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "vision.db")
            engine = FaceEngine(database, AppConfig())
            database.add_employee(
                "SELFTEST", "自检样本", "", [engine.encode_face(face)] * 10
            )
            engine.reload()
            employee_id, code, name, distance = engine.predict_face(face)
            self.assertIsNotNone(employee_id)
            self.assertEqual(code, "SELFTEST")
            self.assertEqual(name, "自检样本")
            self.assertLessEqual(distance, 1.0)
            duplicate = engine.find_duplicate_identity([face] * 10)
            self.assertIsNotNone(duplicate)
            self.assertEqual(duplicate[1], "SELFTEST")
            self.assertIsNone(
                engine.find_duplicate_identity(
                    [face] * 10, exclude_employee_id=duplicate[0]
                )
            )

    def test_blurred_face_fails_quality_gate(self):
        random = np.random.default_rng(20261020)
        frame = random.integers(50, 210, size=(200, 200, 3), dtype=np.uint8)
        blurred = cv2.GaussianBlur(frame, (99, 99), 0)
        with tempfile.TemporaryDirectory() as directory:
            engine = FaceEngine(Database(Path(directory) / "quality.db"), AppConfig())
            issue = engine.assess_quality(blurred, (0, 0, 200, 200))
            self.assertIsNotNone(issue)
            self.assertIn("模糊", issue)


if __name__ == "__main__":
    unittest.main()
