import json
import os
import platform
import random
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List

import requests
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from pync import Notifier
except Exception:
    Notifier = None

try:
    from plyer import notification as plyer_notification
except Exception:
    plyer_notification = None

from douyin_fetch import fetch_likes, normalize_douyin_url

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"


def short_num(num: int) -> str:
    if num >= 100000000:
        return f"{num / 100000000:.2f}亿"
    if num >= 10000:
        return f"{num / 10000:.2f}万"
    return str(num)


def send_macos_notification(title: str, message: str) -> bool:
    if sys.platform.startswith("win"):
        if plyer_notification is None:
            return False
        try:
            plyer_notification.notify(
                title=title,
                message=message,
                app_name="抖音点赞监控",
                timeout=10,
            )
            return True
        except Exception:
            return False

    if sys.platform != "darwin":
        return False

    if Notifier is not None:
        try:
            Notifier.notify(
                message,
                title=title,
                sound="Glass",
                group="douyin-like-monitor",
                appIcon="https://www.douyin.com/favicon.ico",
            )
            return True
        except Exception:
            pass

    # AppleScript 字符串里的双引号需要转义，避免通知命令失效。
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_message = message.replace("\\", "\\\\").replace('"', '\\"')

    scripts = [
        f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"',
        (
            'tell application "System Events" to '
            f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'
        ),
    ]

    for script in scripts:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
    return False


class MonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("抖音点赞监控")
        self.root.geometry("1100x680")

        self.rows: List[Dict[str, tk.Widget]] = []
        self.running = False
        self.stop_event = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.reached_once: set[str] = set()
        self.last_likes: Dict[str, int] = {}
        self.auth_token: str | None = None
        self.api_base_var = tk.StringVar(
            value=os.getenv("DOUYIN_AUTH_API", "http://127.0.0.1:8000").rstrip("/")
        )

        self.interval_var = tk.StringVar(value="60")
        self.rand_min_var = tk.StringVar(value="120")
        self.rand_max_var = tk.StringVar(value="420")
        self.batch_size_var = tk.StringVar(value="8")
        self.alert_cooldown_var = tk.StringVar(value="0")
        self.repeat_alert_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="状态：未开始")
        self.auth_status_var = tk.StringVar(value="登录状态：未登录")
        self.login_user_var = tk.StringVar(value="")
        self.login_pass_var = tk.StringVar(value="")
        self.insecure_ssl_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._load_config_if_exists()
        self._set_operate_enabled(False)
        self.root.withdraw()
        self._show_login_dialog()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")

        row1 = ttk.Frame(top)
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="检测间隔（秒）:").pack(side="left")
        ttk.Entry(row1, textvariable=self.interval_var, width=8).pack(side="left", padx=(8, 20))
        ttk.Label(row1, text="随机区间:").pack(side="left")
        ttk.Entry(row1, textvariable=self.rand_min_var, width=6).pack(side="left", padx=(6, 4))
        ttk.Label(row1, text="-").pack(side="left")
        ttk.Entry(row1, textvariable=self.rand_max_var, width=6).pack(side="left", padx=(4, 12))
        ttk.Label(row1, text="分片数:").pack(side="left")
        ttk.Entry(row1, textvariable=self.batch_size_var, width=5).pack(side="left", padx=(6, 12))
        ttk.Label(row1, text="提醒冷却秒:").pack(side="left")
        ttk.Entry(row1, textvariable=self.alert_cooldown_var, width=6).pack(side="left", padx=(6, 12))

        row2 = ttk.Frame(top)
        row2.pack(fill="x")
        self.btn_add_row = ttk.Button(row2, text="新增一行", command=self.add_row)
        self.btn_add_row.pack(side="left")
        self.btn_save = ttk.Button(row2, text="保存配置", command=self.save_config)
        self.btn_save.pack(side="left", padx=8)
        self.btn_test_notify = ttk.Button(row2, text="测试系统通知", command=self.test_system_notification)
        self.btn_test_notify.pack(
            side="left", padx=(0, 8)
        )
        self.btn_start = ttk.Button(row2, text="开始监控", command=self.start_monitor)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row2, text="停止监控", command=self.stop_monitor)
        self.btn_stop.pack(side="left", padx=8)
        self.chk_ssl = ttk.Checkbutton(
            row2,
            text="代理环境兼容（跳过SSL校验）",
            variable=self.insecure_ssl_var,
        )
        self.chk_ssl.pack(side="left", padx=(12, 0))
        self.chk_repeat = ttk.Checkbutton(
            row2,
            text="达标后每次都提醒",
            variable=self.repeat_alert_var,
        )
        self.chk_repeat.pack(side="left", padx=(8, 0))

        table_wrap = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        table_wrap.pack(fill="both", expand=True)

        header = ttk.Frame(table_wrap)
        header.pack(fill="x", pady=(10, 6))
        ttk.Label(header, text="名称", width=16).pack(side="left")
        ttk.Label(header, text="抖音链接", width=56).pack(side="left", padx=(8, 8))
        ttk.Label(header, text="目标点赞", width=12).pack(side="left")
        ttk.Label(header, text="操作", width=8).pack(side="left", padx=(8, 0))

        self.rows_frame = ttk.Frame(table_wrap)
        self.rows_frame.pack(fill="both", expand=True)

        log_wrap = ttk.Frame(self.root, padding=12)
        log_wrap.pack(fill="both", expand=True)
        ttk.Label(log_wrap, textvariable=self.status_var).pack(anchor="w", pady=(0, 6))
        ttk.Label(log_wrap, textvariable=self.auth_status_var).pack(anchor="w", pady=(0, 6))

        self.log_text = tk.Text(log_wrap, height=12)
        self.log_text.pack(fill="both", expand=True)
        self.log("界面已就绪。你可以新增多条链接并设置目标点赞。")

        if not self.rows:
            self.add_row()

    def add_row(self, name: str = "", url: str = "", target: str = "") -> None:
        row_wrap = ttk.Frame(self.rows_frame)
        row_wrap.pack(fill="x", pady=4)

        name_var = tk.StringVar(value=name)
        url_var = tk.StringVar(value=url)
        target_var = tk.StringVar(value=target)

        name_entry = ttk.Entry(row_wrap, textvariable=name_var, width=20)
        name_entry.pack(side="left")
        url_entry = ttk.Entry(row_wrap, textvariable=url_var, width=70)
        url_entry.pack(side="left", padx=(8, 8))
        target_entry = ttk.Entry(row_wrap, textvariable=target_var, width=12)
        target_entry.pack(side="left")

        btn = ttk.Button(row_wrap, text="删除", command=lambda: self.remove_row(row_wrap))
        btn.pack(side="left", padx=(8, 0))

        self.rows.append(
            {
                "frame": row_wrap,
                "name_var": name_var,
                "url_var": url_var,
                "target_var": target_var,
            }
        )

    def remove_row(self, frame: ttk.Frame) -> None:
        self.rows = [r for r in self.rows if r["frame"] != frame]
        frame.destroy()

    def _collect_videos(self) -> List[Dict[str, object]]:
        videos: List[Dict[str, object]] = []
        for i, row in enumerate(self.rows, start=1):
            name = row["name_var"].get().strip() or f"视频{i}"
            url = normalize_douyin_url(row["url_var"].get())
            target_raw = row["target_var"].get().strip()
            if not url:
                continue
            if not target_raw.isdigit() or int(target_raw) <= 0:
                raise ValueError(f"{name} 的目标点赞必须是大于 0 的整数")
            videos.append({"name": name, "url": url, "target_likes": int(target_raw)})
        if not videos:
            raise ValueError("请至少填写一条有效的抖音链接")
        return videos

    def save_config(self) -> None:
        try:
            interval = int(self.interval_var.get().strip())
            if interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("配置错误", "检测间隔必须是大于 0 的整数秒")
            return

        try:
            videos = self._collect_videos()
        except ValueError as e:
            messagebox.showerror("配置错误", str(e))
            return

        payload = {
            "check_interval_seconds": interval,
            "insecure_ssl": bool(self.insecure_ssl_var.get()),
            "auth_api_base": self.api_base_var.get().strip(),
            "random_interval_min_seconds": int(self.rand_min_var.get().strip() or 120),
            "random_interval_max_seconds": int(self.rand_max_var.get().strip() or 420),
            "batch_size": int(self.batch_size_var.get().strip() or 8),
            "repeat_alert": bool(self.repeat_alert_var.get()),
            "alert_cooldown_seconds": int(self.alert_cooldown_var.get().strip() or 0),
            "videos": videos,
        }
        DEFAULT_CONFIG.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.log(f"配置已保存：{DEFAULT_CONFIG}")
        messagebox.showinfo("保存成功", "配置已保存")

    def _load_config_if_exists(self) -> None:
        if not DEFAULT_CONFIG.exists():
            self.log("未找到配置文件，已使用默认配置（代理兼容默认开启）。")
            return
        try:
            data = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
            self.interval_var.set(str(int(data.get("check_interval_seconds", 60))))
            self.insecure_ssl_var.set(bool(data.get("insecure_ssl", False)))
            self.api_base_var.set(str(data.get("auth_api_base", self.api_base_var.get())).rstrip("/"))
            self.rand_min_var.set(str(int(data.get("random_interval_min_seconds", 120))))
            self.rand_max_var.set(str(int(data.get("random_interval_max_seconds", 420))))
            self.batch_size_var.set(str(int(data.get("batch_size", 8))))
            self.repeat_alert_var.set(bool(data.get("repeat_alert", True)))
            self.alert_cooldown_var.set(str(int(data.get("alert_cooldown_seconds", 0))))

            for row in self.rows:
                row["frame"].destroy()
            self.rows.clear()

            for item in data.get("videos", []):
                self.add_row(
                    str(item.get("name", "")),
                    str(item.get("url", "")),
                    str(item.get("target_likes", "")),
                )
            if not self.rows:
                self.add_row()
            self.log("已加载现有配置。")
        except Exception as e:
            self.log(f"加载配置失败：{e}")

    def start_monitor(self) -> None:
        if not self.auth_token:
            messagebox.showerror("未授权", "请先登录管理员发放的账号密码")
            return
        if self.running:
            messagebox.showinfo("提示", "监控已经在运行中")
            return

        try:
            interval = int(self.interval_var.get().strip())
            if interval <= 0:
                raise ValueError
            rand_min = int(self.rand_min_var.get().strip())
            rand_max = int(self.rand_max_var.get().strip())
            batch_size = int(self.batch_size_var.get().strip())
            alert_cooldown = int(self.alert_cooldown_var.get().strip())
            if rand_min <= 0 or rand_max <= 0 or batch_size <= 0 or alert_cooldown < 0:
                raise ValueError
            videos = self._collect_videos()
        except ValueError as e:
            messagebox.showerror("配置错误", str(e))
            return

        if rand_min > rand_max:
            rand_min, rand_max = rand_max, rand_min

        payload = {
            "check_interval_seconds": interval,
            "insecure_ssl": bool(self.insecure_ssl_var.get()),
            "auth_api_base": self.api_base_var.get().strip(),
            "random_interval_min_seconds": rand_min,
            "random_interval_max_seconds": rand_max,
            "batch_size": batch_size,
            "repeat_alert": bool(self.repeat_alert_var.get()),
            "alert_cooldown_seconds": alert_cooldown,
            "videos": videos,
        }
        DEFAULT_CONFIG.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.reached_once.clear()
        self.stop_event.clear()
        self.running = True
        self.status_var.set("状态：监控中")
        self.log("监控已启动。")

        self.monitor_thread = threading.Thread(
            target=self._monitor_worker,
            args=(
                interval,
                videos,
                bool(self.insecure_ssl_var.get()),
                rand_min,
                rand_max,
                batch_size,
                bool(self.repeat_alert_var.get()),
                alert_cooldown,
            ),
            daemon=True,
        )
        self.monitor_thread.start()

    def stop_monitor(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.running = False
        self.status_var.set("状态：已停止")
        self.log("监控已停止。")

    def _monitor_worker(
        self,
        interval: int,
        videos: List[Dict[str, object]],
        insecure_ssl: bool,
        rand_min: int,
        rand_max: int,
        batch_size: int,
        repeat_alert: bool,
        alert_cooldown: int,
    ) -> None:
        video_state: Dict[str, Dict[str, float | int]] = {}
        for v in videos:
            video_state[str(v["url"])] = {
                "next_run_at": 0.0,
                "fail_count": 0,
                "last_alert_at": 0.0,
            }
        cursor = 0

        while not self.stop_event.is_set():
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            self.root.after(0, lambda n=now: self.log(f"\n[{n}] 开始新一轮检测..."))
            total = len(videos)
            step = max(1, min(batch_size, total))
            if cursor >= total:
                cursor = 0
            batch = videos[cursor : cursor + step]
            if len(batch) < step:
                batch += videos[: step - len(batch)]
            cursor = (cursor + step) % total

            for item in batch:
                if self.stop_event.is_set():
                    break
                name = str(item["name"])
                url = str(item["url"])
                target_likes = int(item["target_likes"])
                state = video_state[url]
                now_ts = time.time()
                if now_ts < float(state["next_run_at"]):
                    continue
                try:
                    likes = fetch_likes(url, insecure_ssl=insecure_ssl)
                    self.last_likes[url] = likes
                    progress = f"{short_num(likes)}/{short_num(target_likes)}"
                    self.root.after(
                        0, lambda n=name, l=likes, p=progress: self.log(f"- {n}: 当前点赞 {l} ({p})")
                    )
                    state["fail_count"] = 0
                    state["next_run_at"] = now_ts + random.randint(rand_min, rand_max)

                    should_alert = False
                    if likes >= target_likes:
                        if repeat_alert:
                            last_alert_at = float(state["last_alert_at"])
                            if alert_cooldown == 0 or now_ts - last_alert_at >= alert_cooldown:
                                should_alert = True
                        elif url not in self.reached_once:
                            should_alert = True
                            self.reached_once.add(url)

                    if should_alert:
                        state["last_alert_at"] = now_ts
                        msg = f"{name} 点赞达到 {short_num(likes)}，已超过目标 {short_num(target_likes)}"
                        self.root.after(0, lambda m=msg: self.log(f"  -> 触发提醒: {m}"))
                        self.root.after(0, lambda m=msg: self._notify_user(m))
                except Exception as e:
                    state["fail_count"] = int(state["fail_count"]) + 1
                    backoff = min(1800, (2 ** int(state["fail_count"])) * 30)
                    state["next_run_at"] = now_ts + backoff
                    self.root.after(0, lambda n=name, err=e: self.log(f"- {n}: 检测失败: {err}"))
            sleep_sec = min(5, interval)
            for _ in range(sleep_sec):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        self.root.after(0, self._mark_stopped_from_worker)

    def _mark_stopped_from_worker(self) -> None:
        self.running = False
        self.status_var.set("状态：已停止")

    def _notify_user(self, msg: str) -> None:
        # 优先系统通知（后台也可见）；失败时回退应用内弹窗。
        notified = send_macos_notification("抖音点赞监控提醒", msg)
        self.root.bell()
        if not notified:
            messagebox.showinfo("抖音点赞监控提醒", msg)
            self.log("系统通知发送失败，已使用应用内弹窗提醒。")

    def test_system_notification(self) -> None:
        msg = "这是一条测试通知。如果你在桌面右上角看到了它，说明系统通知正常。"
        notified = send_macos_notification("抖音点赞监控测试", msg)
        self.root.bell()
        if notified:
            self.log("测试通知已发送，请查看系统通知中心。")
        else:
            self.log("测试通知发送失败，系统将使用应用内弹窗提示。")
            messagebox.showinfo("抖音点赞监控测试", msg)

    def log(self, text: str) -> None:
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def _set_operate_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_add_row.config(state=state)
        self.btn_save.config(state=state)
        self.btn_test_notify.config(state=state)
        self.btn_start.config(state=state)
        self.btn_stop.config(state=state)
        self.chk_ssl.config(state=state)
        self.chk_repeat.config(state=state)

    def login_account(self) -> None:
        username = self.login_user_var.get().strip()
        password = self.login_pass_var.get().strip()
        ok, msg = self._perform_login(username, password, self.api_base_var.get().strip())
        if ok:
            self.auth_status_var.set(f"登录状态：已登录（{username}）")
            self._set_operate_enabled(True)
            self.log(f"账号 {username} 登录成功，服务端: {self.api_base_var.get()}")
        else:
            self.auth_token = None
            self.auth_status_var.set("登录状态：登录失败")
            self._set_operate_enabled(False)
            messagebox.showerror("登录失败", msg)

    def _perform_login(self, username: str, password: str, api_base: str) -> tuple[bool, str]:
        if not username or not password:
            return False, "请输入账号和密码"
        payload = {
            "username": username,
            "password": password,
            "device_id": f"{platform.node()}-{uuid.getnode()}",
            "device_name": platform.platform(),
        }
        try:
            api_base = api_base.strip().rstrip("/")
            if not api_base.startswith("http://") and not api_base.startswith("https://"):
                api_base = "http://" + api_base
            r = requests.post(f"{api_base}/auth/login", json=payload, timeout=15)
            if r.status_code != 200:
                return False, r.text
            self.auth_token = r.json()["access_token"]
            self.api_base_var.set(api_base)
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _show_login_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("登录授权")
        dlg.geometry("480x230")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", self.root.destroy)

        wrap = ttk.Frame(dlg, padding=16)
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="请先登录账号密码，登录后进入监控操作界面").pack(
            anchor="w", pady=(0, 10)
        )

        line1 = ttk.Frame(wrap)
        line1.pack(fill="x", pady=4)
        ttk.Label(line1, text="服务端地址", width=10).pack(side="left")
        entry_api = ttk.Entry(line1, textvariable=self.api_base_var)
        entry_api.pack(side="left", fill="x", expand=True)

        line2 = ttk.Frame(wrap)
        line2.pack(fill="x", pady=4)
        ttk.Label(line2, text="账号", width=10).pack(side="left")
        entry_user = ttk.Entry(line2, textvariable=self.login_user_var)
        entry_user.pack(side="left", fill="x", expand=True)

        line3 = ttk.Frame(wrap)
        line3.pack(fill="x", pady=4)
        ttk.Label(line3, text="密码", width=10).pack(side="left")
        entry_pwd = ttk.Entry(line3, textvariable=self.login_pass_var, show="*")
        entry_pwd.pack(side="left", fill="x", expand=True)

        dlg_status = tk.StringVar(value="未登录")
        ttk.Label(wrap, textvariable=dlg_status).pack(anchor="w", pady=(8, 6))

        def submit_login(_event=None):
            ok, msg = self._perform_login(
                self.login_user_var.get().strip(),
                self.login_pass_var.get().strip(),
                self.api_base_var.get().strip(),
            )
            if ok:
                username = self.login_user_var.get().strip()
                self.auth_status_var.set(f"登录状态：已登录（{username}）")
                self._set_operate_enabled(True)
                self.log(f"账号 {username} 登录成功，服务端: {self.api_base_var.get()}")
                dlg.destroy()
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            else:
                dlg_status.set(f"登录失败：{msg}")

        btn_row = ttk.Frame(wrap)
        btn_row.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_row, text="登录", command=submit_login).pack(side="left")
        ttk.Button(btn_row, text="退出", command=self.root.destroy).pack(side="left", padx=8)

        entry_pwd.bind("<Return>", submit_login)
        entry_api.focus_set()


def main() -> None:
    root = tk.Tk()
    app = MonitorApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_monitor(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
