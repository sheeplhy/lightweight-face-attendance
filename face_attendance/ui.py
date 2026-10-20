from __future__ import annotations

import logging
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2
from PIL import Image, ImageTk

from .config import AppConfig
from .database import Database
from .security import hash_password, validate_password, verify_password
from .vision import FaceEngine, FaceMatch

LOGGER = logging.getLogger(__name__)


class EnrollmentDialog(tk.Toplevel):
    def __init__(self, owner: "AdminWindow", employee_id: int | None = None):
        super().__init__(owner)
        self.owner = owner
        self.app = owner.app
        self.employee_id = employee_id
        self.samples = []
        self.running = False
        self.title("重新采集人脸" if employee_id else "员工建档")
        self.resizable(False, False)
        self.transient(owner)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="员工编号").grid(row=0, column=0, sticky="w", pady=4)
        self.code = ttk.Entry(frame, width=28)
        self.code.grid(row=0, column=1, pady=4)
        ttk.Label(frame, text="姓名").grid(row=1, column=0, sticky="w", pady=4)
        self.name = ttk.Entry(frame, width=28)
        self.name.grid(row=1, column=1, pady=4)
        ttk.Label(frame, text="部门").grid(row=2, column=0, sticky="w", pady=4)
        self.department = ttk.Entry(frame, width=28)
        self.department.grid(row=2, column=1, pady=4)
        if employee_id is not None:
            employee = self.app.database.employee(employee_id)
            if employee is None:
                raise ValueError("员工不存在")
            self.code.insert(0, employee["employee_code"])
            self.name.insert(0, employee["name"])
            self.department.insert(0, employee["department"])
            self.code.configure(state="disabled")
            self.name.configure(state="disabled")
            self.department.configure(state="disabled")
        self.status = ttk.Label(
            frame,
            text="填写信息后开始采集；画面中只能有一张正脸。",
            wraplength=320,
        )
        self.status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 8))
        self.progress = ttk.Progressbar(
            frame, maximum=self.app.config.enrollment_samples, length=320
        )
        self.progress.grid(row=4, column=0, columnspan=2, pady=8)
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))
        self.start_button = ttk.Button(buttons, text="开始采集", command=self.start)
        self.start_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="取消", command=self.close).pack(side="left", padx=4)
        self.code.focus_set()

    def start(self) -> None:
        if not self.code.get().strip() or not self.name.get().strip():
            messagebox.showwarning("信息不完整", "员工编号和姓名不能为空。", parent=self)
            return
        if self.app.latest_frame is None:
            messagebox.showerror("摄像头不可用", "没有可用画面，请先恢复摄像头。", parent=self)
            return
        self.samples.clear()
        self.running = True
        self.start_button.configure(state="disabled")
        self.status.configure(text="正在采集，请缓慢左右转头并保持光线充足……")
        self.after(100, self.capture)

    def capture(self) -> None:
        if not self.running:
            return
        frame = self.app.latest_frame
        face, issue = (
            self.app.engine.extract_enrollment_face(frame.copy())
            if frame is not None
            else (None, "摄像头无可用画面")
        )
        if face is not None:
            self.samples.append(face)
            self.progress.configure(value=len(self.samples))
            self.status.configure(
                text=f"已采集 {len(self.samples)}/{self.app.config.enrollment_samples}，请缓慢调整角度。"
            )
        else:
            self.status.configure(text=issue or "人脸质量不符合要求")
        if len(self.samples) >= self.app.config.enrollment_samples:
            self.finish()
        else:
            self.after(220, self.capture)

    def finish(self) -> None:
        self.running = False
        try:
            duplicate = self.app.engine.find_duplicate_identity(
                self.samples, exclude_employee_id=self.employee_id
            )
            if duplicate:
                _, code, name, distance = duplicate
                self.start_button.configure(state="normal")
                messagebox.showerror(
                    "疑似重复建档",
                    f"采集的人脸与现有员工 {code} {name} 高度相似（距离 {distance:.1f}）。\n"
                    "为避免代打卡，本次保存已取消。",
                    parent=self,
                )
                return
            encoded_samples = [self.app.engine.encode_face(face) for face in self.samples]
            if self.employee_id is None:
                self.app.database.add_employee(
                    self.code.get(), self.name.get(), self.department.get(), encoded_samples
                )
            else:
                self.app.database.replace_face_samples(self.employee_id, encoded_samples)
            self.app.engine.reload()
            self.owner.refresh_all()
            messagebox.showinfo("保存成功", "员工信息和人脸样本已保存。", parent=self)
            self.destroy()
        except Exception as exc:
            LOGGER.exception("Employee enrollment failed")
            self.start_button.configure(state="normal")
            messagebox.showerror("建档失败", str(exc), parent=self)

    def close(self) -> None:
        self.running = False
        self.destroy()


