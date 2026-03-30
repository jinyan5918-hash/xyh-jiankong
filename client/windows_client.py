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

# 与 client/release_version.txt 保持一致；若打包未带入该文件，标题仍显示此版本（发版请两处同改）
CLIENT_VERSION_FALLBACK = "1.2.13"

PREFS_FILENAME = "user_prefs.json"

# 柔和浅紫粉系界面（易读、不刺眼）；Windows 下配合 clam 主题上色
_THEME = {
    "bg": "#f7f2fb",
    "fg": "#4c3d5a",
    "muted": "#8e7f9e",
    "warn": "#b5657c",
    "accent": "#8b6cb3",
    "card": "#ffffff",
    "tree_sel_bg": "#eadcf7",
    "tree_sel_fg": "#3d2f4d",
    "hl_row": "#f5ebff",
    "btn_bg": "#e8dff5",
    "btn_active": "#dcc9f0",
    "btn_pressed": "#cab5e6",
    "btn_fg": "#453459",
}

# 登录页 / 主界面顶部装饰（粉嫩海浪 + 简笔小猫），纯 Canvas 绘制、无外部图片
_DECO = {
    "sky_top": "#fff5fb",
    "sky_mid": "#fce8f3",
    "sky_low": "#e8d4f0",
    "wave_a": "#b3e5fc",
    "wave_b": "#f8bbd9",
    "wave_c": "#d1c4e9",
    "foam": "#ffffff",
    "kitty_face": "#fff8fc",
    "kitty_ear": "#ffc2d4",
    "kitty_bow": "#ff5ca8",
    "kitty_bow_dark": "#e91e8c",
    "star": "#ffd54f",
    "cloud": "#ffffff",
}


