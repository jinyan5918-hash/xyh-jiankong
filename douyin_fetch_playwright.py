"""
抖音点赞抓取 — Playwright 真 Chromium 路径（抗风控能力通常强于裸 HTTP）。

启用：export DOUYIN_USE_PLAYWRIGHT=1（或由 systemd Environment= 设置）
依赖：pip install playwright && playwright install chromium
可选：DOUYIN_COOKIE（整段 Cookie 头）、DOUYIN_PLAYWRIGHT_PROXY（单条代理 URL）
排障：DOUYIN_PLAYWRIGHT_DEBUG=1 失败时在 stderr 打诊断 JSON；DOUYIN_PLAYWRIGHT_WARMUP=1 先打开 m.douyin.com 再跳短链（机房 IP 可试）。

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

from douyin_fetch import _digg_from_item_api_json, _extract_item_id, _fetch_likes_by_item_api

# 与 douyin_fetch._DIGG_PATTERNS 保持一致，避免仅 Playwright 路径漏解析
_DIGG_PATTERNS = (
    re.compile(r'"digg_count"\s*:\s*(\d+)'),
    re.compile(r'"digg_count"\s*:\s*"(\d+)"'),
    re.compile(r'"like_count"\s*:\s*(\d+)'),
    re.compile(r'"admire_count"\s*:\s*(\d+)'),
    re.compile(r"\bdigg_count\s*[=:]\s*(\d+)"),
)
COMMENT_PATTERN = re.compile(r'"comment_count"\s*:\s*(\d+)')

# 短链 id 至少 6 位，避免把误贴在 path 里的「https」当成 id（仅 5 位）
_SHORT_CODE_IN_URL = re.compile(
    r"https://v\.douyin\.com/([A-Za-z0-9]{6,})/?", re.IGNORECASE
)


def _pick_short_share_url(raw: str) -> str:
    """若误粘贴成 …/v.douyin.com/https://v.douyin.com/xxx/，取最后一个合法短链。"""
    hits = _SHORT_CODE_IN_URL.findall(raw)
    if not hits:
        return raw
    return f"https://v.douyin.com/{hits[-1]}/"


_HOME_ONLY = frozenset({"https://www.douyin.com", "https://douyin.com"})


def _item_api_likes(final_url: str, html: str) -> int | None:
    """与 douyin_fetch._fetch_likes_once 一致：从落地页解析 aweme_id 后走 iteminfo 等接口（复用 DOUYIN_COOKIE）。"""
    item_id = _extract_item_id(final_url, html)
    if not item_id:
        return None
    return _fetch_likes_by_item_api(item_id, insecure_ssl=False)


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
    nums: list[int] = []
    for p in _DIGG_PATTERNS:
        for x in p.findall(html):
            try:
                nums.append(int(x))
            except Exception:
                continue
    if not nums:
        return None
    return max(nums)


def _extract_comment_count_from_html(html: str) -> int | None:
    matches = COMMENT_PATTERN.findall(html)
    if not matches:
        return None
    return max(int(x) for x in matches)


def _strip_xssi_json_prefix(raw: str) -> str:
    s = raw.strip()
    for prefix in ("while(1);", "for(;;);", ")]}'"):
        if s.startswith(prefix):
            s = s[len(prefix) :].lstrip()
            break
    return s


def _pw_collect_response_digg(
    response: Any, bucket: list[int], diag_urls: list[str] | None
) -> None:
    """收集页面加载过程中 XHR/fetch 返回的 JSON 中的 digg（与真实客户端一致，常比首屏 HTML 可靠）。"""
    try:
        if response.status != 200:
            return
        req = response.request
        rt = req.resource_type
        u = (response.url or "").lower()
        apiish = any(
            x in u
            for x in (
                "iteminfo",
                "aweme/v1",
                "aweme/v2",
                "web/api",
                "/aweme/detail",
                "multi/aweme",
                "aweme_id=",
                "item_ids=",
            )
        )
        if not apiish and rt not in ("xhr", "fetch", "other"):
            return
        if "douyin" not in u and "iesdouyin" not in u and "amemv" not in u and "snssdk" not in u:
            return
        ct = (response.headers.get("content-type") or "").lower()
        if "json" not in ct and "text/plain" not in ct and "javascript" not in ct:
            if not apiish:
                return
        try:
            body = response.text()
        except Exception:
            return
        if len(body) > 4_000_000 or (
            "digg_count" not in body and "like_count" not in body and '"statistics"' not in body
        ):
            return
        if diag_urls is not None and len(diag_urls) < 48:
            diag_urls.append(response.url[:240])
        try:
            data = json.loads(_strip_xssi_json_prefix(body))
            d = _digg_from_item_api_json(data)
            if d is not None:
                bucket.append(int(d))
        except Exception:
            pass
        alt = _extract_likes_from_html(body)
        if alt is not None:
            bucket.append(int(alt))
    except Exception:
        pass