class AdminWindow(tk.Toplevel):
    def __init__(self, app: "AttendanceApp"):
        super().__init__(app.root)
        self.app = app
        self.title("管理员控制台")
        self.geometry("900x570")
        self.transient(app.root)

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 4))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="新增员工", command=lambda: EnrollmentDialog(self)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="重新采集人脸", command=self.re_enroll).pack(side="left", padx=3)
        ttk.Button(toolbar, text="启用/停用", command=self.toggle_active).pack(side="left", padx=3)
        ttk.Button(toolbar, text="删除员工及人脸", command=self.delete_employee).pack(side="left", padx=3)
        ttk.Button(toolbar, text="导出考勤 CSV", command=self.export).pack(side="left", padx=3)
        ttk.Button(toolbar, text="备份数据库", command=self.backup).pack(side="left", padx=3)
        ttk.Button(toolbar, text="修改管理员密码", command=self.change_password).pack(side="left", padx=3)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=8)
        employee_tab = ttk.Frame(notebook)
        attendance_tab = ttk.Frame(notebook)
        notebook.add(employee_tab, text="员工")
        notebook.add(attendance_tab, text="最近考勤")

        self.employee_tree = ttk.Treeview(
            employee_tab,
            columns=("code", "name", "department", "active", "samples"),
            show="headings",
        )
        for key, title, width in (
            ("code", "员工编号", 140),
            ("name", "姓名", 130),
            ("department", "部门", 160),
            ("active", "状态", 80),
            ("samples", "样本数", 80),
        ):
            self.employee_tree.heading(key, text=title)
            self.employee_tree.column(key, width=width, anchor="center")
        self.employee_tree.pack(fill="both", expand=True)

        self.attendance_tree = ttk.Treeview(
            attendance_tab,
            columns=("code", "name", "event", "time", "distance", "device"),
            show="headings",
        )
        for key, title, width in (
            ("code", "员工编号", 130),
            ("name", "姓名", 120),
            ("event", "类型", 70),
            ("time", "打卡时间", 180),
            ("distance", "识别距离", 90),
            ("device", "设备", 170),
        ):
            self.attendance_tree.heading(key, text=title)
            self.attendance_tree.column(key, width=width, anchor="center")
        self.attendance_tree.pack(fill="both", expand=True)
        self.refresh_all()

    def selected_employee_id(self) -> int | None:
        selected = self.employee_tree.selection()
        return int(selected[0]) if selected else None

    def refresh_all(self) -> None:
        for item in self.employee_tree.get_children():
            self.employee_tree.delete(item)
        for row in self.app.database.list_employees():
            self.employee_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["employee_code"],
                    row["name"],
                    row["department"],
                    "启用" if row["active"] else "停用",
                    row["sample_count"],
                ),
            )
        for item in self.attendance_tree.get_children():
            self.attendance_tree.delete(item)
        for row in self.app.database.recent_attendance():
            display_time = datetime.fromisoformat(row["occurred_at"]).strftime("%Y-%m-%d %H:%M:%S")
            self.attendance_tree.insert(
                "",
                "end",
                values=(
                    row["employee_code"],
                    row["employee_name"],
                    row["event_type"],
                    display_time,
                    f"{row['confidence']:.1f}",
                    row["device_name"],
                ),
            )

    def toggle_active(self) -> None:
        employee_id = self.selected_employee_id()
        if employee_id is None:
            messagebox.showwarning("未选择员工", "请先选择一名员工。", parent=self)
            return
        row = self.app.database.employee(employee_id)
        self.app.database.set_employee_active(employee_id, not bool(row["active"]))
        self.app.engine.reload()
        self.refresh_all()

    def re_enroll(self) -> None:
        employee_id = self.selected_employee_id()
        if employee_id is None:
            messagebox.showwarning("未选择员工", "请先选择一名员工。", parent=self)
            return
        EnrollmentDialog(self, employee_id)

    def delete_employee(self) -> None:
        employee_id = self.selected_employee_id()
        if employee_id is None:
            messagebox.showwarning("未选择员工", "请先选择一名员工。", parent=self)
            return
        row = self.app.database.employee(employee_id)
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除 {row['employee_code']} {row['name']} 及全部人脸样本吗？\n"
            "历史考勤记录会保留，此操作不可撤销。",
            parent=self,
        ):
            return
        self.app.database.delete_employee(employee_id)
        self.app.engine.reload()
        self.refresh_all()

    def export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出考勤记录",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            initialfile=f"attendance-{datetime.now():%Y%m%d}.csv",
        )
        if not path:
            return
        try:
            count = self.app.database.export_attendance(Path(path))
            messagebox.showinfo("导出完成", f"已导出 {count} 条记录。", parent=self)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def change_password(self) -> None:
        first = simpledialog.askstring("修改密码", "输入新密码（至少 8 个字符）：", show="*", parent=self)
        if first is None:
            return
        second = simpledialog.askstring("修改密码", "再次输入新密码：", show="*", parent=self)
        if first != second:
            messagebox.showerror("修改失败", "两次输入的密码不一致。", parent=self)
            return
        try:
            self.app.database.set_setting("admin_password", hash_password(first))
            messagebox.showinfo("修改成功", "管理员密码已更新。", parent=self)
        except ValueError as exc:
            messagebox.showerror("修改失败", str(exc), parent=self)

    def backup(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="备份数据库",
            defaultextension=".db",
            filetypes=[("SQLite 数据库", "*.db")],
            initialfile=f"face-attendance-backup-{datetime.now():%Y%m%d}.db",
        )
        if not path:
            return
        try:
            self.app.database.backup(Path(path))
            messagebox.showinfo("备份完成", "数据库已安全备份。", parent=self)
        except OSError as exc:
            messagebox.showerror("备份失败", str(exc), parent=self)


