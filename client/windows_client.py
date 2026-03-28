import json
import platform
import sys
import uuid
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import requests

try:
    import winsound
except ImportError:
    winsound = None

try:
    from plyer import notification as plyer_notification
except Exception:
    plyer_notification = None


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_api_base() -> str:
    cfg_path = _app_dir() / "config.json"
    if cfg_path.is_file():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        base = str(data.get("api_base", "")).strip().rstrip("/")
        if base:
            return base
    if getattr(sys, "frozen", False):
        raise RuntimeError("缺少或无效的 config.json（需要 api_base 字段）")
    return "http://127.0.0.1:8000"


def get_device_id() -> str:
    return f"{platform.node()}-{uuid.getnode()}"


def play_notice_sound() -> None:
    if winsound:
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            try:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except Exception:
                pass


def desktop_notify(title: str, message: str) -> None:
    if plyer_notification:
        try:
            plyer_notification.notify(
                title=title,
                message=message,
                app_name="抖音点赞监控",
                timeout=12,
            )
            return
        except Exception:
            pass
    messagebox.showinfo(title, message)


class App:
    def __init__(self, root: tk.Tk, api_base: str):
        self.root = root
        self.api_base = api_base.rstrip("/")
        self.root.title("企业版抖音点赞监控客户端")
        self.root.geometry("1100x780")
        self.token = None
        self._seen_alert_ids: set[int] = set()

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.interval_min_var = tk.StringVar(value="180")
        self.interval_max_var = tk.StringVar(value="480")
        self.status_var = tk.StringVar(value="状态：未登录")

        self._build()
        self.root.after(8000, self._tick_logs)
        self.root.after(10000, self._tick_alerts)

    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="账号").pack(side="left")
        ttk.Entry(top, textvariable=self.username_var, width=16).pack(side="left", padx=(8, 12))
        ttk.Label(top, text="密码").pack(side="left")
        ttk.Entry(top, textvariable=self.password_var, show="*", width=18).pack(
            side="left", padx=(8, 12)
        )
        ttk.Button(top, text="登录", command=self.login).pack(side="left")

        ctrl = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        ctrl.pack(fill="x")
        ttk.Button(ctrl, text="测试系统通知", command=self.test_notification).pack(side="left", **pad)
        ttk.Button(ctrl, text="开始监控", command=self.monitor_start).pack(side="left", **pad)
        ttk.Button(ctrl, text="暂停监控", command=self.monitor_pause).pack(side="left", **pad)
        ttk.Button(ctrl, text="停止监控", command=self.monitor_stop).pack(side="left", **pad)
        ttk.Button(ctrl, text="刷新监控状态", command=self.refresh_monitor_status).pack(
            side="left", **pad
        )

        iv = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        iv.pack(fill="x")
        ttk.Label(iv, text="检测间隔最小(秒)").pack(side="left")
        ttk.Entry(iv, textvariable=self.interval_min_var, width=8).pack(side="left", padx=(6, 16))
        ttk.Label(iv, text="最大(秒)").pack(side="left")
        ttk.Entry(iv, textvariable=self.interval_max_var, width=8).pack(side="left", padx=(6, 16))
        ttk.Button(iv, text="应用间隔", command=self.apply_interval).pack(side="left", **pad)

        task_form = ttk.Frame(self.root, padding=12)
        task_form.pack(fill="x")
        ttk.Label(task_form, text="名称").pack(side="left")
        ttk.Entry(task_form, textvariable=self.name_var, width=14).pack(side="left", padx=(8, 8))
        ttk.Label(task_form, text="视频链接").pack(side="left")
        ttk.Entry(task_form, textvariable=self.url_var, width=52).pack(side="left", padx=(8, 8))
        ttk.Label(task_form, text="目标点赞").pack(side="left")
        ttk.Entry(task_form, textvariable=self.target_var, width=10).pack(side="left", padx=(8, 8))
        ttk.Button(task_form, text="新增任务", command=self.create_task).pack(side="left", **pad)
        ttk.Button(task_form, text="刷新列表", command=self.refresh_tasks).pack(side="left", **pad)
        ttk.Button(task_form, text="更新选中", command=self.update_selected_task).pack(
            side="left", **pad
        )
        ttk.Button(task_form, text="删除选中", command=self.delete_selected_task).pack(
            side="left", **pad
        )

        table_wrap = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        table_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table_wrap,
            columns=("id", "name", "url", "target", "enabled"),
            show="headings",
            height=12,
        )
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="名称")
        self.tree.heading("url", text="视频链接")
        self.tree.heading("target", text="目标点赞")
        self.tree.heading("enabled", text="启用")
        self.tree.column("id", width=50)
        self.tree.column("name", width=120)
        self.tree.column("url", width=560)
        self.tree.column("target", width=90)
        self.tree.column("enabled", width=70)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_task)

        log_fr = ttk.LabelFrame(self.root, text="监控日志（服务端检测记录）", padding=(8, 4))
        log_fr.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.log_text = scrolledtext.ScrolledText(log_fr, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")

    def _headers(self):
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def test_notification(self) -> None:
        play_notice_sound()
        desktop_notify("测试通知", "若你看到本提示并听到系统提示音，则达标提醒可用。")

    def login(self):
        payload = {
            "username": self.username_var.get().strip(),
            "password": self.password_var.get().strip(),
            "device_id": get_device_id(),
            "device_name": platform.platform(),
        }
        try:
            r = requests.post(f"{self.api_base}/auth/login", json=payload, timeout=15)
            if r.status_code != 200:
                messagebox.showerror("登录失败", r.text)
                return
            self.token = r.json()["access_token"]
            self._seen_alert_ids.clear()
            self.status_var.set("状态：已登录")
            self.refresh_tasks()
            self.refresh_monitor_status()
            self._tick_logs()
        except Exception as e:
            messagebox.showerror("登录失败", str(e))

    def refresh_monitor_status(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            r = requests.get(
                f"{self.api_base}/monitor/status", headers=self._headers(), timeout=15
            )
            if r.status_code != 200:
                messagebox.showerror("状态", r.text)
                return
            d = r.json()
            imin, imax = d.get("interval_min_sec"), d.get("interval_max_sec")
            if imin is not None:
                self.interval_min_var.set(str(imin))
            if imax is not None:
                self.interval_max_var.set(str(imax))
            g = "全局调度运行中" if d.get("global_scheduler_running") else "全局调度未运行（联系管理员）"
            if d.get("monitoring_paused"):
                m = "你的监控：已暂停"
            elif d.get("monitoring_active"):
                m = "你的监控：运行中"
            else:
                m = "你的监控：已停止"
            self.status_var.set(f"状态：已登录 | {m} | {g}")
        except Exception as e:
            messagebox.showerror("状态", str(e))

    def apply_interval(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            mn = int(self.interval_min_var.get().strip())
            mx = int(self.interval_max_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "间隔须为整数（秒）")
            return
        try:
            r = requests.patch(
                f"{self.api_base}/monitor/settings",
                json={"interval_min_sec": mn, "interval_max_sec": mx},
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code != 200:
                messagebox.showerror("保存失败", r.text)
                return
            messagebox.showinfo("成功", "检测间隔已保存（服务端按随机区间调度，降低风控特征）。")
            self.refresh_monitor_status()
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def monitor_start(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            r = requests.post(f"{self.api_base}/monitor/start", headers=self._headers(), timeout=15)
            if r.status_code != 200:
                messagebox.showerror("开始监控", r.text)
                return
            self.refresh_monitor_status()
        except Exception as e:
            messagebox.showerror("开始监控", str(e))

    def monitor_pause(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            r = requests.post(f"{self.api_base}/monitor/pause", headers=self._headers(), timeout=15)
            if r.status_code != 200:
                messagebox.showerror("暂停", r.text)
                return
            self.refresh_monitor_status()
        except Exception as e:
            messagebox.showerror("暂停", str(e))

    def monitor_stop(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            r = requests.post(f"{self.api_base}/monitor/stop", headers=self._headers(), timeout=15)
            if r.status_code != 200:
                messagebox.showerror("停止", r.text)
                return
            self.refresh_monitor_status()
        except Exception as e:
            messagebox.showerror("停止", str(e))

    def _append_log_lines(self, lines: list[str]) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", "\n".join(lines))
        self.log_text.configure(state="disabled")

    def _tick_logs(self):
        if self.token:
            try:
                r = requests.get(
                    f"{self.api_base}/my/records",
                    params={"limit": 120},
                    headers=self._headers(),
                    timeout=20,
                )
                if r.status_code == 200:
                    rows = r.json()
                    lines = []
                    for row in rows:
                        ts = str(row.get("checked_at", ""))[:19]
                        ok = "OK" if row.get("success") else "失败"
                        likes = row.get("likes")
                        err = (row.get("error_message") or "").strip()
                        name = row.get("task_name", "")
                        tid = row.get("task_id")
                        if row.get("success"):
                            lines.append(
                                f"{ts}  任务#{tid} {name}  点赞={likes}  {ok}"
                            )
                        else:
                            lines.append(
                                f"{ts}  任务#{tid} {name}  {ok}  {err[:80]}"
                            )
                    self._append_log_lines(lines)
            except Exception:
                pass
        self.root.after(8000, self._tick_logs)

    def _tick_alerts(self):
        if self.token:
            try:
                r = requests.get(
                    f"{self.api_base}/alerts/unread",
                    headers=self._headers(),
                    timeout=15,
                )
                if r.status_code == 200:
                    for a in r.json():
                        aid = int(a["id"])
                        if aid in self._seen_alert_ids:
                            continue
                        self._seen_alert_ids.add(aid)
                        title = "点赞已达目标"
                        msg = (
                            f"{a.get('task_name','')} 当前 {a.get('likes')} "
                            f"/ 目标 {a.get('target_likes')}"
                        )
                        play_notice_sound()
                        desktop_notify(title, msg)
                        requests.post(
                            f"{self.api_base}/alerts/{aid}/ack",
                            headers=self._headers(),
                            timeout=10,
                        )
            except Exception:
                pass
        self.root.after(10000, self._tick_alerts)

    def refresh_tasks(self):
        if not self.token:
            return
        try:
            r = requests.get(f"{self.api_base}/tasks", headers=self._headers(), timeout=15)
            if r.status_code != 200:
                messagebox.showerror("获取任务失败", r.text)
                return
            for row in self.tree.get_children():
                self.tree.delete(row)
            for item in r.json():
                self.tree.insert(
                    "",
                    "end",
                    values=(
                        item["id"],
                        item["name"],
                        item["video_url"],
                        item["target_likes"],
                        item["enabled"],
                    ),
                )
        except Exception as e:
            messagebox.showerror("获取任务失败", str(e))

    def create_task(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            target = int(self.target_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "目标点赞必须是整数")
            return
        payload = {
            "name": self.name_var.get().strip() or "未命名任务",
            "video_url": self.url_var.get().strip(),
            "target_likes": target,
            "enabled": True,
        }
        try:
            r = requests.post(
                f"{self.api_base}/tasks", json=payload, headers=self._headers(), timeout=15
            )
            if r.status_code != 200:
                messagebox.showerror("创建失败", r.text)
                return
            self.refresh_tasks()
        except Exception as e:
            messagebox.showerror("创建失败", str(e))

    def on_select_task(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if len(values) < 5:
            return
        self.name_var.set(str(values[1]))
        self.url_var.set(str(values[2]))
        self.target_var.set(str(values[3]))

    def update_selected_task(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一个任务")
            return
        values = self.tree.item(selected[0], "values")
        task_id = int(values[0])
        try:
            target = int(self.target_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "目标点赞必须是整数")
            return
        payload = {
            "name": self.name_var.get().strip() or "未命名任务",
            "video_url": self.url_var.get().strip(),
            "target_likes": target,
        }
        try:
            r = requests.patch(
                f"{self.api_base}/tasks/{task_id}",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code != 200:
                messagebox.showerror("更新失败", r.text)
                return
            self.refresh_tasks()
        except Exception as e:
            messagebox.showerror("更新失败", str(e))

    def delete_selected_task(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一个任务")
            return
        values = self.tree.item(selected[0], "values")
        task_id = int(values[0])
        if not messagebox.askyesno("确认删除", f"确定删除任务 #{task_id} 吗？"):
            return
        try:
            r = requests.delete(
                f"{self.api_base}/tasks/{task_id}",
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code != 200:
                messagebox.showerror("删除失败", r.text)
                return
            self.refresh_tasks()
        except Exception as e:
            messagebox.showerror("删除失败", str(e))


def main():
    try:
        api_base = load_api_base()
    except Exception as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("配置错误", str(e))
        return
    root = tk.Tk()
    App(root, api_base)
    root.mainloop()


if __name__ == "__main__":
    main()
