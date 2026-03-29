"""
抖音点赞抓取 — Playwright 真 Chromium 路径（抗风控能力通常强于裸 HTTP）。

启用：export DOUYIN_USE_PLAYWRIGHT=1（或由 systemd Environment= 设置）
依赖：pip install playwright && playwright install chromium
可选：DOUYIN_COOKIE（整段 Cookie 头）、DOUYIN_PLAYWRIGHT_PROXY（单条代理 URL）

与 douyin_fetch.py 相同签名，供调度器按环境变量择优加载。
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from typing import Any

DIGG_PATTERN = re.compile(r'"digg_count"\s*:\s*(\d+)')
VIDEO_ID_PATTERNS = [
    re.compile(r"/video/(\d+)"),
    re.compile(r'"aweme_id"\s*:\s*"(\d+)"'),
]

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


def fetch_likes(url: str, insecure_ssl: bool = True, auto_fallback_ssl: bool = True) -> int:
    del insecure_ssl, auto_fallback_ssl  # Playwright 自行校验证书；需忽略时可改 ignore_https_errors

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
        # 人类化间隔，降低请求节奏特征（秒级随机）
        time.sleep(random.uniform(0.8, 2.8))
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(random.uniform(0.5, 1.5))
        html = page.content()
        likes = _extract_likes_from_html(html)
        if likes is not None:
            return likes
        # 再等一等异步注入的 JSON
        page.wait_for_timeout(3000)
        html = page.content()
        likes = _extract_likes_from_html(html)
        if likes is not None:
            return likes
        raise ValueError("Playwright 页面中未解析到 digg_count（可能遇验证页或链接失效）")
    finally:
        page.close()
        context.close()
