import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from face_attendance.database import Database
from face_attendance.__main__ import backup_before_upgrade


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = Database(self.root / "attendance.db")
        self.employee_id = self.database.add_employee(
            "E001", "测试员工", "研发部", [b"sample-1", b"sample-2"]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_employee_and_samples_are_created_atomically(self):
        rows = self.database.list_employees()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["employee_code"], "E001")
        self.assertEqual(rows[0]["sample_count"], 2)

    def test_duplicate_check_in_is_blocked(self):
        start = datetime(2026, 10, 20, 9, 0, tzinfo=timezone(timedelta(hours=8)))
        accepted, _ = self.database.check_in(self.employee_id, "上班", 32.5, "测试终端", 60, start)
        duplicate, message = self.database.check_in(
            self.employee_id, "下班", 31.2, "测试终端", 60, start + timedelta(seconds=20)
        )
        later, _ = self.database.check_in(
            self.employee_id, "下班", 30.1, "测试终端", 60, start + timedelta(seconds=61)
        )
        self.assertTrue(accepted)
        self.assertFalse(duplicate)
        self.assertIn("重复打卡", message)
        self.assertTrue(later)
        records = self.database.recent_attendance()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event_type"], "下班")

    def test_disabled_employee_cannot_check_in(self):
        self.database.set_employee_active(self.employee_id, False)
        accepted, message = self.database.check_in(self.employee_id, "上班", 30, "测试终端", 60)
        self.assertFalse(accepted)
        self.assertIn("停用", message)

    def test_delete_removes_face_data_but_preserves_attendance_snapshot(self):
        accepted, _ = self.database.check_in(self.employee_id, "上班", 30, "测试终端", 60)
        self.assertTrue(accepted)
        self.database.delete_employee(self.employee_id)
        self.assertEqual(self.database.list_employees(), [])
        records = self.database.recent_attendance()
        self.assertEqual(records[0]["employee_code"], "E001")
        self.assertEqual(self.database.training_samples(), [])

    def test_export_and_backup(self):
        self.database.check_in(self.employee_id, "上班", 30, "测试终端", 60)
        csv_path = self.root / "records.csv"
        backup_path = self.root / "backup.db"
        self.assertEqual(self.database.export_attendance(csv_path), 1)
        self.database.backup(backup_path)
        self.assertIn("E001", csv_path.read_text(encoding="utf-8-sig"))
        self.assertEqual(len(Database(backup_path).recent_attendance()), 1)

    def test_face_samples_can_be_replaced(self):
        self.database.replace_face_samples(self.employee_id, [b"new-1", b"new-2", b"new-3"])
        row = self.database.list_employees()[0]
        self.assertEqual(row["sample_count"], 3)
        self.assertEqual(len(self.database.training_samples()), 3)

    def test_invalid_event_type_is_rejected(self):
        with self.assertRaises(ValueError):
            self.database.check_in(self.employee_id, "午休", 30, "测试终端", 60)

    def test_v1_database_is_migrated(self):
        legacy_path = self.root / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        conn.execute(
            "CREATE TABLE attendance_records ("
            "id INTEGER PRIMARY KEY, employee_id INTEGER, employee_code TEXT NOT NULL, "
            "employee_name TEXT NOT NULL, occurred_at TEXT NOT NULL, "
            "confidence REAL NOT NULL, device_name TEXT NOT NULL)"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        migrated = Database(legacy_path)
        with migrated.connection() as migrated_conn:
            columns = {
                row[1]
                for row in migrated_conn.execute("PRAGMA table_info(attendance_records)").fetchall()
            }
            version = migrated_conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertIn("event_type", columns)
        self.assertEqual(version, 2)

    def test_v1_database_is_backed_up_before_migration(self):
        legacy_path = self.root / "legacy-backup.db"
        conn = sqlite3.connect(legacy_path)
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES('before-upgrade')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        backup_before_upgrade(legacy_path, self.root)
        backups = list((self.root / "backups").glob("pre-upgrade-v1-*.db"))
        self.assertEqual(len(backups), 1)
        backup_conn = sqlite3.connect(backups[0])
        try:
            self.assertEqual(
                backup_conn.execute("SELECT value FROM marker").fetchone()[0],
                "before-upgrade",
            )
            self.assertEqual(backup_conn.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            backup_conn.close()


if __name__ == "__main__":
    unittest.main()
