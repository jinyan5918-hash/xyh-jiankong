import json
import platform
import re
import sys
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import requests

try:
    from zoneinfo import ZoneInfo

    _TZ_CN = ZoneInfo("Asia/Shanghai")
except Exception:
    _TZ_CN = None

try:
    import winsound
except ImportError:
    winsound = None

try:
    from plyer import notification as plyer_notification
except Exception:
    plyer_notification = None

try:
    import ctypes
except ImportError:
    ctypes = None

# 与 client/release_version.txt 保持一致；若打包未带入该文件，标题仍显示此版本（发版请两处同改）
CLIENT_VERSION_FALLBACK = "1.2.0"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_client_version() -> str:
    """与 release_version.txt 同步；发新版时编辑 client/release_version.txt 并重新打包。"""
    bases: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass))
        bases.append(Path(sys.executable).resolve().parent)
    bases.append(Path(__file__).resolve().parent)
    for base in bases:
        p = base / "release_version.txt"
        try:
            if p.is_file():
                lines = p.read_text(encoding="utf-8").strip().splitlines()
                if lines and lines[0].strip():
                    return lines[0].strip()
        except Exception:
            pass
    return CLIENT_VERSION_FALLBACK


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


def normalize_task_url(raw: str) -> str:
    """与服务器 urlnorm 一致，减少错误链接。"""
    import urllib.parse

    url = raw.strip()
    url = re.sub(r"[\u200b-\u200d\uFEFF\r\n\t ]+", "", url)
    url = url.replace("https://v.https://", "https://")
    url = url.replace("http://v.https://", "https://")
    url = url.replace("https://v.http://", "http://")
    while "https://https://" in url:
        url = url.replace("https://https://", "https://", 1)
    while "http://https://" in url:
        url = url.replace("http://https://", "https://", 1)
    if not url.startswith("http://") and not url.startswith("https://"):
        if url.startswith("//"):
            url = "https:" + url
        else:
            url = "https://" + url
    parsed = urllib.parse.urlsplit(url)
    if not parsed.netloc:
        raise ValueError("链接格式无效，请使用抖音视频分享页或 v.douyin.com 短链")
    return urllib.parse.urlunsplit(parsed)


def format_log_time(iso_val) -> str:
    """服务端多为 UTC；统一显示为北京时间（优先 Asia/Shanghai，否则 UTC+8）。"""
    s = str(iso_val).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if _TZ_CN:
            dt = dt.astimezone(_TZ_CN)
        else:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(iso_val)[:22]


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
                timeout=14,
            )
            return
        except Exception:
            pass
    messagebox.showinfo(title, message)


