"""抖音任务链接规范化（与 douyin_fetch 一致，并修复常见粘贴错误）。"""

import re
import urllib.parse


def normalize_douyin_url_safe(raw: str) -> str:
    url = raw.strip()
    url = re.sub(r"[\u200b-\u200d\uFEFF\r\n\t ]+", "", url)
    # 粘贴时偶发 https://v.https://v.douyin.com/...
    url = url.replace("https://v.https://", "https://")
    url = url.replace("http://v.https://", "https://")
    url = url.replace("https://v.http://", "http://")
    while "https://https://" in url:
        url = url.replace("https://https://", "https://", 1)
    while "http://https://" in url:
        url = url.replace("http://https://", "https://", 1)
    if url.startswith("http://") or url.startswith("https://"):
        pass
    elif url.startswith("//"):
        url = "https:" + url
    else:
        url = "https://" + url
    parsed = urllib.parse.urlsplit(url)
    if not parsed.netloc:
        raise ValueError("链接格式无效，请使用抖音视频分享页或 v.douyin.com 短链")
    return urllib.parse.urlunsplit(parsed)
