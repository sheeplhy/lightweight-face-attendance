from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Sequence

VALID_EVENT_TYPES = {"上班", "下班", "签到"}


def local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    department TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS face_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    image BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attendance_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
                    employee_code TEXT NOT NULL,
                    employee_name TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT '签到',
                    occurred_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    device_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_attendance_employee_time
                    ON attendance_records(employee_id, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_attendance_time
                    ON attendance_records(occurred_at DESC);
                """
            )
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version < 2:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(attendance_records)").fetchall()
                }
                if "event_type" not in columns:
                    conn.execute(
                        "ALTER TABLE attendance_records "
                        "ADD COLUMN event_type TEXT NOT NULL DEFAULT '签到'"
                    )
                conn.execute("PRAGMA user_version = 2")

    def get_setting(self, key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def add_employee(
        self, code: str, name: str, department: str, samples: Sequence[bytes]
    ) -> int:
        code, name, department = code.strip(), name.strip(), department.strip()
        if not code or not name:
            raise ValueError("员工编号和姓名不能为空")
        if not samples:
            raise ValueError("必须采集人脸样本")
        now = local_now()
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "INSERT INTO employees(employee_code, name, department, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (code, name, department, now, now),
            )
            employee_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO face_samples(employee_id, image, created_at) VALUES(?, ?, ?)",
                [(employee_id, sample, now) for sample in samples],
            )
            conn.execute(
                "INSERT INTO audit_logs(action, details, created_at) VALUES(?, ?, ?)",
                ("employee_created", f"{code} {name}; samples={len(samples)}", now),
            )
            return employee_id

    def list_employees(self, active_only: bool = False) -> list[sqlite3.Row]:
        query = (
            "SELECT e.*, COUNT(s.id) AS sample_count FROM employees e "
            "LEFT JOIN face_samples s ON s.employee_id = e.id"
        )
        params: tuple[object, ...] = ()
        if active_only:
            query += " WHERE e.active = 1"
        query += " GROUP BY e.id ORDER BY e.employee_code"
        with self.connection() as conn:
            return list(conn.execute(query, params).fetchall())

    def set_employee_active(self, employee_id: int, active: bool) -> None:
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT employee_code, name FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if row is None:
                raise ValueError("员工不存在")
            now = local_now()
            conn.execute(
                "UPDATE employees SET active = ?, updated_at = ? WHERE id = ?",
                (int(active), now, employee_id),
            )
            conn.execute(
                "INSERT INTO audit_logs(action, details, created_at) VALUES(?, ?, ?)",
                ("employee_enabled" if active else "employee_disabled", row["employee_code"], now),
            )

    def delete_employee(self, employee_id: int) -> None:
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT employee_code, name FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if row is None:
                raise ValueError("员工不存在")
            conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            conn.execute(
                "INSERT INTO audit_logs(action, details, created_at) VALUES(?, ?, ?)",
                ("employee_deleted", f"{row['employee_code']} {row['name']}", local_now()),
            )

    def replace_face_samples(self, employee_id: int, samples: Sequence[bytes]) -> None:
        if not samples:
            raise ValueError("必须采集人脸样本")
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT employee_code, name FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if row is None:
                raise ValueError("员工不存在")
            now = local_now()
            conn.execute("DELETE FROM face_samples WHERE employee_id = ?", (employee_id,))
            conn.executemany(
                "INSERT INTO face_samples(employee_id, image, created_at) VALUES(?, ?, ?)",
                [(employee_id, sample, now) for sample in samples],
            )
            conn.execute(
                "UPDATE employees SET updated_at = ? WHERE id = ?", (now, employee_id)
            )
            conn.execute(
                "INSERT INTO audit_logs(action, details, created_at) VALUES(?, ?, ?)",
                ("face_samples_replaced", f"{row['employee_code']}; samples={len(samples)}", now),
            )

    def training_samples(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT e.id, e.employee_code, e.name, s.image "
                    "FROM employees e JOIN face_samples s ON s.employee_id = e.id "
                    "WHERE e.active = 1 ORDER BY e.id, s.id"
                ).fetchall()
            )

    def employee(self, employee_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()

    def check_in(
        self,
        employee_id: int,
        event_type: str,
        confidence: float,
        device_name: str,
        duplicate_seconds: int,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError("无效的考勤类型")
        occurred = (now or datetime.now().astimezone()).astimezone()
        with self.transaction(immediate=True) as conn:
            person = conn.execute(
                "SELECT employee_code, name, active FROM employees WHERE id = ?",
                (employee_id,),
            ).fetchone()
            if person is None or not person["active"]:
                return False, "员工不存在或已停用"
            latest = conn.execute(
                "SELECT occurred_at FROM attendance_records "
                "WHERE employee_id = ? ORDER BY occurred_at DESC LIMIT 1",
                (employee_id,),
            ).fetchone()
            if latest:
                previous = datetime.fromisoformat(latest["occurred_at"])
                elapsed = (occurred - previous).total_seconds()
                if elapsed < duplicate_seconds:
                    remaining = max(1, int(duplicate_seconds - elapsed))
                    return False, f"请勿重复打卡，{remaining} 秒后可再次操作"
            occurred_text = occurred.isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO attendance_records("
                "employee_id, employee_code, employee_name, event_type, "
                "occurred_at, confidence, device_name"
                ") VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    employee_id,
                    person["employee_code"],
                    person["name"],
                    event_type,
                    occurred_text,
                    float(confidence),
                    device_name,
                ),
            )
            return True, occurred_text

    def recent_attendance(self, limit: int = 200) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT employee_code, employee_name, event_type, occurred_at, confidence, device_name "
                    "FROM attendance_records ORDER BY occurred_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )

    def export_attendance(self, path: Path) -> int:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT employee_code, employee_name, event_type, occurred_at, confidence, device_name "
                "FROM attendance_records ORDER BY occurred_at"
            ).fetchall()
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["员工编号", "姓名", "考勤类型", "打卡时间", "识别距离", "设备"])
            for row in rows:
                values = list(row)
                for index in (0, 1, 2, 5):
                    text = str(values[index])
                    if text.startswith(("=", "+", "-", "@")):
                        values[index] = "'" + text
                writer.writerow(values)
        return len(rows)

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
