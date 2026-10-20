from __future__ import annotations

import logging
import sqlite3
import sys
import tkinter as tk
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

from .config import AppConfig, app_home
from .database import Database


def configure_logging(log_path) -> None:
    handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def create_daily_backup(database: Database, root, retention_days: int) -> None:
    backup_dir = root / "backups"
    today = datetime.now().date()
    destination = backup_dir / f"attendance-{today:%Y%m%d}.db"
    if not destination.exists():
        database.backup(destination)
    cutoff = today - timedelta(days=retention_days)
    for path in backup_dir.glob("attendance-????????.db"):
        try:
            stamp = datetime.strptime(path.stem.removeprefix("attendance-"), "%Y%m%d").date()
            if stamp < cutoff:
                path.unlink()
        except (ValueError, OSError):
            logging.getLogger(__name__).warning("Cannot rotate backup: %s", path)


def backup_before_upgrade(database_path, root) -> None:
    if not database_path.exists():
        return
    source = sqlite3.connect(database_path)
    target = None
    try:
        version = int(source.execute("PRAGMA user_version").fetchone()[0])
        if version != 1:
            return
        backup_dir = root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = backup_dir / f"pre-upgrade-v1-{datetime.now():%Y%m%d-%H%M%S}.db"
        target = sqlite3.connect(destination)
        source.backup(target)
    finally:
        if target is not None:
            target.close()
        source.close()


def main() -> int:
    root_dir = app_home()
    configure_logging(root_dir / "logs" / "application.log")
    logger = logging.getLogger(__name__)
    root = tk.Tk()
    root.withdraw()
    try:
        config = AppConfig.load(root_dir / "config.json")
        database_path = root_dir / "attendance.db"
        backup_before_upgrade(database_path, root_dir)
        database = Database(database_path)
        create_daily_backup(database, root_dir, config.backup_retention_days)
        from .ui import AttendanceApp, setup_first_password

        if not setup_first_password(root, database):
            root.destroy()
            return 1
        root.deiconify()
        AttendanceApp(root, database, config)
        root.mainloop()
        return 0
    except Exception as exc:
        logger.exception("Application startup failed")
        messagebox.showerror(
            "启动失败",
            f"程序无法启动：{exc}\n\n详细信息已写入：{root_dir / 'logs' / 'application.log'}",
            parent=root,
        )
        root.destroy()
        return 1


if __name__ == "__main__":
    sys.exit(main())
