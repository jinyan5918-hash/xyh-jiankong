"""企业微信群机器人 Webhook 推送（手机企业微信可收）。"""

import json
import os
import urllib.error
import urllib.request


def is_valid_wecom_webhook_url(url: str) -> bool:
    u = url.strip()
    if not u:
        return True
    return u.startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=")


def send_wecom_text(webhook_url: str, content: str) -> None:
    body = json.dumps(
        {"msgtype": "text", "text": {"content": content}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        raise RuntimeError(
            f"HTTP {e.code}: {err_body or e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}") from e
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as je:
        raise RuntimeError(f"企业微信返回非 JSON: {raw[:300]}") from je
    code = data.get("errcode")
    if code not in (0, None, "0"):
        raise RuntimeError(f"企业微信返回: {raw}")


def pick_webhook_for_user(user_webhook: str | None) -> str | None:
    u = (user_webhook or "").strip()
    if u:
        return u
    env = os.getenv("ENTERPRISE_WECOM_WEBHOOK", "").strip()
    return env or None


def push_reach_alert(
    webhook_url: str,
    *,
    task_id: int,
    task_name: str,
    likes: int,
    target_likes: int,
    video_url: str,
) -> None:
    url_short = (video_url[:180] + "…") if len(video_url) > 180 else video_url
    content = (
        # 兼容历史字段名：target_likes 现语义为 step_likes（每增长多少赞提醒）
        "【抖音点赞增长提醒】\n"
        f"任务编号：#{task_id}\n"
        f"任务名称：{task_name}\n"
        f"当前点赞：{likes}\n"
        f"每增长：{target_likes} 赞提醒\n"
        f"视频链接：{url_short}"
    )
    send_wecom_text(webhook_url, content)


def push_comment_alert(
    webhook_url: str,
    *,
    task_id: int,
    task_name: str,
    comment_count: int,
    video_url: str,
    comment_snippet: str | None = None,
) -> None:
    url_short = (video_url[:180] + "…") if len(video_url) > 180 else video_url
    snippet = (comment_snippet or "").strip()
    extra = ""
    if snippet:
        snippet = snippet.replace("\r", " ").replace("\n", " ").strip()
        extra = f"最新评论：{snippet[:140]}\n"
    content = (
        "【抖音新评论提醒】\n"
        f"任务编号：#{task_id}\n"
        f"任务名称：{task_name}\n"
        f"当前评论数：{comment_count}\n"
        f"{extra}"
        f"视频链接：{url_short}"
    )
    send_wecom_text(webhook_url, content)
