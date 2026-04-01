"""
抖音开放平台 —「查询特定视频的视频数据」（服务端 OpenAPI）。

官方文档:
https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/server/basic-abilities/video-id-convert/user-video-data/video-data

接口: POST https://open.douyin.com/api/apps/v1/video/query/
Scope: ma.video.bind（需在控制台申请「视频数据查询」等能力）

重要限制（与文档一致）:
- Body 中的 item_ids **仅能查询当前用户 access_token 对应抖音账号下、该用户上传的视频**；
  不能用于任意第三方/友商未授权视频。
- 仅返回**公开**视频数据；隐私视频可能时间为 0 或不可用。
- 需用户通过小程序 tt.showDouyinOpenAuth 授权后换取用户口令 access_token（act.xxx）与 open_id。

本模块供 jiankong 调度器在配置齐全时**优先**调用，避免机房抓取 403。
未配置映射或关闭开关时，fetch_metrics_optional 返回 None，由 Playwright/HTTP 兜底。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from douyin_fetch import _extract_item_id, normalize_douyin_url

VIDEO_QUERY_URL = "https://open.douyin.com/api/apps/v1/video/query/"


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _open_item_id_map() -> dict[str, str]:
    raw = os.getenv("DOUYIN_OPENAPI_ITEM_MAP_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k.strip()] = v.strip()
    return out


def _resolve_open_item_id(video_url: str) -> str | None:
    """
    将监控任务里的分享链接映射到开放平台 item_id（加密串，通常以 @ 开头）。
    映射由管理员维护在 DOUYIN_OPENAPI_ITEM_MAP_JSON 中。
    """
    m = _open_item_id_map()
    if not m:
        return None
    url = normalize_douyin_url(video_url.strip())
    if url in m:
        return m[url]
    nu = url.rstrip("/")
    if nu in m:
        return m[nu]
    # 允许用 aweme 数字 id 做 key（与分享页解析一致时）
    # 无 HTML 时仅从 URL 解析
    aid = _extract_item_id(url, "")
    if aid and aid in m:
        return m[aid]
    return None


def _parse_video_query_response(body: dict[str, Any]) -> tuple[int, int | None]:
    err_no = int(body.get("err_no", -1))
    if err_no != 0:
        msg = str(body.get("err_msg") or "unknown")
        raise ValueError(f"抖音开放平台 video/query 业务错误 err_no={err_no} err_msg={msg}")
    data = body.get("data") or {}
    inner = data.get("data") or {}
    lst = inner.get("list")
    if not lst or not isinstance(lst, list):
        raise ValueError("抖音开放平台 video/query 返回无 list")
    first = lst[0] or {}
    stats = first.get("statistics") or {}
    likes = int(stats.get("digg_count") or 0)
    cc = stats.get("comment_count")
    try:
        comment_count = int(cc) if cc is not None else None
    except Exception:
        comment_count = None
    return likes, comment_count


def query_video_statistics(
    item_ids: list[str],
    open_id: str,
    user_access_token: str,
    timeout_sec: float = 30.0,
) -> tuple[int, int | None]:
    """
    调用官方 video/query，返回 (点赞数, 评论总数)。
    item_ids 为开放平台 open item_id（文档示例多为 @ 开头的加密串）。
    """
    if not item_ids:
        raise ValueError("item_ids 为空")
    if not open_id or not user_access_token:
        raise ValueError("open_id 或 access-token 为空")
    q = urllib.parse.urlencode({"open_id": open_id})
    url = f"{VIDEO_QUERY_URL}?{q}"
    payload = json.dumps({"item_ids": item_ids}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "access-token": user_access_token.strip(),
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            detail = str(e)
        raise ValueError(f"抖音开放平台 HTTP {e.code}: {detail}") from e
    body = json.loads(raw)
    return _parse_video_query_response(body)


def fetch_metrics_optional(video_url: str, insecure_ssl: bool = True) -> dict[str, Any] | None:
    """
    若未开启或未配置映射，返回 None（调度器继续 Playwright/HTTP）。
    否则返回与 douyin_fetch_playwright.fetch_metrics 相同结构的 dict。
    """
    del insecure_ssl
    if not _truthy("DOUYIN_USE_OPENAPI"):
        return None
    token = os.getenv("DOUYIN_OPENAPI_USER_ACCESS_TOKEN", "").strip()
    open_id = os.getenv("DOUYIN_OPENAPI_OPEN_ID", "").strip()
    if not token or not open_id:
        return None
    item_id = _resolve_open_item_id(video_url)
    if not item_id:
        return None
    likes, cc = query_video_statistics([item_id], open_id, token)
    return {
        "likes": likes,
        "comment_count": cc,
        "latest_comment": None,
    }
