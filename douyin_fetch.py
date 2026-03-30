"""抖音分享链接点赞数抓取（仅标准库 HTTP）。供服务端调度器使用，避免加载 tkinter / requests。"""

import base64
import hashlib
import http.cookiejar
import json
import os
import random
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

DIGG_PATTERN = re.compile(r'"digg_count"\s*:\s*(\d+)')
VIDEO_ID_PATTERNS = [
    re.compile(r"/video/(\d+)"),
    re.compile(r'"aweme_id"\s*:\s*"(\d+)"'),
    re.compile(r'"itemId"\s*:\s*"(\d+)"'),
]
COOKIE_JAR = http.cookiejar.CookieJar()
PROXY_POOL = [x.strip() for x in os.getenv("DOUYIN_PROXY_POOL", "").split(",") if x.strip()]
# 从本机浏览器开发者工具复制 Cookie（抖音页），可缓解云服务器 403；注意勿泄露
_DOUYIN_BROWSER_COOKIE = os.getenv("DOUYIN_COOKIE", "").strip()


def normalize_douyin_url(raw: str) -> str:
    url = raw.strip()
    url = re.sub(r"[\u200b-\u200d\uFEFF\r\n\t ]+", "", url)
    if not url:
        return url
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    parsed = urllib.parse.urlsplit(url)
    if not parsed.netloc:
        raise ValueError(f"链接格式无效：{raw}")
    return urllib.parse.urlunsplit(parsed)


def _b64decode_nopad(data: str) -> bytes:
    data = data + "=" * ((4 - len(data) % 4) % 4)
    return base64.b64decode(data)


