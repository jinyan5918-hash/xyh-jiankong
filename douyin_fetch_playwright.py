"""
抖音点赞抓取 — Playwright 真 Chromium 路径（抗风控能力通常强于裸 HTTP）。

启用：export DOUYIN_USE_PLAYWRIGHT=1（或由 systemd Environment= 设置）
依赖：pip install playwright && playwright install chromium
可选：DOUYIN_COOKIE（整段 Cookie 头）、DOUYIN_PLAYWRIGHT_PROXY（单条代理 URL）

与 douyin_fetch.py 相同签名，供调度器按环境变量择优加载。

注意：Sync API 不能在 Uvicorn 的 asyncio 线程里用 → 父进程用 subprocess 调 --pw-child。
子进程内用「单次 with sync_playwright()」管理生命周期，避免全局 browser 被误关或继承信号导致异常。
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DIGG_PATTERN = re.compile(r'"digg_count"\s*:\s*(\d+)')
COMMENT_PATTERN = re.compile(r'"comment_count"\s*:\s*(\d+)')

_UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-breakpad",
    "--disable-features=TranslateUI",
]


def _reset_signals_for_child() -> None:
    """避免继承 Uvicorn/ systemd 侧信号处理，误杀 Chromium 子进程。"""
    if sys.platform != "win32":
        import signal

        try:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
        except Exception:
            pass


def _extract_likes_from_html(html: str) -> int | None:
    matches = DIGG_PATTERN.findall(html)
    if not matches:
        return None
    return max(int(x) for x in matches)


def _extract_comment_count_from_html(html: str) -> int | None:
    matches = COMMENT_PATTERN.findall(html)
    if not matches:
        return None
    return max(int(x) for x in matches)


def _impl_fetch_in_child_process(url: str) -> dict[str, Any]:
    """仅在子进程中调用：一次 with 块内完成 launch → 抓取 → 关闭。"""
    _reset_signals_for_child()
    from playwright.sync_api import sync_playwright

    cookie_header = os.getenv("DOUYIN_COOKIE", "").strip()
    proxy = os.getenv("DOUYIN_PLAYWRIGHT_PROXY", "").strip() or None
    proxy_cfg: dict[str, Any] | None = {"server": proxy} if proxy else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            timeout=120_000,
            args=list(_CHROMIUM_ARGS),
        )
        try:
            context = browser.new_context(
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
                cc = _extract_comment_count_from_html(html)
                if likes is not None:
                    return {
                        "likes": likes,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                page.wait_for_timeout(3000)
                html = page.content()
                likes = _extract_likes_from_html(html)
                cc = _extract_comment_count_from_html(html)
                if likes is not None:
                    return {
                        "likes": likes,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                raise ValueError(
                    "Playwright 页面中未解析到 digg_count（可能遇验证页或链接失效）"
                )
            finally:
                page.close()
                context.close()
        finally:
            browser.close()


def fetch_likes(url: str, insecure_ssl: bool = True, auto_fallback_ssl: bool = True) -> int:
    del insecure_ssl, auto_fallback_ssl
    script = Path(__file__).resolve()
    repo_root = script.parent
    lock_path = repo_root / ".douyin_playwright_subprocess.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()

    def _run_sub() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--pw-child"],
            input=url,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(repo_root),
            start_new_session=True,
        )

    if sys.platform == "win32":
        r = _run_sub()
    else:
        import fcntl

        with open(lock_path, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                r = _run_sub()
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

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


def fetch_metrics(url: str, insecure_ssl: bool = True, auto_fallback_ssl: bool = True) -> dict[str, Any]:
    """返回 {likes, comment_count, latest_comment}；latest_comment 可能为 None。"""
    del auto_fallback_ssl
    script = Path(__file__).resolve()
    repo_root = script.parent
    lock_path = repo_root / ".douyin_playwright_subprocess.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()

    def _run_sub() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--pw-child"],
            input=url,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(repo_root),
            start_new_session=True,
        )

    if sys.platform == "win32":
        r = _run_sub()
    else:
        import fcntl

        with open(lock_path, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                r = _run_sub()
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

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
    return {
        "likes": int(data.get("likes") or 0),
        "comment_count": (int(data["comment_count"]) if data.get("comment_count") is not None else None),
        "latest_comment": data.get("latest_comment"),
    }


def fetch_author_nickname(url: str, insecure_ssl: bool = True, auto_fallback_ssl: bool = True) -> str | None:
    """Playwright 路径下解析作者昵称，优先 author.nickname。"""
    del insecure_ssl, auto_fallback_ssl
    script = Path(__file__).resolve()
    repo_root = script.parent
    lock_path = repo_root / ".douyin_playwright_subprocess.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()

    def _run_sub() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--pw-child"],
            input=url,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(repo_root),
            start_new_session=True,
        )

    if sys.platform == "win32":
        r = _run_sub()
    else:
        import fcntl

        with open(lock_path, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                r = _run_sub()
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    out = (r.stdout or "").strip()
    line = out.splitlines()[-1] if out else ""
    data = json.loads(line) if line else {}
    if not data.get("ok"):
        return None
    html = str(data.get("html") or "")
    if not html:
        return None
    m = re.search(
        r'"author"\s*:\s*\{[^{}]{0,1400}?"nickname"\s*:\s*"([^"]{1,120})"',
        html,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()[:128]
    m2 = re.search(r'"nickname"\s*:\s*"([^"]{1,120})"', html)
    if m2:
        return m2.group(1).strip()[:128]
    return None


def _main_child() -> None:
    url = sys.stdin.read().strip()
    if not url:
        print(json.dumps({"ok": False, "error": "empty url"}))
        sys.exit(1)
    try:
        d = _impl_fetch_in_child_process(url)
        d["html"] = d.get("html") or ""
        print(json.dumps({"ok": True, **d}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--pw-child":
        _main_child()
    else:
        sys.stderr.write("Use: python douyin_fetch_playwright.py --pw-child <stdin url>\n")
        sys.exit(2)