class App:
    def __init__(self, root: tk.Tk, api_base: str):
        self.root = root
        self.api_base = api_base.rstrip("/")
        self.root.title(f"企业版抖音点赞监控客户端 v{get_client_version()}")
        self.root.geometry("1100x880")
        self.token = None
        self._seen_alert_ids: set[int] = set()
        self._last_log_lines: list[str] = []

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.interval_min_var = tk.StringVar(value="180")
        self.interval_max_var = tk.StringVar(value="480")
        self.wecom_webhook_var = tk.StringVar()
        self.status_var = tk.StringVar(value="状态：未登录")
        self._wecom_blocked = False
        self._widgets_need_wecom: list[tk.Widget] = []

        self._ctx_menu = None
        self._build()
        self.root.after(8000, self._tick_logs)
        self.root.after(10000, self._tick_alerts)

    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}

        style = ttk.Style()
        try:
            style.configure("Treeview", rowheight=22)
            self.tree_tag_highlight = "hlrow"
            style.map(
                "Treeview",
                background=[("selected", "#3474eb")],
                foreground=[("selected", "white")],
            )
        except Exception:
            self.tree_tag_highlight = "hlrow"

        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="账号").pack(side="left")
        ttk.Entry(top, textvariable=self.username_var, width=16).pack(side="left", padx=(8, 12))
        ttk.Label(top, text="密码").pack(side="left")
        ttk.Entry(top, textvariable=self.password_var, show="*", width=18).pack(
            side="left", padx=(8, 12)
        )
        ttk.Button(top, text="登录", command=self.login).pack(side="left")

        self.wecom_gate_label = ttk.Label(
            self.root,
            text="",
            foreground="#b00020",
            wraplength=1040,
            justify="left",
        )
        self.wecom_gate_label.pack(fill="x", padx=16, pady=(0, 4))

        ctrl = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        ctrl.pack(fill="x")
        ttk.Button(ctrl, text="测试系统通知", command=self.test_notification).pack(
            side="left", **pad
        )
        for txt, cmd in [
            ("开始监控", self.monitor_start),
            ("暂停监控", self.monitor_pause),
            ("停止监控", self.monitor_stop),
            ("刷新监控状态", self.refresh_monitor_status),
            ("导出监控日志", self.export_monitor_log),
        ]:
            b = ttk.Button(ctrl, text=txt, command=cmd)
            b.pack(side="left", **pad)
            self._widgets_need_wecom.append(b)

        iv = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        iv.pack(fill="x")
        ttk.Label(iv, text="检测间隔最小(秒)").pack(side="left")
        e1 = ttk.Entry(iv, textvariable=self.interval_min_var, width=8)
        e1.pack(side="left", padx=(6, 16))
        self._widgets_need_wecom.append(e1)
        ttk.Label(iv, text="最大(秒)").pack(side="left")
        e2 = ttk.Entry(iv, textvariable=self.interval_max_var, width=8)
        e2.pack(side="left", padx=(6, 16))
        self._widgets_need_wecom.append(e2)
        b_iv = ttk.Button(iv, text="应用间隔", command=self.apply_interval)
        b_iv.pack(side="left", **pad)
        self._widgets_need_wecom.append(b_iv)

        wx_fr = ttk.LabelFrame(
            self.root,
            text="手机通知：企业微信群机器人（员工账号必填；管理员可在后台为您预填，登录后自动显示）",
            padding=(10, 8),
        )
        wx_fr.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(
            wx_fr,
            text="Webhook 地址（在企业微信群 → 群机器人 → 添加机器人 → 复制地址）",
            wraplength=980,
        ).pack(anchor="w")
        wx_row = ttk.Frame(wx_fr)
        wx_row.pack(fill="x", pady=(6, 0))
        self.wecom_entry = ttk.Entry(wx_row, textvariable=self.wecom_webhook_var, width=95)
        self.wecom_entry.pack(side="left", padx=(0, 8))
        ttk.Button(wx_row, text="保存企业微信通知", command=self.save_wecom_notify).pack(
            side="left", **pad
        )

        task_form = ttk.Frame(self.root, padding=12)
        task_form.pack(fill="x")
        ttk.Label(task_form, text="名称").pack(side="left")
        te1 = ttk.Entry(task_form, textvariable=self.name_var, width=14)
        te1.pack(side="left", padx=(8, 8))
        self._widgets_need_wecom.append(te1)
        ttk.Label(task_form, text="视频链接").pack(side="left")
        te2 = ttk.Entry(task_form, textvariable=self.url_var, width=52)
        te2.pack(side="left", padx=(8, 8))
        self._widgets_need_wecom.append(te2)
        ttk.Label(task_form, text="目标点赞").pack(side="left")
        te3 = ttk.Entry(task_form, textvariable=self.target_var, width=10)
        te3.pack(side="left", padx=(8, 8))
        self._widgets_need_wecom.append(te3)
        for txt, cmd in [
            ("新增任务", self.create_task),
            ("刷新列表", self.refresh_tasks),
            ("更新选中", self.update_selected_task),
            ("删除选中", self.delete_selected_task),
        ]:
            b = ttk.Button(task_form, text=txt, command=cmd)
            b.pack(side="left", **pad)
            self._widgets_need_wecom.append(b)

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
        self.tree.heading("url", text="视频链接（右键复制/打开；双击打开）")
        self.tree.heading("target", text="目标点赞")
        self.tree.heading("enabled", text="启用")
        self.tree.column("id", width=50)
        self.tree.column("name", width=120)
        self.tree.column("url", width=560)
        self.tree.column("target", width=90)
        self.tree.column("enabled", width=70)
        self.tree.pack(fill="both", expand=True)
        self._widgets_need_wecom.append(self.tree)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_task)
        self.tree.bind("<Double-1>", self._tree_double_click)
        self.tree.bind("<ButtonRelease-3>", self._tree_context_menu)
        if sys.platform == "darwin":
            self.tree.bind("<ButtonRelease-2>", self._tree_context_menu)
        self.tree.tag_configure(self.tree_tag_highlight, background="#a8d5ff")

        self._ctx_menu = tk.Menu(self.root, tearoff=0)
        self._ctx_menu.add_command(label="复制视频链接", command=self._ctx_copy_url)
        self._ctx_menu.add_command(label="在浏览器中打开链接", command=self._ctx_open_url)

        log_fr = ttk.LabelFrame(self.root, text="监控日志（服务端检测记录，时间为北京时间）", padding=(8, 4))
        log_fr.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.log_text = scrolledtext.ScrolledText(log_fr, height=9, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")

    def _tree_context_menu(self, event):
        """在鼠标释放时弹出右键菜单（比 Button-3 按下时更利于先选中行）。"""
        if getattr(self, "_wecom_blocked", False):
            return "break"
        if event.widget != self.tree:
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        self._ctx_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _selected_row_url(self) -> str | None:
        selected = self.tree.selection()
        if not selected:
            return None
        values = self.tree.item(selected[0], "values")
        if len(values) < 3:
            return None
        return str(values[2]).strip()

    def _ctx_copy_url(self):
        url = self._selected_row_url()
        if not url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update_idletasks()
        self.root.update()
        messagebox.showinfo("已复制", "视频链接已复制到剪贴板。")

    def _ctx_open_url(self):
        url = self._selected_row_url()
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _tree_double_click(self, event):
        if getattr(self, "_wecom_blocked", False):
            return "break"
        if event.widget != self.tree:
            return
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.tree.focus(row)
        self._ctx_open_url()

    def set_wecom_gate(self, blocked: bool) -> None:
        self._wecom_blocked = bool(blocked)
        if self._wecom_blocked:
            self.wecom_gate_label.configure(
                text=(
                    "【未配置企业微信 Webhook】任务列表、监控、日志等功能已锁定。"
                    "若管理员已在后台为您填写，请点「保存企业微信通知」同步；"
                    "否则请在下方粘贴 Webhook 后保存。"
                ),
            )
        else:
            self.wecom_gate_label.configure(text="")
        st = "disabled" if self._wecom_blocked else "normal"
        for w in self._widgets_need_wecom:
            try:
                if w == self.tree:
                    self.tree.configure(selectmode="none" if self._wecom_blocked else "extended")
                    continue
                w.configure(state=st)
            except tk.TclError:
                pass

    def _bring_to_front(self) -> None:
        try:
            self.root.deiconify()
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        if sys.platform == "win32" and ctypes:
            try:
                hwnd = self.root.winfo_id()
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _highlight_task(self, task_id: int) -> None:
        for item in self.tree.get_children():
            self.tree.item(item, tags=())
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if not vals:
                continue
            try:
                if int(vals[0]) == task_id:
                    self.tree.selection_set(item)
                    self.tree.focus(item)
                    self.tree.see(item)
                    self.tree.item(item, tags=(self.tree_tag_highlight,))
                    return
            except (ValueError, TypeError):
                continue

    def _api_error_detail(self, r: requests.Response) -> str:
        try:
            d = r.json()
            if isinstance(d, dict) and "detail" in d:
                det = d["detail"]
                if isinstance(det, list):
                    return "\n".join(
                        str(x.get("msg", x)) for x in det if isinstance(x, dict)
                    ) or r.text
                return str(det)
        except Exception:
            pass
        return r.text or f"HTTP {r.status_code}"

    def _headers(self):
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def _wecom_test_trace(self, text: str) -> None:
        """写入 exe 同目录，便于核对是否已请求测试接口。"""
        try:
            p = _app_dir() / "wecom_test_last.txt"
            p.write_text(
                f"{datetime.now(timezone.utc).isoformat()}Z\n{text}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def test_notification(self) -> None:
        """登录后仅请求服务端向企业微信机器人发测试消息，不依赖弹窗/右下角通知。"""
        play_notice_sound()
        if not self.token:
            self._wecom_test_trace("skipped: not logged in")
            try:
                self.status_var.set("未登录：无法测试企业微信推送，请先登录并保存 Webhook。")
            except Exception:
                pass
            return
        err = None
        http_st = None
        try:
            try:
                self.root.config(cursor="watch")
                self.root.update_idletasks()
            except Exception:
                pass
            r = requests.post(
                f"{self.api_base}/user/notify-test-wecom",
                headers=self._headers(),
                timeout=25,
            )
            http_st = r.status_code
            if r.status_code != 200:
                err = self._api_error_detail(r)
        except Exception as e:
            err = str(e)
        finally:
            try:
                self.root.config(cursor="")
            except Exception:
                pass
        trace = f"http_status={http_st}\nerr={err!r}\napi_base={self.api_base!r}"
        self._wecom_test_trace(trace)
        if err:
            short = (err[:200] + "…") if len(err) > 200 else err
            try:
                self.status_var.set(
                    f"企业微信测试失败 HTTP {http_st or '—'}：{short}"
                )
            except Exception:
                pass
        else:
            try:
                self.status_var.set(
                    "企业微信测试：已请求服务端推送，请到绑定机器人的群内查看（无弹窗）"
                )
            except Exception:
                pass

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
                messagebox.showerror("登录失败", self._api_error_detail(r))
                return
            self.token = r.json()["access_token"]
            self._seen_alert_ids.clear()
            self.status_var.set("状态：已登录")
            self.load_notify_settings()
            if self._wecom_blocked:
                for row in self.tree.get_children():
                    self.tree.delete(row)
                self.status_var.set("状态：已登录 | 请先完成企业微信 Webhook（下方保存后解锁）")
            else:
                self.refresh_tasks()
                self.refresh_monitor_status()
            self._tick_logs()
        except Exception as e:
            messagebox.showerror("登录失败", str(e))

    def load_notify_settings(self):
        if not self.token:
            return
        try:
            r = requests.get(
                f"{self.api_base}/user/notify-settings",
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code == 200:
                d = r.json()
                self.wecom_webhook_var.set(str(d.get("wecom_webhook_url") or ""))
                block = bool(d.get("block_operations_until_wecom"))
                self.set_wecom_gate(block)
        except Exception:
            pass

    def save_wecom_notify(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        url = self.wecom_webhook_var.get().strip()
        try:
            r = requests.patch(
                f"{self.api_base}/user/notify-settings",
                json={"wecom_webhook_url": url or None},
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code != 200:
                messagebox.showerror("保存失败", self._api_error_detail(r))
                return
            messagebox.showinfo(
                "成功",
                "已保存。点赞达标时服务端会向该机器人所在群推送消息；员工账号不可清空 Webhook。",
            )
            self.load_notify_settings()
            if not self._wecom_blocked:
                self.refresh_tasks()
                self.refresh_monitor_status()
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def refresh_monitor_status(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            r = requests.get(
                f"{self.api_base}/monitor/status", headers=self._headers(), timeout=15
            )
            if r.status_code != 200:
                messagebox.showerror("状态", self._api_error_detail(r))
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
                messagebox.showerror("保存失败", self._api_error_detail(r))
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
                messagebox.showerror("开始监控", self._api_error_detail(r))
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
                messagebox.showerror("暂停", self._api_error_detail(r))
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
                messagebox.showerror("停止", self._api_error_detail(r))
                return
            self.refresh_monitor_status()
        except Exception as e:
            messagebox.showerror("停止", str(e))

    def export_monitor_log(self):
        if not self._last_log_lines:
            messagebox.showinfo("提示", "暂无日志可导出，请登录并等待拉取日志。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本", "*.txt"), ("全部", "*.*")],
            title="导出监控日志",
        )
        if not path:
            return
        try:
            Path(path).write_text("\n".join(self._last_log_lines), encoding="utf-8")
            messagebox.showinfo("成功", f"已保存到：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _append_log_lines(self, lines: list[str]) -> None:
        self._last_log_lines = list(lines)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", "\n".join(lines))
        self.log_text.configure(state="disabled")

    def _tick_logs(self):
        if self.token and not getattr(self, "_wecom_blocked", False):
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
                        ts = format_log_time(row.get("checked_at", ""))
                        ok = "成功" if row.get("success") else "失败"
                        likes = row.get("likes")
                        err = (row.get("error_message") or "").strip()
                        name = row.get("task_name", "")
                        tid = row.get("task_id")
                        if row.get("success"):
                            lines.append(
                                f"{ts}  |  任务#{tid}「{name}」  当前点赞 {likes}  {ok}"
                            )
                        else:
                            lines.append(
                                f"{ts}  |  任务#{tid}「{name}」  {ok}  {err[:120]}"
                            )
                    self._append_log_lines(lines)
            except Exception:
                pass
        self.root.after(8000, self._tick_logs)

    def _tick_alerts(self):
        if self.token and not getattr(self, "_wecom_blocked", False):
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
                        tid = int(a["task_id"])
                        tname = a.get("task_name") or ""
                        likes = a.get("likes")
                        target = a.get("target_likes")
                        vurl = (a.get("video_url") or "").strip()
                        url_short = (vurl[:72] + "…") if len(vurl) > 72 else vurl
                        title = "点赞已达设定目标"
                        msg = (
                            f"任务 #{tid}「{tname}」\n"
                            f"当前点赞 {likes} ，目标 {target} （已达到或超过）\n"
                            f"链接：{url_short or '（请在列表中查看）'}\n"
                            f"主窗口将自动打开并高亮该任务。"
                        )
                        play_notice_sound()
                        self.root.after(0, lambda tid=tid: self._on_reach_alert(tid))
                        desktop_notify(title, msg)
                        requests.post(
                            f"{self.api_base}/alerts/{aid}/ack",
                            headers=self._headers(),
                            timeout=10,
                        )
            except Exception:
                pass
        self.root.after(10000, self._tick_alerts)

    def _on_reach_alert(self, task_id: int) -> None:
        self._bring_to_front()
        self._highlight_task(task_id)

    def refresh_tasks(self):
        if not self.token:
            return
        try:
            r = requests.get(f"{self.api_base}/tasks", headers=self._headers(), timeout=15)
            if r.status_code != 200:
                messagebox.showerror("获取任务失败", self._api_error_detail(r))
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
        try:
            url = normalize_task_url(self.url_var.get())
        except ValueError as e:
            messagebox.showerror("链接无效", str(e))
            return
        payload = {
            "name": self.name_var.get().strip() or "未命名任务",
            "video_url": url,
            "target_likes": target,
            "enabled": True,
        }
        try:
            r = requests.post(
                f"{self.api_base}/tasks", json=payload, headers=self._headers(), timeout=15
            )
            if r.status_code != 200:
                messagebox.showerror("创建失败", self._api_error_detail(r))
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
        try:
            url = normalize_task_url(self.url_var.get())
        except ValueError as e:
            messagebox.showerror("链接无效", str(e))
            return
        payload = {
            "name": self.name_var.get().strip() or "未命名任务",
            "video_url": url,
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
                messagebox.showerror("更新失败", self._api_error_detail(r))
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
                messagebox.showerror("删除失败", self._api_error_detail(r))
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