def _deco_wave_polygon(
    w: int,
    h: int,
    y_base: float,
    amplitude: float,
    wavelength: float,
    phase: float,
) -> list[float]:
    import math

    pts: list[float] = []
    step = max(6, w // 64)
    x = 0.0
    while x <= w + step:
        y = y_base + amplitude * math.sin((x / max(wavelength, 1.0)) * 2 * math.pi + phase)
        pts.extend([x, y])
        x += step
    pts.extend([float(w), float(h), 0.0, float(h)])
    return pts


def _paint_login_deco(canvas: tk.Canvas, w: int, h: int) -> None:
    import math

    canvas.delete("deco")
    tag = "deco"
    w = max(w, 2)
    h = max(h, 2)
    canvas.create_rectangle(0, 0, w, h // 2, fill=_DECO["sky_top"], outline="", tags=tag)
    canvas.create_rectangle(0, h // 2, w, h, fill=_DECO["sky_mid"], outline="", tags=tag)
    for cx, cy, r in [(w * 0.12, h * 0.22, 28), (w * 0.22, h * 0.28, 20), (w * 0.78, h * 0.18, 24)]:
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=_DECO["cloud"], outline="#f5e6ef", tags=tag)
    for sx, sy in [(w * 0.08, h * 0.12), (w * 0.88, h * 0.1), (w * 0.5, h * 0.08)]:
        canvas.create_text(sx, sy, text="✦", fill=_DECO["star"], font=("Segoe UI Symbol", 14), tags=tag)
    y_sea = h * 0.52
    canvas.create_polygon(
        _deco_wave_polygon(w, h, y_sea, 14.0, w * 0.45, 0.0),
        fill=_DECO["wave_a"],
        outline="",
        tags=tag,
    )
    canvas.create_polygon(
        _deco_wave_polygon(w, h, y_sea + 18.0, 11.0, w * 0.38, 1.2),
        fill=_DECO["wave_b"],
        outline="",
        tags=tag,
    )
    canvas.create_polygon(
        _deco_wave_polygon(w, h, y_sea + 32.0, 9.0, w * 0.5, 2.4),
        fill=_DECO["wave_c"],
        outline="",
        tags=tag,
    )
    foam_y = y_sea + 8.0
    for fx in range(-20, w + 40, 55):
        fy = foam_y + 6 * math.sin(fx * 0.08)
        canvas.create_oval(fx, fy, fx + 14, fy + 6, fill=_DECO["foam"], outline="", tags=tag)
    kx, ky = w * 0.72, h * 0.36
    s = min(w / 520.0, h / 220.0, 1.15)
    s = max(s, 0.75)
    rxf, ryf = 46 * s, 40 * s
    canvas.create_polygon(
        kx - 38 * s,
        ky - 22 * s,
        kx - 14 * s,
        ky - 50 * s,
        kx + 8 * s,
        ky - 26 * s,
        fill=_DECO["kitty_ear"],
        outline="#f48fb1",
        width=2,
        tags=tag,
    )
    canvas.create_polygon(
        kx + 38 * s,
        ky - 22 * s,
        kx + 14 * s,
        ky - 50 * s,
        kx - 8 * s,
        ky - 26 * s,
        fill=_DECO["kitty_ear"],
        outline="#f48fb1",
        width=2,
        tags=tag,
    )
    canvas.create_oval(
        kx - rxf,
        ky - ryf,
        kx + rxf,
        ky + ryf,
        fill=_DECO["kitty_face"],
        outline="#ffb6c1",
        width=2,
        tags=tag,
    )
    canvas.create_oval(kx - rxf - 32 * s, ky - 46 * s, kx - rxf - 8 * s, ky - 22 * s, fill=_DECO["kitty_bow"], outline="", tags=tag)
    canvas.create_oval(kx - rxf - 22 * s, ky - 50 * s, kx + 2 * s, ky - 26 * s, fill=_DECO["kitty_bow"], outline="", tags=tag)
    canvas.create_oval(kx - rxf - 18 * s, ky - 40 * s, kx - rxf + 2 * s, ky - 24 * s, fill=_DECO["kitty_bow_dark"], outline="", tags=tag)
    eye_dx = 16 * s
    for ex in (kx - eye_dx, kx + eye_dx):
        canvas.create_oval(ex - 5 * s, ky - 8 * s, ex + 5 * s, ky + 4 * s, fill="#4a3728", outline="", tags=tag)
        canvas.create_oval(ex - 2 * s, ky - 5 * s, ex + 1 * s, ky - 2 * s, fill="#ffffff", outline="", tags=tag)
    canvas.create_polygon(
        kx,
        ky + 6 * s,
        kx - 5 * s,
        ky + 14 * s,
        kx + 5 * s,
        ky + 14 * s,
        fill="#ff8fab",
        outline="",
        tags=tag,
    )
    for sign in (-1, 1):
        canvas.create_line(
            kx + sign * 12 * s,
            ky + 4 * s,
            kx + sign * 38 * s,
            ky + 2 * s,
            fill="#c9b8c8",
            width=1,
            tags=tag,
        )
        canvas.create_line(
            kx + sign * 12 * s,
            ky + 10 * s,
            kx + sign * 36 * s,
            ky + 10 * s,
            fill="#c9b8c8",
            width=1,
            tags=tag,
        )


def _ui_font(size: int = 10, bold: bool = False) -> tuple:
    if sys.platform == "win32":
        family = "Microsoft YaHei UI"
    elif sys.platform == "darwin":
        family = "PingFang SC"
    else:
        family = "DejaVu Sans"
    return (family, size, "bold") if bold else (family, size)


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


def load_local_wecom_pref() -> str:
    """exe 同目录本地缓存 Webhook，避免服务端暂未同步时每次重填。"""
    try:
        p = _app_dir() / PREFS_FILENAME
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            return str(d.get("wecom_webhook_url") or "").strip()
    except Exception:
        pass
    return ""


def save_local_wecom_pref(url: str) -> None:
    try:
        p = _app_dir() / PREFS_FILENAME
        data: dict = {}
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["wecom_webhook_url"] = url.strip()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


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
        self.root.title(f"企业版账号辅助客户端 v{get_client_version()}")
        # 尽量紧凑：任务表支持滚动，窗口不必很大
        self.root.geometry("980x720")
        try:
            self.root.configure(background=_THEME["bg"])
        except Exception:
            pass
        self.token = None
        self._seen_alert_ids: set[int] = set()
        self._last_log_lines: list[str] = []

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.url_var = tk.StringVar()
        # 设定每增长多少赞提醒（可自定义）
        self.step_var = tk.StringVar(value="10")
        self.interval_min_var = tk.StringVar(value="180")
        self.interval_max_var = tk.StringVar(value="480")
        self.wecom_webhook_var = tk.StringVar(value=load_local_wecom_pref())
        self.status_var = tk.StringVar(value="状态：未登录")
        self._wecom_blocked = False
        self._widgets_need_wecom: list[tk.Widget] = []
        self._tasks_cache: list[dict] = []
        self._task_sort_labels = (
            "ID 新→旧",
            "ID 旧→新",
            "当前点赞 高→低",
            "当前点赞 低→高",
            "评论数 高→低",
            "评论数 低→高",
        )
        self._task_sort_var = tk.StringVar(value=self._task_sort_labels[0])
        self._task_sort_combo: ttk.Combobox | None = None

        self._ctx_menu = None
        self._welcome_fr: tk.Frame | None = None
        self._welcome_version_label: ttk.Label | None = None
        self._welcome_canvas: tk.Canvas | None = None
        self._main_fr: ttk.Frame | None = None
        self._apply_styles()
        self._build_welcome()
        self._build_main()
        self.root.after(8000, self._tick_logs)
        self.root.after(10000, self._tick_alerts)

    def _apply_styles(self) -> None:
        self.tree_tag_highlight = "hlrow"
        style = ttk.Style()
        T = _THEME
        try:
            if sys.platform == "win32":
                style.theme_use("clam")
        except Exception:
            pass
        try:
            style.configure(".", background=T["bg"], foreground=T["fg"])
            style.configure("TFrame", background=T["bg"])
            style.configure("TLabel", background=T["bg"], foreground=T["fg"], font=_ui_font(10))
            style.configure("TLabelframe", background=T["bg"], relief="solid", borderwidth=1)
            style.configure(
                "TLabelframe.Label",
                background=T["bg"],
                foreground=T["accent"],
                font=_ui_font(10, True),
            )
            style.configure(
                "TEntry",
                fieldbackground=T["card"],
                insertcolor=T["fg"],
                font=_ui_font(10),
            )
            style.configure(
                "TButton",
                padding=(14, 6),
                font=_ui_font(10),
                background=T["btn_bg"],
                foreground=T["btn_fg"],
                borderwidth=0,
                focuscolor=T["bg"],
            )
            style.map(
                "TButton",
                background=[
                    ("active", T["btn_active"]),
                    ("pressed", T["btn_pressed"]),
                ],
            )
            # 任务行操作按钮：略减左右内边距，避免文字两侧留白过大
            style.configure(
                "Compact.TButton",
                padding=(6, 4),
                font=_ui_font(10),
                background=T["btn_bg"],
                foreground=T["btn_fg"],
                borderwidth=0,
                focuscolor=T["bg"],
            )
            style.map(
                "Compact.TButton",
                background=[
                    ("active", T["btn_active"]),
                    ("pressed", T["btn_pressed"]),
                ],
            )
            style.configure(
                "Treeview",
                rowheight=26,
                background=T["card"],
                fieldbackground=T["card"],
                foreground=T["fg"],
                font=_ui_font(10),
            )
            style.map(
                "Treeview",
                background=[("selected", T["tree_sel_bg"])],
                foreground=[("selected", T["tree_sel_fg"])],
            )
            style.configure(
                "Treeview.Heading",
                font=_ui_font(10, True),
                background=T["btn_bg"],
                foreground=T["btn_fg"],
            )
        except Exception:
            pass

    def _on_welcome_canvas_configure(self, event):
        if self._welcome_canvas is None or event.widget is not self._welcome_canvas:
            return
        w = max(event.width, 2)
        h = max(event.height, 2)
        _paint_login_deco(self._welcome_canvas, w, h)

    def _build_welcome(self) -> None:
        self._welcome_fr = tk.Frame(self.root, bg=_THEME["bg"])
        self._welcome_canvas = tk.Canvas(
            self._welcome_fr,
            height=220,
            highlightthickness=0,
            bd=0,
            bg=_DECO["sky_top"],
        )
        self._welcome_canvas.pack(fill=tk.X)
        self._welcome_canvas.bind("<Configure>", self._on_welcome_canvas_configure)

        self._welcome_version_label = ttk.Label(
            self._welcome_fr,
            text=f"版本 v{get_client_version()}",
            font=_ui_font(9),
            foreground="#9a8caf",
        )
        self._welcome_version_label.place(relx=1.0, rely=1.0, anchor="se", x=-18, y=-16)

        inner = ttk.Frame(self._welcome_fr, padding=(40, 12, 40, 52))
        inner.pack(fill="both", expand=True)
        shell = ttk.Frame(inner)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.columnconfigure(1, weight=0)
        shell.columnconfigure(2, weight=1)
        mid = ttk.Frame(shell)
        mid.grid(row=0, column=1, sticky="n", pady=(4, 0))

        text_stack = ttk.Frame(mid)
        text_stack.pack(pady=(0, 6))
        ttk.Label(
            text_stack,
            text="企业版账号辅助客户端",
            font=_ui_font(11),
            foreground="#6f5f82",
            justify="center",
        ).pack(anchor="center")
        ttk.Label(
            text_stack,
            text="系统已为你开启好运模式～",
            font=_ui_font(17, True),
            foreground="#7d5fa8",
            justify="center",
        ).pack(pady=(16, 0), anchor="center")
        ttk.Label(
            text_stack,
            text="星与海/创左内部使用，泄漏追责",
            font=_ui_font(9),
            foreground="#b07888",
            wraplength=420,
            justify="center",
        ).pack(pady=(20, 0), anchor="center")

        row1 = ttk.Frame(mid)
        row1.pack(fill="x", pady=(26, 8))
        ttk.Label(row1, text="账号", width=6).pack(side="left")
        ttk.Entry(row1, textvariable=self.username_var, width=28).pack(side="left", padx=(12, 0))
        row2 = ttk.Frame(mid)
        row2.pack(fill="x", pady=8)
        ttk.Label(row2, text="密码", width=6).pack(side="left")
        ttk.Entry(row2, textvariable=self.password_var, show="*", width=28).pack(side="left", padx=(12, 0))
        ttk.Button(mid, text="登录", command=self.login).pack(pady=(22, 0))
        self._welcome_fr.pack(fill="both", expand=True)

    def _build_main(self) -> None:
        pad = {"padx": 6, "pady": 4}
        self._main_fr = ttk.Frame(self.root)

        self.wecom_gate_label = ttk.Label(
            self._main_fr,
            text="",
            foreground=_THEME["warn"],
            wraplength=1040,
            justify="left",
        )
        self.wecom_gate_label.pack(fill="x", padx=16, pady=(0, 4))

        ctrl = ttk.Frame(self._main_fr, padding=(12, 0, 12, 8))
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

        iv = ttk.Frame(self._main_fr, padding=(12, 0, 12, 8))
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
            self._main_fr,
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

        task_form = ttk.Frame(self._main_fr, padding=12)
        task_form.pack(fill="x")
        ttk.Label(task_form, text="名称").pack(side="left")
        te1 = ttk.Entry(task_form, textvariable=self.name_var, width=14)
        te1.pack(side="left", padx=(8, 8))
        self._widgets_need_wecom.append(te1)

        ttk.Label(task_form, text="视频链接").pack(side="left")
        te2 = ttk.Entry(task_form, textvariable=self.url_var, width=48)
        te2.pack(side="left", padx=(8, 8))
        self._widgets_need_wecom.append(te2)
        ttk.Label(task_form, text="每增长(赞)提醒").pack(side="left")
        te3 = ttk.Entry(task_form, textvariable=self.step_var, width=10)
        te3.pack(side="left", padx=(8, 8))
        self._widgets_need_wecom.append(te3)
        b_new = ttk.Button(task_form, text="新增任务", command=self.create_task)
        b_new.pack(side="left", **pad)
        self._widgets_need_wecom.append(b_new)

        task_row2 = ttk.Frame(self._main_fr, padding=(12, 0, 12, 6))
        task_row2.pack(fill="x")
        ttk.Label(
            task_row2,
            text=(
                "单任务：选中表格一行后，可「暂停任务」（仅停这一条）、「恢复任务」、「停用任务」（长期不参与检测）、"
                "「启用任务」。列表中「任务状态」为中文；「当前点赞/评论数」为服务端最近一次成功爬取到的数量。"
            ),
            font=_ui_font(9),
            foreground=_THEME["muted"],
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        task_btns = ttk.Frame(task_row2)
        task_btns.pack(anchor="w")
        _btn_pad = (0, 5)
        for txt, cmd in [
            ("暂停任务", self.task_pause_selected),
            ("恢复任务", self.task_resume_selected),
            ("停用任务", self.task_disable_selected),
            ("启用任务", self.task_enable_selected),
            ("刷新列表", self.refresh_tasks),
            ("更新选中", self.update_selected_task),
            ("删除选中", self.delete_selected_task),
        ]:
            b = ttk.Button(task_btns, text=txt, command=cmd, style="Compact.TButton")
            b.pack(side="left", padx=_btn_pad)
            self._widgets_need_wecom.append(b)

        bottom = ttk.Frame(self._main_fr, padding=(12, 4, 12, 8), height=28)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, anchor="w")

        mid_pane = ttk.PanedWindow(self._main_fr, orient=tk.VERTICAL)
        mid_pane.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        table_wrap = ttk.Frame(mid_pane, padding=(0, 0, 0, 6))
        sort_bar = ttk.Frame(table_wrap)
        sort_bar.pack(fill="x", pady=(0, 6))
        ttk.Label(sort_bar, text="列表排序").pack(side="left", padx=(0, 8))
        self._task_sort_combo = ttk.Combobox(
            sort_bar,
            textvariable=self._task_sort_var,
            values=list(self._task_sort_labels),
            state="readonly",
            width=20,
        )
        self._task_sort_combo.pack(side="left")
        self._task_sort_combo.bind("<<ComboboxSelected>>", self._on_task_sort_change)
        self._widgets_need_wecom.append(self._task_sort_combo)

        tree_shell = ttk.Frame(table_wrap)
        tree_shell.pack(fill="both", expand=True)
        ysb = ttk.Scrollbar(tree_shell, orient="vertical")
        xsb = ttk.Scrollbar(tree_shell, orient="horizontal")
        self.tree = ttk.Treeview(
            tree_shell,
            columns=("id", "name", "url", "current", "comments", "status"),
            show="headings",
            height=12,
            yscrollcommand=ysb.set,
            xscrollcommand=xsb.set,
        )
        ysb.config(command=self.tree.yview)
        xsb.config(command=self.tree.xview)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="名称")
        self.tree.heading("url", text="视频链接（右键复制/打开；双击打开）")
        self.tree.heading("current", text="当前点赞")
        self.tree.heading("comments", text="评论数")
        self.tree.heading("status", text="任务状态")
        self.tree.column("id", width=48)
        self.tree.column("name", width=100)
        self.tree.column("url", width=520)
        self.tree.column("current", width=72)
        self.tree.column("comments", width=72)
        self.tree.column("status", width=80)
        self._widgets_need_wecom.append(self.tree)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_task)
        self.tree.bind("<Double-1>", self._tree_double_click)
        self.tree.bind("<ButtonRelease-3>", self._tree_context_menu)
        if sys.platform == "darwin":
            self.tree.bind("<ButtonRelease-2>", self._tree_context_menu)
        self.tree.tag_configure(self.tree_tag_highlight, background=_THEME["hl_row"])

        self._ctx_menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=_THEME["card"],
            fg=_THEME["fg"],
            activebackground=_THEME["tree_sel_bg"],
            activeforeground=_THEME["tree_sel_fg"],
            relief="flat",
        )
        self._ctx_menu.add_command(label="复制视频链接", command=self._ctx_copy_url)
        self._ctx_menu.add_command(label="在浏览器中打开链接", command=self._ctx_open_url)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="暂停此任务", command=self.task_pause_selected)
        self._ctx_menu.add_command(label="恢复此任务", command=self.task_resume_selected)
        self._ctx_menu.add_command(label="停用此任务", command=self.task_disable_selected)
        self._ctx_menu.add_command(label="启用此任务", command=self.task_enable_selected)

        mid_pane.add(table_wrap, weight=5)
        log_fr = ttk.LabelFrame(
            mid_pane,
            text="监控日志（服务端检测记录，时间为北京时间）",
            padding=(8, 4),
        )
        mid_pane.add(log_fr, weight=2)
        try:
            mid_pane.paneconfigure(log_fr, minsize=120)
        except Exception:
            pass
        self.log_text = scrolledtext.ScrolledText(
            log_fr,
            height=9,
            state="disabled",
            wrap="word",
            font=_ui_font(9),
            bg=_THEME["card"],
            fg=_THEME["fg"],
            insertbackground=_THEME["fg"],
            selectbackground=_THEME["tree_sel_bg"],
            selectforeground=_THEME["tree_sel_fg"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=_THEME["btn_active"],
            highlightcolor=_THEME["accent"],
        )
        self.log_text.pack(fill="both", expand=True)

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
                foreground=_THEME["warn"],
                text=(
                    "【温馨提示】尚未配置企业微信 Webhook，任务与监控暂时不可用。"
                    "若管理员已在后台为您填好，请点击「保存企业微信通知」同步；"
                    "也可在下方粘贴 Webhook 后保存。"
                ),
            )
        else:
            self.wecom_gate_label.configure(text="", foreground=_THEME["muted"])
        st = "disabled" if self._wecom_blocked else "normal"
        for w in self._widgets_need_wecom:
            try:
                if w == self.tree:
                    self.tree.configure(selectmode="none" if self._wecom_blocked else "extended")
                    continue
                w.configure(state=st)
            except tk.TclError:
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

    def _current_sort_key(self) -> str:
        m = {
            self._task_sort_labels[0]: "id_desc",
            self._task_sort_labels[1]: "id_asc",
            self._task_sort_labels[2]: "current_desc",
            self._task_sort_labels[3]: "current_asc",
            self._task_sort_labels[4]: "comment_desc",
            self._task_sort_labels[5]: "comment_asc",
        }
        return m.get(self._task_sort_var.get(), "id_desc")

    def _on_task_sort_change(self, _event=None):
        self._render_task_rows(self._tasks_cache)

    def _fmt_current_likes_cell(self, v) -> str:
        if v is None:
            return "—"
        try:
            return str(int(v))
        except (TypeError, ValueError):
            return "—"

    def _task_row_status(self, item: dict) -> str:
        en = bool(item.get("enabled", True))
        pau = bool(item.get("task_paused", False))
        if not en:
            return "已停用"
        if pau:
            return "已暂停"
        return "监控中"

    def _sort_tasks_list(self, items: list[dict]) -> list[dict]:
        k = self._current_sort_key()
        if k == "id_desc":
            return sorted(items, key=lambda x: int(x["id"]), reverse=True)
        if k == "id_asc":
            return sorted(items, key=lambda x: int(x["id"]), reverse=False)
        if k == "current_desc":
            return sorted(
                items,
                key=lambda x: (
                    0 if x.get("current_likes") is not None else 1,
                    -(int(x["current_likes"]) if x.get("current_likes") is not None else 0),
                ),
            )
        if k == "current_asc":
            return sorted(
                items,
                key=lambda x: (
                    0 if x.get("current_likes") is not None else 1,
                    int(x["current_likes"]) if x.get("current_likes") is not None else 0,
                ),
            )
        if k == "comment_desc":
            return sorted(
                items,
                key=lambda x: (
                    0 if x.get("comment_count") is not None else 1,
                    -(int(x["comment_count"]) if x.get("comment_count") is not None else 0),
                ),
            )
        if k == "comment_asc":
            return sorted(
                items,
                key=lambda x: (
                    0 if x.get("comment_count") is not None else 1,
                    int(x["comment_count"]) if x.get("comment_count") is not None else 0,
                ),
            )
        return list(items)

    def _render_task_rows(self, items: list[dict]) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        ordered = self._sort_tasks_list(list(items))
        for item in ordered:
            self.tree.insert(
                "",
                "end",
                values=(
                    item["id"],
                    item["name"],
                    item["video_url"],
                    self._fmt_current_likes_cell(item.get("current_likes")),
                    self._fmt_current_likes_cell(item.get("comment_count")),
                    self._task_row_status(item),
                ),
            )

    def _selected_task_id(self) -> int | None:
        selected = self.tree.selection()
        if not selected:
            return None
        values = self.tree.item(selected[0], "values")
        if not values:
            return None
        try:
            return int(values[0])
        except (TypeError, ValueError):
            return None

    def _patch_task(self, task_id: int, payload: dict) -> bool:
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return False
        try:
            r = requests.patch(
                f"{self.api_base}/tasks/{task_id}",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code != 200:
                messagebox.showerror("操作失败", self._api_error_detail(r))
                return False
            self.refresh_tasks()
            return True
        except Exception as e:
            messagebox.showerror("操作失败", str(e))
            return False

    def task_pause_selected(self):
        tid = self._selected_task_id()
        if tid is None:
            messagebox.showinfo("提示", "请先选中一个任务")
            return
        self._patch_task(tid, {"task_paused": True})

    def task_resume_selected(self):
        tid = self._selected_task_id()
        if tid is None:
            messagebox.showinfo("提示", "请先选中一个任务")
            return
        self._patch_task(tid, {"task_paused": False})

    def task_disable_selected(self):
        tid = self._selected_task_id()
        if tid is None:
            messagebox.showinfo("提示", "请先选中一个任务")
            return
        self._patch_task(tid, {"enabled": False, "task_paused": False})

    def task_enable_selected(self):
        tid = self._selected_task_id()
        if tid is None:
            messagebox.showinfo("提示", "请先选中一个任务")
            return
        self._patch_task(tid, {"enabled": True, "task_paused": False})

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
            if self._welcome_fr is not None and self._main_fr is not None:
                self._welcome_fr.pack_forget()
                self._main_fr.pack(fill="both", expand=True)
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
                srv = str(d.get("wecom_webhook_url") or "").strip()
                local = load_local_wecom_pref()
                self.wecom_webhook_var.set(srv or local)
                if srv:
                    save_local_wecom_pref(srv)
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
            save_local_wecom_pref(url)
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
                        vurl = (a.get("video_url") or "").strip()
                        url_short = (vurl[:72] + "…") if len(vurl) > 72 else vurl
                        atype = (a.get("type") or "like_step").strip()
                        if atype == "comment":
                            cc = a.get("comment_count")
                            snippet = (a.get("comment_snippet") or "").strip()
                            title = "发现新评论"
                            msg = (
                                f"任务 #{tid}「{tname}」\n"
                                f"当前评论数 {cc}\n"
                                + (f"最新评论：{snippet[:140]}\n" if snippet else "")
                                + f"链接：{url_short or '（请在列表中查看）'}\n"
                                + "打开本客户端窗口时可在列表中看到该任务已高亮。"
                            )
                        else:
                            likes = a.get("likes")
                            step = a.get("step_likes")
                            title = "点赞增长提醒"
                            msg = (
                                f"任务 #{tid}「{tname}」\n"
                                f"当前点赞 {likes} ，每增长 {step} 赞提醒\n"
                                f"链接：{url_short or '（请在列表中查看）'}\n"
                                f"打开本客户端窗口时可在列表中看到该任务已高亮。"
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
        self._highlight_task(task_id)

    def refresh_tasks(self):
        if not self.token:
            return
        try:
            r = requests.get(f"{self.api_base}/tasks", headers=self._headers(), timeout=15)
            if r.status_code != 200:
                messagebox.showerror("获取任务失败", self._api_error_detail(r))
                return
            items = r.json()
            self._tasks_cache = items
            self._render_task_rows(items)
        except Exception as e:
            messagebox.showerror("获取任务失败", str(e))

    def create_task(self):
        if not self.token:
            messagebox.showinfo("提示", "请先登录")
            return
        try:
            step = int(self.step_var.get().strip())
            if step < 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("参数错误", "每增长(赞)提醒 必须是非负整数（0 表示不提醒）")
            return
        try:
            url = normalize_task_url(self.url_var.get())
        except ValueError as e:
            messagebox.showerror("链接无效", str(e))
            return
        payload = {
            "name": self.name_var.get().strip() or "未命名任务",
            "video_url": url,
            "target_likes": 0,
            "notify_step_likes": step,
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
        if len(values) < 6:
            return
        self.name_var.set(str(values[1]))
        self.url_var.set(str(values[2]))
        try:
            tid = int(values[0])
        except Exception:
            return
        for it in self._tasks_cache:
            try:
                if int(it.get("id")) == tid:
                    sv = it.get("notify_step_likes", 10)
                    self.step_var.set(str(int(sv) if sv is not None else 10))
                    break
            except Exception:
                continue

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
            step = int(self.step_var.get().strip())
            if step < 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("参数错误", "每增长(赞)提醒 必须是非负整数（0 表示不提醒）")
            return
        try:
            url = normalize_task_url(self.url_var.get())
        except ValueError as e:
            messagebox.showerror("链接无效", str(e))
            return
        payload = {
            "name": self.name_var.get().strip() or "未命名任务",
            "video_url": url,
            "target_likes": 0,
            "notify_step_likes": step,
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