def _extract_waf_cookie_value(challenge_html: str) -> str | None:
    m = re.search(r'cs="([^"]+)"', challenge_html)
    if not m:
        return None
    try:
        c = json.loads(_b64decode_nopad(m.group(1)).decode("utf-8"))
        prefix = _b64decode_nopad(c["v"]["a"])
        expect_hex = _b64decode_nopad(c["v"]["c"]).hex()
        answer = None
        for i in range(1_000_001):
            if hashlib.sha256(prefix + str(i).encode("utf-8")).hexdigest() == expect_hex:
                answer = i
                break
        if answer is None:
            return None
        c["d"] = base64.b64encode(str(answer).encode("utf-8")).decode("utf-8")
        return base64.b64encode(
            json.dumps(c, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("utf-8")
    except Exception:
        return None


def _is_waf_challenge_page(html: str) -> bool:
    return "_wafchallengeid" in html and "Please wait" in html


def _request_headers(mobile_ua: bool) -> dict[str, str]:
    ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
        if mobile_ua
        else (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    )
    h: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.douyin.com/" if not mobile_ua else "https://m.douyin.com/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if _DOUYIN_BROWSER_COOKIE:
        h["Cookie"] = _DOUYIN_BROWSER_COOKIE
    return h


def _request_text(
    url: str,
    insecure_ssl: bool = False,
    auto_fallback_ssl: bool = True,
    mobile_ua: bool = False,
    *,
    allow_ua_fallback: bool = True,
) -> tuple[str, str]:
    req = urllib.request.Request(
        url=url,
        headers=_request_headers(mobile_ua),
        method="GET",
    )

    def _open_html(use_insecure: bool) -> tuple[str, str]:
        context = None
        if use_insecure:
            context = ssl._create_unverified_context()
        handlers = [
            urllib.request.HTTPCookieProcessor(COOKIE_JAR),
            urllib.request.HTTPSHandler(context=context),
        ]
        if PROXY_POOL:
            proxy = random.choice(PROXY_POOL)
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(req, timeout=45) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            final_url = resp.geturl()

        if _is_waf_challenge_page(html):
            waf_value = _extract_waf_cookie_value(html)
            if waf_value:
                parsed = urllib.parse.urlsplit(final_url or url)
                domain = parsed.hostname or "iesdouyin.com"
                if domain.endswith("iesdouyin.com"):
                    domain = ".iesdouyin.com"
                cookie = http.cookiejar.Cookie(
                    version=0,
                    name="_wafchallengeid",
                    value=waf_value,
                    port=None,
                    port_specified=False,
                    domain=domain,
                    domain_specified=True,
                    domain_initial_dot=domain.startswith("."),
                    path="/",
                    path_specified=True,
                    secure=False,
                    expires=None,
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False,
                )
                COOKIE_JAR.set_cookie(cookie)
                with opener.open(req, timeout=45) as resp2:
                    return resp2.read().decode("utf-8", errors="ignore"), resp2.geturl()
        return html, final_url

    try:
        html, final_url = _open_html(insecure_ssl)
    except urllib.error.HTTPError as e:
        # 机房 IP 常见 403：自动换另一种 UA 再试一次（仅一次，避免死循环）
        if e.code == 403 and allow_ua_fallback:
            return _request_text(
                url,
                insecure_ssl,
                auto_fallback_ssl,
                not mobile_ua,
                allow_ua_fallback=False,
            )
        raise
    except urllib.error.URLError as e:
        if auto_fallback_ssl and not insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e):
            html, final_url = _open_html(True)
        else:
            raise
    return html, final_url


def _extract_item_id(final_url: str, html: str) -> str | None:
    for pattern in VIDEO_ID_PATTERNS:
        m = pattern.search(final_url)
        if m:
            return m.group(1)
    for pattern in VIDEO_ID_PATTERNS:
        m = pattern.search(html)
        if m:
            return m.group(1)
    return None


def _fetch_likes_by_item_api(item_id: str, insecure_ssl: bool = False) -> int | None:
    try:
        share_html, _ = _request_text(
            f"https://www.iesdouyin.com/share/video/{item_id}/",
            insecure_ssl=insecure_ssl,
            auto_fallback_ssl=True,
            mobile_ua=True,
        )
        share_matches = DIGG_PATTERN.findall(share_html)
        if share_matches:
            return max(int(x) for x in share_matches)
    except Exception:
        pass

    api_candidates = [
        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={item_id}",
        f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={item_id}",
    ]
    for api_url in api_candidates:
        try:
            body, _ = _request_text(
                api_url, insecure_ssl=insecure_ssl, auto_fallback_ssl=True
            )
            data = json.loads(body)
            if data.get("item_list"):
                return int(data["item_list"][0]["statistics"]["digg_count"])
            if data.get("aweme_detail"):
                return int(data["aweme_detail"]["statistics"]["digg_count"])
        except Exception:
            continue
    return None


def fetch_likes(url: str, insecure_ssl: bool = False, auto_fallback_ssl: bool = True) -> int:
    # 短链 / 分享页用移动端 UA 首跳，减少 403（与 _request_text 内 403 重试互为补充）
    prefer_mobile = (
        os.getenv("DOUYIN_PREFER_MOBILE_UA", "1").strip() not in ("0", "false", "no")
    )
    html, final_url = _request_text(
        url,
        insecure_ssl=insecure_ssl,
        auto_fallback_ssl=auto_fallback_ssl,
        mobile_ua=prefer_mobile,
    )

    normalized_final = final_url.rstrip("/")
    if normalized_final in {"https://www.douyin.com", "https://douyin.com"}:
        raise ValueError("短链已失效或不是视频链接（跳转到抖音首页）")

    item_id = _extract_item_id(final_url, html)
    if item_id:
        api_likes = _fetch_likes_by_item_api(item_id, insecure_ssl=insecure_ssl)
        if api_likes is not None:
            return api_likes

    matches = DIGG_PATTERN.findall(html)
    if not matches:
        raise ValueError("未解析到点赞数（请确认链接是可访问的视频分享链接）")
    return max(int(x) for x in matches)


def fetch_metrics(url: str, insecure_ssl: bool = False, auto_fallback_ssl: bool = True) -> dict:
    """兼容调度器：HTTP 路径仅返回点赞；评论相关返回 None。"""
    return {
        "likes": fetch_likes(url, insecure_ssl=insecure_ssl, auto_fallback_ssl=auto_fallback_ssl),
        "comment_count": None,
        "latest_comment": None,
    }
