"""
抖音小程序 tt.showDouyinOpenAuth 返回的 ticket → 用户 access_token（act.xxx）。

开放平台文档（换票）：
https://developer.open-douyin.com/docs/resource/zh-CN/mini-app/develop/server/interface-request-credential/user-authorization/get-user-access-token

请求：POST https://open.douyin.com/oauth/access_token/
Content-Type: application/x-www-form-urlencoded
字段：client_key, client_secret, code=<ticket>, grant_type=authorization_code

环境变量：
- DOUYIN_OPEN_PLATFORM_CLIENT_KEY：小程序 client_key（通常与 AppID 一致，如 tt 开头）
- DOUYIN_OPEN_PLATFORM_CLIENT_SECRET：小程序 App Secret
- DOUYIN_OPENAUTH_CALLBACK_SECRET（可选）：若设置，请求须带 Header Authorization: Bearer <该值>
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/douyin/open-auth", tags=["douyin-open-platform"])

OAUTH_ACCESS_TOKEN_URL = "https://open.douyin.com/oauth/access_token/"


class TicketExchangeIn(BaseModel):
    ticket: str = Field(..., min_length=8, description="tt.showDouyinOpenAuth success 返回的 ticket，作 code 使用")


class TicketExchangeOut(BaseModel):
    ok: bool = True
    open_id: str | None = None
    access_token: str | None = None
    expires_in: int | None = None
    refresh_token: str | None = None
    refresh_expires_in: int | None = None
    scope: str | None = None
    raw_err: str | None = Field(None, description="开放平台错误信息（若有）")


def _check_callback_secret(authorization: str | None) -> None:
    secret = os.getenv("DOUYIN_OPENAUTH_CALLBACK_SECRET", "").strip()
    if not secret:
        return
    expected = f"Bearer {secret}"
    if (authorization or "").strip() != expected:
        raise HTTPException(status_code=401, detail="缺少或错误的 Authorization（需 Bearer + DOUYIN_OPENAUTH_CALLBACK_SECRET）")


def _exchange_ticket_http(ticket: str, client_key: str, client_secret: str) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": ticket.strip(),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OAUTH_ACCESS_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw = str(e)
        raise ValueError(f"HTTP {e.code}: {raw[:1200]}") from e
    return json.loads(raw)


def _parse_oauth_response(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容 data 嵌套或顶层直出。"""
    if not isinstance(payload, dict):
        raise ValueError(f"响应非 JSON 对象: {payload!r}"[:500])
    err_no = payload.get("error_code")
    if err_no is None and "err_no" in payload:
        err_no = payload.get("err_no")
    if err_no is not None and int(err_no) != 0:
        msg = (
            payload.get("description")
            or payload.get("err_msg")
            or payload.get("message")
            or str(payload)
        )
        raise ValueError(f"开放平台错误 err_no={err_no}: {msg}")
    data = payload.get("data")
    if isinstance(data, dict) and ("access_token" in data or "open_id" in data):
        return data
    if "access_token" in payload or "open_id" in payload:
        return payload
    if isinstance(data, dict):
        return data
    raise ValueError(f"无法解析换票响应: {str(payload)[:500]}")


@router.post("/ticket", response_model=TicketExchangeOut)
def exchange_ticket(
    body: TicketExchangeIn,
    authorization: str | None = Header(None, alias="Authorization"),
) -> TicketExchangeOut:
    """
    小程序 POST JSON: {\"ticket\":\"...\"}
    若配置了 DOUYIN_OPENAUTH_CALLBACK_SECRET，须带:
    Authorization: Bearer <DOUYIN_OPENAUTH_CALLBACK_SECRET>
    """
    _check_callback_secret(authorization)
    ck = os.getenv("DOUYIN_OPEN_PLATFORM_CLIENT_KEY", "").strip()
    cs = os.getenv("DOUYIN_OPEN_PLATFORM_CLIENT_SECRET", "").strip()
    if not ck or not cs:
        raise HTTPException(
            status_code=503,
            detail="服务端未配置 DOUYIN_OPEN_PLATFORM_CLIENT_KEY / DOUYIN_OPEN_PLATFORM_CLIENT_SECRET",
        )
    try:
        raw = _exchange_ticket_http(body.ticket, ck, cs)
        data = _parse_oauth_response(raw)
    except ValueError as e:
        return TicketExchangeOut(ok=False, raw_err=str(e))
    except Exception as e:
        return TicketExchangeOut(ok=False, raw_err=str(e)[:800])
    return TicketExchangeOut(
        ok=True,
        open_id=data.get("open_id"),
        access_token=data.get("access_token"),
        expires_in=_int_or_none(data.get("expires_in")),
        refresh_token=data.get("refresh_token"),
        refresh_expires_in=_int_or_none(data.get("refresh_expires_in")),
        scope=data.get("scope"),
    )


def _int_or_none(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None
