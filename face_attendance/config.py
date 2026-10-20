from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    camera_indices: tuple[int, ...] = (0, 1)
    recognition_threshold: float = 65.0
    duplicate_interval_seconds: int = 60
    enrollment_samples: int = 20
    detection_interval_frames: int = 3
    stable_recognition_cycles: int = 3
    min_face_size: int = 100
    min_face_brightness: float = 40.0
    max_face_brightness: float = 215.0
    min_face_sharpness: float = 80.0
    duplicate_face_threshold: float = 45.0
    device_name: str = "本机考勤终端"
    backup_retention_days: int = 7

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            value = cls()
            path.write_text(
                json.dumps(asdict(value), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return value
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("配置包含未知字段：" + ", ".join(sorted(unknown)))
        if "camera_indices" in raw:
            raw["camera_indices"] = tuple(int(v) for v in raw["camera_indices"])
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.camera_indices:
            raise ValueError("camera_indices 不能为空")
        if not 20 <= self.recognition_threshold <= 120:
            raise ValueError("recognition_threshold 必须在 20 到 120 之间")
        if self.duplicate_interval_seconds < 1:
            raise ValueError("duplicate_interval_seconds 必须大于 0")
        if not 10 <= self.enrollment_samples <= 100:
            raise ValueError("enrollment_samples 必须在 10 到 100 之间")
        if self.detection_interval_frames < 1:
            raise ValueError("detection_interval_frames 必须大于 0")
        if self.stable_recognition_cycles < 2:
            raise ValueError("stable_recognition_cycles 不能小于 2")
        if self.min_face_size < 60:
            raise ValueError("min_face_size 不能小于 60")
        if not 0 <= self.min_face_brightness < self.max_face_brightness <= 255:
            raise ValueError("人脸亮度阈值无效")
        if self.min_face_sharpness < 0:
            raise ValueError("min_face_sharpness 不能小于 0")
        if not 20 <= self.duplicate_face_threshold <= 100:
            raise ValueError("duplicate_face_threshold 必须在 20 到 100 之间")
        if not 1 <= self.backup_retention_days <= 90:
            raise ValueError("backup_retention_days 必须在 1 到 90 之间")


def app_home() -> Path:
    override = os.environ.get("FACE_ATTENDANCE_HOME")
    if override:
        root = Path(override).expanduser().resolve()
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) / "FaceAttendance" if local else Path.home() / ".face-attendance"
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    return root
