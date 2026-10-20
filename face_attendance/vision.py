from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .config import AppConfig
from .database import Database


@dataclass(frozen=True)
class FaceMatch:
    box: tuple[int, int, int, int]
    employee_id: int | None
    employee_code: str | None
    name: str | None
    distance: float | None
    quality_issue: str | None = None

    @property
    def recognized(self) -> bool:
        return self.employee_id is not None


class FaceEngine:
    FACE_SIZE = (160, 160)

    def __init__(self, database: Database, config: AppConfig):
        if not hasattr(cv2, "face") or not hasattr(cv2.face, "LBPHFaceRecognizer_create"):
            raise RuntimeError(
                "当前 OpenCV 缺少 face 模块。请卸载 opencv-python，"
                "并按 requirements.txt 安装 opencv-contrib-python。"
            )
        self.database = database
        self.config = config
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.detector = cv2.CascadeClassifier(cascade_path)
        if self.detector.empty():
            raise RuntimeError("人脸检测模型加载失败：" + cascade_path)
        self.recognizer = None
        self.people: dict[int, tuple[str, str]] = {}
        self.reload()

    def _normalized_face(self, gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
        x, y, width, height = box
        crop = gray[y : y + height, x : x + width]
        crop = cv2.resize(crop, self.FACE_SIZE, interpolation=cv2.INTER_AREA)
        return cv2.equalizeHist(crop)

    def detect(self, frame: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        boxes = self.detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=6,
            minSize=(self.config.min_face_size, self.config.min_face_size),
        )
        return gray, [tuple(map(int, item)) for item in boxes]

    def recognize(self, frame: np.ndarray) -> list[FaceMatch]:
        gray, boxes = self.detect(frame)
        matches: list[FaceMatch] = []
        for box in boxes:
            quality_issue = self.assess_quality(frame, box)
            if quality_issue:
                matches.append(FaceMatch(box, None, None, None, None, quality_issue))
                continue
            employee_id, code, name, distance = self.predict_face(
                self._normalized_face(gray, box)
            )
            matches.append(FaceMatch(box, employee_id, code, name, distance))
        return matches

    def predict_face(
        self, face: np.ndarray
    ) -> tuple[int | None, str | None, str | None, float | None]:
        """识别一张已标准化的人脸图；主要用于识别链路复用与自动化测试。"""
        if self.recognizer is None:
            return None, None, None, None
        label, distance = self.recognizer.predict(face)
        person = self.people.get(int(label))
        if person and float(distance) <= self.config.recognition_threshold:
            return int(label), person[0], person[1], float(distance)
        return None, None, None, float(distance)

    def extract_single_face(self, frame: np.ndarray) -> np.ndarray | None:
        gray, boxes = self.detect(frame)
        if len(boxes) != 1:
            return None
        return self._normalized_face(gray, boxes[0])

    def extract_enrollment_face(self, frame: np.ndarray) -> tuple[np.ndarray | None, str | None]:
        gray, boxes = self.detect(frame)
        if not boxes:
            return None, "未检测到人脸，请正对摄像头"
        if len(boxes) > 1:
            return None, "画面中只能有一人"
        issue = self.assess_quality(frame, boxes[0])
        if issue:
            return None, issue
        return self._normalized_face(gray, boxes[0]), None

    def assess_quality(self, frame: np.ndarray, box: tuple[int, int, int, int]) -> str | None:
        x, y, width, height = box
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        crop = gray[y : y + height, x : x + width]
        brightness = float(np.mean(crop))
        sharpness = float(cv2.Laplacian(crop, cv2.CV_64F).var())
        if brightness < self.config.min_face_brightness:
            return "画面过暗，请增加正面光线"
        if brightness > self.config.max_face_brightness:
            return "画面过亮，请避免强光直射"
        if sharpness < self.config.min_face_sharpness:
            return "画面模糊，请保持稳定并靠近摄像头"
        return None

    @staticmethod
    def encode_face(face: np.ndarray) -> bytes:
        ok, encoded = cv2.imencode(".png", face)
        if not ok:
            raise RuntimeError("人脸样本编码失败")
        return encoded.tobytes()

    def reload(self) -> None:
        rows = self.database.training_samples()
        self.people = {}
        images: list[np.ndarray] = []
        labels: list[int] = []
        for row in rows:
            image = cv2.imdecode(np.frombuffer(row["image"], dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            employee_id = int(row["id"])
            images.append(image)
            labels.append(employee_id)
            self.people[employee_id] = (str(row["employee_code"]), str(row["name"]))
        if not images:
            self.recognizer = None
            return
        recognizer = cv2.face.LBPHFaceRecognizer_create()
        recognizer.train(images, np.asarray(labels, dtype=np.int32))
        self.recognizer = recognizer

    def find_duplicate_identity(
        self, faces: list[np.ndarray], exclude_employee_id: int | None = None
    ) -> tuple[int, str, str, float] | None:
        if self.recognizer is None or not faces:
            return None
        candidates: dict[int, list[float]] = {}
        for face in faces:
            label, distance = self.recognizer.predict(face)
            label = int(label)
            if label == exclude_employee_id:
                continue
            if float(distance) <= self.config.duplicate_face_threshold and label in self.people:
                candidates.setdefault(label, []).append(float(distance))
        minimum_votes = max(3, len(faces) // 3)
        qualified = [
            (label, values)
            for label, values in candidates.items()
            if len(values) >= minimum_votes
        ]
        if not qualified:
            return None
        label, distances = min(qualified, key=lambda item: sum(item[1]) / len(item[1]))
        code, name = self.people[label]
        return label, code, name, sum(distances) / len(distances)