def _net_digg_best(bucket: list[int]) -> int | None:
    return max(bucket) if bucket else None


def _debug_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _playwright_failure_diag(
    page: Any, html: str, net_diggs: list[int], diag_urls: list[str], has_cookie: bool
) -> None:
    """.stderr 输出一行 JSON，便于区分：机房空壳页 / 无 XHR 数据 / Cookie 未带上等。"""
    import sys

    title = ""
    try:
        title = (page.title() or "")[:200]
    except Exception:
        pass
    final_url = ""
    try:
        final_url = page.url or ""
    except Exception:
        pass
    low = html.lower()
    hints: dict[str, bool] = {
        "maybe_verify": any(
            w in low
            for w in ("验证码", "验证", "安全验证", "captcha", "slider", "拖动", "访问异常")
        ),
        "maybe_empty_shell": "digg_count" not in html and "aweme_id" not in html and len(html) < 8000,
    }
    payload = {
        "playwright_debug": True,
        "final_url": final_url[:400],
        "title": title,
        "html_chars": len(html),
        "net_digg_samples": len(net_diggs),
        "xhr_urls_seen": len(diag_urls),
        "xhr_url_sample": diag_urls[:8],
        "do_have_cookie_header": has_cookie,
        "hints": hints,
    }
    try:
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
    except Exception:
        pass


def _impl_fetch_in_child_process(url: str, require_likes: bool = True) -> dict[str, Any]:
    """仅在子进程中调用：一次 with 块内完成 launch → 抓取 → 关闭。"""
    url = _pick_short_share_url(url.strip())
    _reset_signals_for_child()
    from playwright.sync_api import sync_playwright

    cookie_header = os.getenv("DOUYIN_COOKIE", "").strip()
    proxy = os.getenv("DOUYIN_PLAYWRIGHT_PROXY", "").strip() or None
    proxy_cfg: dict[str, Any] | None = {"server": proxy} if proxy else None
    want_debug = _debug_truthy("DOUYIN_PLAYWRIGHT_DEBUG")
    want_warmup = _debug_truthy("DOUYIN_PLAYWRIGHT_WARMUP")

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
            net_diggs: list[int] = []
            diag_urls: list[str] | None = [] if want_debug else None
            page.on(
                "response",
                lambda r: _pw_collect_response_digg(r, net_diggs, diag_urls),
            )
            try:
                time.sleep(random.uniform(0.8, 2.8))
                if want_warmup:
                    try:
                        page.goto(
                            "https://m.douyin.com/",
                            wait_until="domcontentloaded",
                            timeout=25_000,
                        )
                        time.sleep(random.uniform(0.4, 1.2))
                    except Exception:
                        pass
                page.goto(url, wait_until="load", timeout=90_000)
                time.sleep(random.uniform(0.5, 1.5))
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    pass
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
                if not require_likes:
                    return {
                        "likes": 0,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                nd = _net_digg_best(net_diggs)
                if nd is not None:
                    return {
                        "likes": nd,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                if page.url.rstrip("/") in _HOME_ONLY:
                    raise ValueError("短链已失效或不是视频链接（跳转到抖音首页）")
                api_likes = _item_api_likes(page.url, html)
                if api_likes is not None:
                    return {
                        "likes": api_likes,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                page.wait_for_timeout(3000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    pass
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
                if not require_likes:
                    return {
                        "likes": 0,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                nd = _net_digg_best(net_diggs)
                if nd is not None:
                    return {
                        "likes": nd,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                if page.url.rstrip("/") in _HOME_ONLY:
                    raise ValueError("短链已失效或不是视频链接（跳转到抖音首页）")
                api_likes = _item_api_likes(page.url, html)
                if api_likes is not None:
                    return {
                        "likes": api_likes,
                        "comment_count": cc,
                        "latest_comment": None,
                        "html": html[:500000],
                    }
                if want_debug:
                    _playwright_failure_diag(
                        page,
                        html,
                        net_diggs,
                        diag_urls if diag_urls is not None else [],
                        bool(cookie_header),
                    )
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
        d = _impl_fetch_in_child_process(url, require_likes=True)
        d["html"] = d.get("html") or ""
        print(json.dumps({"ok": True, **d}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def _main_author_child() -> None:
    url = sys.stdin.read().strip()
    if not url:
        print(json.dumps({"ok": False, "error": "empty url"}))
        sys.exit(1)
    try:
        d = _impl_fetch_in_child_process(url, require_likes=False)
        d["html"] = d.get("html") or ""
        print(json.dumps({"ok": True, **d}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--pw-child":
        _main_child()
    elif len(sys.argv) == 2 and sys.argv[1] == "--pw-author-child":
        _main_author_child()
    else:
        sys.stderr.write("Use: python douyin_fetch_playwright.py --pw-child <stdin url>\n")
        sys.exit(2)