class AttendanceApp:
    UPDATE_MS = 33

    def __init__(self, root: tk.Tk, database: Database, config: AppConfig):
        self.root = root
        self.database = database
        self.config = config
        self.engine = FaceEngine(database, config)
        self.cap = None
        self.source_name = "不可用"
        self.latest_frame = None
        self.matches: list[FaceMatch] = []
        self.frame_counter = 0
        self.stable_employee_id: int | None = None
        self.stable_count = 0
        self._photo = None
        self.login_failures = 0
        self.login_locked_until = 0.0
        self._open_camera()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(0, self.update_loop)

    def _open_camera(self) -> None:
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        for index in self.config.camera_indices:
            candidate = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if not candidate.isOpened():
                candidate.release()
                candidate = cv2.VideoCapture(index)
            if candidate.isOpened():
                candidate.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                candidate.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.cap = candidate
                self.source_name = f"摄像头 #{index}"
                return
            candidate.release()
        self.source_name = "不可用"

    def _build_ui(self) -> None:
        self.root.title("轻量化人脸考勤系统 1.2")
        self.root.geometry("980x610")
        self.root.minsize(900, 560)
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        self.video = tk.Label(main, bg="#111111", width=640, height=480)
        self.video.grid(row=0, column=0, rowspan=8, sticky="nsew", padx=(0, 16))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        ttk.Label(main, text="考勤终端", font=("Microsoft YaHei UI", 18, "bold")).grid(
            row=0, column=1, sticky="nw"
        )
        self.source_label = ttk.Label(main, text=f"视频源：{self.source_name}")
        self.source_label.grid(row=1, column=1, sticky="nw", pady=(10, 0))
        self.identity_label = ttk.Label(
            main, text="等待识别……", font=("Microsoft YaHei UI", 14), wraplength=260
        )
        self.identity_label.grid(row=2, column=1, sticky="nw", pady=(30, 8))
        self.status_label = tk.Label(
            main, text="请正对摄像头", fg="#555555", justify="left", wraplength=260
        )
        self.status_label.grid(row=3, column=1, sticky="nw", pady=8)
        checkin_panel = ttk.Frame(main)
        checkin_panel.grid(row=4, column=1, sticky="ew", pady=(15, 6))
        checkin_panel.columnconfigure((0, 1), weight=1)
        self.checkin_buttons = [
            ttk.Button(
                checkin_panel, text="上班打卡", command=lambda: self.check_in("上班"), state="disabled"
            ),
            ttk.Button(
                checkin_panel, text="下班打卡", command=lambda: self.check_in("下班"), state="disabled"
            ),
        ]
        self.checkin_buttons[0].grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.checkin_buttons[1].grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Button(main, text="重试摄像头", command=self.retry_camera).grid(
            row=5, column=1, sticky="ew", pady=6
        )
        ttk.Button(main, text="管理员", command=self.open_admin).grid(
            row=6, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            main,
            text="隐私提示：人脸样本仅存储在本机。此轻量版不含专业活体检测，"
            "仅适用于低风险或有人值守场景。",
            foreground="#7a5b00",
            wraplength=260,
        ).grid(row=7, column=1, sticky="sw", pady=(20, 0))

    def retry_camera(self) -> None:
        self._open_camera()
        self.source_label.configure(text=f"视频源：{self.source_name}")

    def _set_checkin_state(self, state: str) -> None:
        for button in self.checkin_buttons:
            button.configure(state=state)

    def update_loop(self) -> None:
        try:
            if self.cap is None:
                self.latest_frame = None
                self.identity_label.configure(text="摄像头不可用")
                self.status_label.configure(text="请检查设备权限或连接后点击“重试摄像头”", fg="red")
                self._set_checkin_state("disabled")
            else:
                ok, frame = self.cap.read()
                if not ok:
                    LOGGER.warning("Camera frame read failed")
                    self.cap.release()
                    self.cap = None
                    self.source_name = "读取失败"
                    self.source_label.configure(text="视频源：读取失败")
                else:
                    self.latest_frame = frame.copy()
                    self.frame_counter += 1
                    if self.frame_counter % self.config.detection_interval_frames == 0:
                        self.matches = self.engine.recognize(frame)
                        self._update_identity_state()
                    self._draw_matches(frame)
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(rgb)
                    image.thumbnail((640, 480), Image.Resampling.LANCZOS)
                    self._photo = ImageTk.PhotoImage(image)
                    self.video.configure(image=self._photo, width=image.width, height=image.height)
        except Exception:
            LOGGER.exception("Video update failed")
            self.status_label.configure(text="处理画面时发生错误，请查看日志", fg="red")
            self._set_checkin_state("disabled")
        finally:
            if self.root.winfo_exists():
                self.root.after(self.UPDATE_MS, self.update_loop)

    def _update_identity_state(self) -> None:
        if len(self.matches) != 1:
            self.stable_employee_id = None
            self.stable_count = 0
            self._set_checkin_state("disabled")
            if len(self.matches) > 1:
                self.identity_label.configure(text="检测到多人")
                self.status_label.configure(text="每次只允许一人打卡", fg="orange")
            else:
                self.identity_label.configure(text="等待识别……")
                self.status_label.configure(text="请正对摄像头", fg="#555555")
            return
        match = self.matches[0]
        if not match.recognized:
            self.stable_employee_id = None
            self.stable_count = 0
            self.identity_label.configure(text="未识别人员")
            self.status_label.configure(
                text=match.quality_issue or "请联系管理员完成人脸建档", fg="orange"
            )
            self._set_checkin_state("disabled")
            return
        if self.stable_employee_id == match.employee_id:
            self.stable_count += 1
        else:
            self.stable_employee_id = match.employee_id
            self.stable_count = 1
        self.identity_label.configure(text=f"{match.name}\n{match.employee_code}")
        self.status_label.configure(text=f"识别距离：{match.distance:.1f}（越低越可靠）", fg="green")
        self._set_checkin_state(
            "normal" if self.stable_count >= self.config.stable_recognition_cycles else "disabled"
        )

    def _draw_matches(self, frame) -> None:
        for match in self.matches:
            x, y, width, height = match.box
            color = (0, 180, 0) if match.recognized else (0, 165, 255)
            cv2.rectangle(frame, (x, y), (x + width, y + height), color, 2)
            text = match.employee_code if match.recognized else "UNKNOWN"
            cv2.putText(frame, text or "UNKNOWN", (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

    def check_in(self, event_type: str) -> None:
        if (
            len(self.matches) != 1
            or not self.matches[0].recognized
            or self.stable_count < self.config.stable_recognition_cycles
        ):
            self.status_label.configure(text="身份尚未稳定识别，请稍候", fg="orange")
            return
        match = self.matches[0]
        try:
            accepted, message = self.database.check_in(
                match.employee_id,
                event_type,
                match.distance,
                self.config.device_name,
                self.config.duplicate_interval_seconds,
            )
            if accepted:
                displayed = datetime.fromisoformat(message).strftime("%Y-%m-%d %H:%M:%S")
                self.status_label.configure(text=f"{event_type}成功\n{displayed}", fg="green")
                LOGGER.info("Check-in accepted: employee=%s event=%s", match.employee_code, event_type)
            else:
                self.status_label.configure(text=message, fg="orange")
        except Exception:
            LOGGER.exception("Check-in failed")
            self.status_label.configure(text="打卡写入失败，请联系管理员", fg="red")

    def open_admin(self) -> None:
        remaining = int(self.login_locked_until - time.monotonic())
        if remaining > 0:
            messagebox.showwarning("暂时锁定", f"登录失败次数过多，请 {remaining} 秒后重试。")
            return
        password = simpledialog.askstring("管理员登录", "请输入管理员密码：", show="*", parent=self.root)
        if password is None:
            return
        encoded = self.database.get_setting("admin_password") or ""
        if not verify_password(password, encoded):
            self.login_failures += 1
            if self.login_failures >= 5:
                self.login_locked_until = time.monotonic() + 30
                self.login_failures = 0
            LOGGER.warning("Administrator login failed")
            messagebox.showerror("登录失败", "管理员密码错误。")
            return
        self.login_failures = 0
        AdminWindow(self)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.root.destroy()


def setup_first_password(root: tk.Tk, database: Database) -> bool:
    if database.get_setting("admin_password"):
        return True
    messagebox.showinfo(
        "首次启动",
        "请先设置管理员密码。该密码用于员工建档、数据导出和隐私数据管理。",
        parent=root,
    )
    while True:
        first = simpledialog.askstring("设置管理员密码", "输入密码（至少 8 个字符）：", show="*", parent=root)
        if first is None:
            return False
        second = simpledialog.askstring("设置管理员密码", "再次输入密码：", show="*", parent=root)
        if second is None:
            return False
        if first != second:
            messagebox.showerror("设置失败", "两次输入的密码不一致。", parent=root)
            continue
        try:
            validate_password(first)
            database.set_setting("admin_password", hash_password(first))
            return True
        except ValueError as exc:
            messagebox.showerror("设置失败", str(exc), parent=root)
