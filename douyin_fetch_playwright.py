"""
抖音点赞抓取 — Playwright 真 Chromium 路径（抗风控能力通常强于裸 HTTP）。

启用：export DOUYIN_USE_PLAYWRIGHT=1（或由 systemd Environment= 设置）
依赖：pip install playwright && playwright install chromium
可选：DOUYIN_COOKIE（整段 Cookie 头）、DOUYIN_PLAYWRIGHT_PROXY（单条代理 URL）

与 douyin_fetch.py 相同签名，供调度器按环境变量择优加载。

注意：Uvicorn/FastAPI 运行在 asyncio 事件循环上，Playwright Sync API 不能在同进程同线程使用。
因此对外入口 fetch_likes 通过子进程调用本文件 --pw-child，在「无 asyncio」的纯净进程里跑 Sync API。
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

DIGG_PATTERN = re.compile(r'"digg_count"\s*:\s*(\d+)')

_lock = threading.Lock()
_playwright: Any = None
_browser: Any = None

_UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _ensure_browser() -> None:
    global _playwright, _browser
    with _lock:
        if _browser is not None:
            return
        from playwright.sync_api import sync_playwright

        _playwright = sync_playwright().start()
        proxy = os.getenv("DOUYIN_PLAYWRIGHT_PROXY", "").strip() or None
        proxy_cfg = {"server": proxy} if proxy else None
        _browser = _playwright.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
            ],
        )


def _extract_likes_from_html(html: str) -> int | None:
    matches = DIGG_PATTERN.findall(html)
    if not matches:
        return None
    return max(int(x) for x in matches)


def _impl_fetch_in_child_process(url: str) -> int:
    """仅在子进程中调用（无 asyncio loop）。"""
    _ensure_browser()
    cookie_header = os.getenv("DOUYIN_COOKIE", "").strip()

    context = _browser.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=_UA_MOBILE,
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        ignore_https_errors=True,
    )
    headers: dict[str, str] = {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://m.douyin.com/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    context.set_extra_http_headers(headers)

    page = context.new_page()
    try:
        time.sleep(random.uniform(0.8, 2.8))
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(random.uniform(0.5, 1.5))
        html = page.content()
        likes = _extract_likes_from_html(html)
        if likes is not None:
            return likes
        page.wait_for_timeout(3000)
        html = page.content()
        likes = _extract_likes_from_html(html)
        if likes is not None:
            return likes
        raise ValueError("Playwright 页面中未解析到 digg_count（可能遇验证页或链接失效）")
    finally:
        page.close()
        context.close()


def fetch_likes(url: str, insecure_ssl: bool = True, auto_fallback_ssl: bool = True) -> int:
    del insecure_ssl, auto_fallback_ssl
    script = Path(__file__).resolve()
    # 用 stdin 传 URL，避免 shell/argv 对 &、中文等特殊字符出问题
    r = subprocess.run(
        [sys.executable, str(script), "--pw-child"],
        input=url,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
        cwd=str(script.parent),
    )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    line = out.splitlines()[-1] if out else ""
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Playwright 子进程输出无法解析: {line!r} stderr={err!r} code={r.returncode}"
        ) from e
    if not data.get("ok"):
        raise ValueError(data.get("error") or "Playwright 子进程失败")
    return int(data["likes"])


def _main_child() -> None:
    url = sys.stdin.read().strip()
    if not url:
        print(json.dumps({"ok": False, "error": "empty url"}))
        sys.exit(1)
    try:
        n = _impl_fetch_in_child_process(url)
        print(json.dumps({"ok": True, "likes": n}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--pw-child":
        _main_child()
    else:
        sys.stderr.write("Use: python douyin_fetch_playwright.py --pw-child <stdin url>\n")
        sys.exit(2)
