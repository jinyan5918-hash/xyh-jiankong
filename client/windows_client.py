import json
import platform
import sys
import uuid
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

import requests


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_api_base() -> str:
    """与 exe 同目录的 config.json 中的 api_base；开发模式无文件时用本机。"""
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


class App:
    def __init__(self, root: tk.Tk, api_base: str):
        self.root = root
        self.api_base = api_base.rstrip("/")
        self.root.title("企业版抖音点赞监控客户端（MVP）")
        self.root.geometry("980x620")
        self.token = None

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.status_var = tk.StringVar(value="状态：未登录")

        self._build()

    def _build(self):
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text="账号").pack(side="left")
        ttk.Entry(top, textvariable=self.username_var, width=16).pack(side="left", padx=(8, 12))
        ttk.Label(top, text="密码").pack(side="left")
        ttk.Entry(top, textvariable=self.password_var, show="*", width=18).pack(
            side="left", padx=(8, 12)
        )
        ttk.Button(top, text="登录", command=self.login).pack(side="left")

        task_form = ttk.Frame(self.root, padding=12)
        task_form.pack(fill="x")
        ttk.Label(task_form, text="名称").pack(side="left")
        ttk.Entry(task_form, textvariable=self.name_var, width=14).pack(side="left", padx=(8, 8))
        ttk.Label(task_form, text="视频链接").pack(side="left")
        ttk.Entry(task_form, textvariable=self.url_var, width=56).pack(side="left", padx=(8, 8))
        ttk.Label(task_form, text="目标点赞").pack(side="left")
        ttk.Entry(task_form, textvariable=self.target_var, width=10).pack(side="left", padx=(8, 8))
        ttk.Button(task_form, text="新增任务", command=self.create_task).pack(side="left")
        ttk.Button(task_form, text="刷新列表", command=self.refresh_tasks).pack(side="left", padx=8)
        ttk.Button(task_form, text="更新选中", command=self.update_selected_task).pack(side="left")
        ttk.Button(task_form, text="删除选中", command=self.delete_selected_task).pack(side="left", padx=8)

        table_wrap = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        table_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table_wrap,
            columns=("id", "name", "url", "target", "enabled"),
            show="headings",
            height=18,
        )
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="名称")
        self.tree.heading("url", text="视频链接")
        self.tree.heading("target", text="目标点赞")
        self.tree.heading("enabled", text="启用")
        self.tree.column("id", width=60)
        self.tree.column("name", width=140)
        self.tree.column("url", width=520)
        self.tree.column("target", width=100)
        self.tree.column("enabled", width=80)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_task)

        bottom = ttk.Frame(self.root, padding=12)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")

    def _headers(self):
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

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
            self.status_var.set("状态：已登录")
            self.refresh_tasks()
        except Exception as e:
            messagebox.showerror("登录失败", str(e))

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
            r = requests.post(f"{self.api_base}/tasks", json=payload, headers=self._headers(), timeout=15)
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
