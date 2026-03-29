from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .database import Base, engine
from .deps import get_current_user, get_db
from .models import Device, MonitorRecord, MonitorTask, ReachAlert, User
from .urlnorm import normalize_douyin_url_safe
from .schemas import (
    LoginRequest,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    TokenResponse,
    DeviceOut,
    RecordOut,
    UserCreate,
    UserOut,
    UserUpdate,
    MonitorStatusOut,
    MonitorSettingsPatch,
    MyRecordRow,
    ReachAlertOut,
    UserNotifySettingsOut,
    UserNotifySettingsPatch,
)
from .security import create_access_token, hash_password, verify_password
from .scheduler import scheduler
from .wecom import is_valid_wecom_webhook_url, pick_webhook_for_user, send_wecom_text


app = FastAPI(title="Douyin Monitor Auth Server", version="0.1.0")

# 始终从本包下的 static 读取，避免启动时工作目录不同导致后台页面不是最新
_ADMIN_HTML = Path(__file__).resolve().parent / "static" / "admin.html"


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
    _ensure_admin()
    scheduler.start()


def _run_lightweight_migrations() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        if "devices" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("devices")}
            if "is_active" not in cols:
                conn.execute(text("ALTER TABLE devices ADD COLUMN is_active BOOLEAN DEFAULT 1"))
        if "users" in insp.get_table_names():
            ucols = {c["name"] for c in insp.get_columns("users")}
            if "monitoring_active" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN monitoring_active BOOLEAN DEFAULT 1"))
            if "monitoring_paused" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN monitoring_paused BOOLEAN DEFAULT 0"))
            if "interval_min_sec" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN interval_min_sec INTEGER"))
            if "interval_max_sec" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN interval_max_sec INTEGER"))
            if "wecom_webhook_url" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN wecom_webhook_url TEXT"))


def _ensure_admin() -> None:
    from .database import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            db.add(
                User(
                    username="admin",
                    password_hash=hash_password("Admin@123456"),
                    max_devices=3,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/admin")
def admin_console():
    return FileResponse(_ADMIN_HTML)


def _require_admin(user: User) -> None:
    if user.username != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问")


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")

    device = (
        db.query(Device)
        .filter(Device.user_id == user.id, Device.device_id == payload.device_id)
        .first()
    )
    if not device:
        active_devices_q = db.query(Device).filter(
            Device.user_id == user.id, Device.is_active.is_(True)
        )
        count = active_devices_q.count()
        if count >= user.max_devices:
            # 满额时自动下线最久未登录设备，避免管理员被锁在后台外。
            oldest = active_devices_q.order_by(Device.last_login_at.asc()).first()
            if not oldest:
                raise HTTPException(status_code=403, detail="该账号设备数已达上限")
            oldest.is_active = False
        device = Device(
            user_id=user.id,
            device_id=payload.device_id,
            device_name=payload.device_name,
            is_active=True,
        )
        db.add(device)
    else:
        if not device.is_active:
            raise HTTPException(status_code=403, detail="该设备已被管理员下线")
    device.last_login_at = datetime.utcnow()
    db.commit()

    token = create_access_token(user_id=user.id, device_id=payload.device_id)
    return TokenResponse(access_token=token)


@app.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = (
        db.query(MonitorTask)
        .filter(MonitorTask.user_id == current_user.id)
        .order_by(MonitorTask.id.desc())
        .all()
    )
    return tasks


@app.post("/tasks", response_model=TaskOut)
def create_task(
    payload: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        video_url = normalize_douyin_url_safe(payload.video_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    task = MonitorTask(
        user_id=current_user.id,
        name=payload.name,
        video_url=video_url,
        target_likes=payload.target_likes,
        enabled=payload.enabled,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@app.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = (
        db.query(MonitorTask)
        .filter(MonitorTask.id == task_id, MonitorTask.user_id == current_user.id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    updates = payload.model_dump(exclude_unset=True)
    if "video_url" in updates:
        try:
            updates["video_url"] = normalize_douyin_url_safe(updates["video_url"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    for key, value in updates.items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    return task


@app.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = (
        db.query(MonitorTask)
        .filter(MonitorTask.id == task_id, MonitorTask.user_id == current_user.id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.query(MonitorRecord).filter(MonitorRecord.task_id == task_id).delete(
        synchronize_session=False
    )
    db.query(ReachAlert).filter(
        ReachAlert.task_id == task_id,
        ReachAlert.user_id == current_user.id,
    ).delete(synchronize_session=False)
    db.delete(task)
    db.commit()
    return {"ok": True}


def _user_row(db: Session, user_id: int) -> User:
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=401, detail="用户不存在")
    return u


@app.get("/monitor/status", response_model=MonitorStatusOut)
def monitor_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = _user_row(db, current_user.id)
    st = scheduler.status()
    return MonitorStatusOut(
        monitoring_active=u.monitoring_active,
        monitoring_paused=u.monitoring_paused,
        interval_min_sec=u.interval_min_sec,
        interval_max_sec=u.interval_max_sec,
        global_scheduler_running=st["running"],
    )


@app.post("/monitor/start")
def monitor_start(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    u = _user_row(db, current_user.id)
    u.monitoring_active = True
    u.monitoring_paused = False
    db.commit()
    return {"ok": True}


@app.post("/monitor/pause")
def monitor_pause(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    u = _user_row(db, current_user.id)
    if not u.monitoring_active:
        raise HTTPException(status_code=400, detail="当前为停止状态，请先开始监控")
    u.monitoring_paused = True
    db.commit()
    return {"ok": True}


@app.post("/monitor/stop")
def monitor_stop(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    u = _user_row(db, current_user.id)
    u.monitoring_active = False
    u.monitoring_paused = False
    db.commit()
    return {"ok": True}


@app.patch("/monitor/settings")
def monitor_settings(
    payload: MonitorSettingsPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = _user_row(db, current_user.id)
    data = payload.model_dump(exclude_unset=True)
    if "interval_min_sec" in data:
        u.interval_min_sec = data["interval_min_sec"]
    if "interval_max_sec" in data:
        u.interval_max_sec = data["interval_max_sec"]
    imin = u.interval_min_sec
    imax = u.interval_max_sec
    if imin is not None and imax is not None and imin > imax:
        raise HTTPException(status_code=400, detail="间隔最小值不能大于最大值")
    db.commit()
    return {"ok": True}


@app.get("/user/notify-settings", response_model=UserNotifySettingsOut)
def get_user_notify_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = _user_row(db, current_user.id)
    w = (u.wecom_webhook_url or "").strip()
    configured = bool(w)
    # 管理员账号用 Web 后台即可，客户端不强制 Webhook
    staff = u.username != "admin"
    return UserNotifySettingsOut(
        wecom_webhook_url=u.wecom_webhook_url,
        wecom_configured=configured,
        block_operations_until_wecom=staff and not configured,
    )


@app.patch("/user/notify-settings", response_model=UserNotifySettingsOut)
def patch_user_notify_settings(
    payload: UserNotifySettingsPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = _user_row(db, current_user.id)
    if payload.wecom_webhook_url is not None:
        w = payload.wecom_webhook_url.strip() if payload.wecom_webhook_url else ""
        if u.username != "admin" and not w:
            raise HTTPException(
                status_code=400,
                detail="员工账号须填写企业微信 Webhook，不可清空；如需更换请联系管理员在后台修改。",
            )
        if w and not is_valid_wecom_webhook_url(w):
            raise HTTPException(
                status_code=400,
                detail="企业微信 Webhook 须以 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key= 开头",
            )
        u.wecom_webhook_url = w or None
    db.commit()
    db.refresh(u)
    w2 = (u.wecom_webhook_url or "").strip()
    configured = bool(w2)
    staff = u.username != "admin"
    return UserNotifySettingsOut(
        wecom_webhook_url=u.wecom_webhook_url,
        wecom_configured=configured,
        block_operations_until_wecom=staff and not configured,
    )


@app.post("/user/notify-test-wecom")
def notify_test_wecom(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """使用当前账号已保存的 Webhook 向企业微信群发一条测试消息（由服务端调用企业微信接口）。"""
    u = _user_row(db, current_user.id)
    hook = pick_webhook_for_user(u.wecom_webhook_url)
    if not hook:
        raise HTTPException(
            status_code=400,
            detail="未配置企业微信 Webhook。请在下方填写后点「保存企业微信通知」，或由管理员在后台填写后在本客户端点保存同步。",
        )
    content = (
        "【抖音监控】Webhook 测试\n"
        f"账号：{u.username}\n"
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（服务器时间）"
    )
    try:
        send_wecom_text(hook, content)
    except Exception as ex:
        raise HTTPException(
            status_code=502,
            detail=f"企业微信接口调用失败：{ex}",
        ) from ex
    return {"ok": True}


@app.get("/my/records", response_model=list[MyRecordRow])
def my_records(
    limit: int = 150,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    rows = (
        db.query(MonitorRecord, MonitorTask.name)
        .join(MonitorTask, MonitorRecord.task_id == MonitorTask.id)
        .filter(MonitorTask.user_id == current_user.id)
        .order_by(MonitorRecord.checked_at.desc())
        .limit(limit)
        .all()
    )
    return [
        MyRecordRow(
            id=r.id,
            task_id=r.task_id,
            task_name=name,
            checked_at=r.checked_at,
            likes=r.likes,
            success=r.success,
            error_message=r.error_message or "",
        )
        for r, name in rows
    ]


@app.get("/alerts/unread", response_model=list[ReachAlertOut])
def alerts_unread(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ReachAlert, MonitorTask.video_url)
        .outerjoin(
            MonitorTask,
            (MonitorTask.id == ReachAlert.task_id)
            & (MonitorTask.user_id == ReachAlert.user_id),
        )
        .filter(
            ReachAlert.user_id == current_user.id,
            ReachAlert.acknowledged.is_(False),
        )
        .order_by(ReachAlert.created_at.desc())
        .limit(50)
        .all()
    )
    out: list[ReachAlertOut] = []
    for a, vurl in rows:
        out.append(
            ReachAlertOut(
                id=a.id,
                task_id=a.task_id,
                task_name=a.task_name,
                likes=a.likes,
                target_likes=a.target_likes,
                created_at=a.created_at,
                video_url=(vurl or None),
            )
        )
    return out


@app.post("/alerts/{alert_id}/ack")
def alert_ack(
    alert_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    alert = (
        db.query(ReachAlert)
        .filter(ReachAlert.id == alert_id, ReachAlert.user_id == current_user.id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在")
    alert.acknowledged = True
    db.commit()
    return {"ok": True}


@app.get("/admin/users", response_model=list[UserOut])
def admin_list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    users = db.query(User).order_by(User.id.asc()).all()
    return users


@app.post("/admin/users", response_model=UserOut)
def admin_create_user(
    payload: UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    exists = db.query(User).filter(User.username == payload.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    w = payload.wecom_webhook_url.strip()
    if not is_valid_wecom_webhook_url(w):
        raise HTTPException(
            status_code=400,
            detail="企业微信 Webhook 须以 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key= 开头",
        )
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        max_devices=payload.max_devices,
        is_active=True,
        wecom_webhook_url=w,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.patch("/admin/users/{user_id}", response_model=UserOut)
def admin_update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        user.password_hash = hash_password(updates.pop("password"))
    if "wecom_webhook_url" in updates:
        w = updates["wecom_webhook_url"]
        w = w.strip() if isinstance(w, str) else ""
        if not w and user.username != "admin":
            raise HTTPException(
                status_code=400,
                detail="员工账号须保留企业微信 Webhook，不可清空",
            )
        if w and not is_valid_wecom_webhook_url(w):
            raise HTTPException(
                status_code=400,
                detail="企业微信 Webhook 格式不正确",
            )
        updates["wecom_webhook_url"] = w or None
    for key, value in updates.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.username == "admin":
        raise HTTPException(status_code=400, detail="不可删除管理员账号")
    db.query(ReachAlert).filter(ReachAlert.user_id == user_id).delete(synchronize_session=False)
    db.query(Device).filter(Device.user_id == user_id).delete(synchronize_session=False)
    for t in db.query(MonitorTask).filter(MonitorTask.user_id == user_id).all():
        db.delete(t)
    db.delete(user)
    db.commit()
    return {"ok": True}


@app.get("/admin/devices", response_model=list[DeviceOut])
def admin_list_devices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    devices = db.query(Device).order_by(Device.last_login_at.desc()).all()
    return devices


@app.patch("/admin/devices/{device_pk}/deactivate")
def admin_deactivate_device(
    device_pk: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    device = db.query(Device).filter(Device.id == device_pk).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    device.is_active = False
    db.commit()
    return {"ok": True}


@app.patch("/admin/devices/{device_pk}/activate")
def admin_activate_device(
    device_pk: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    device = db.query(Device).filter(Device.id == device_pk).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    device.is_active = True
    db.commit()
    return {"ok": True}


@app.get("/admin/records", response_model=list[RecordOut])
def admin_recent_records(
    limit: int = 50,
    user_id: int | None = None,
    task_id: int | None = None,
    success: bool | None = None,
    hours: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    q = db.query(MonitorRecord).join(MonitorTask, MonitorTask.id == MonitorRecord.task_id)
    if user_id is not None:
        q = q.filter(MonitorTask.user_id == user_id)
    if task_id is not None:
        q = q.filter(MonitorRecord.task_id == task_id)
    if success is not None:
        q = q.filter(MonitorRecord.success.is_(success))
    if hours is not None and hours > 0:
        since = datetime.utcnow() - timedelta(hours=hours)
        q = q.filter(MonitorRecord.checked_at >= since)
    records = q.order_by(MonitorRecord.checked_at.desc()).limit(max(1, min(limit, 500))).all()
    return records


@app.get("/admin/stats")
def admin_stats(
    hours: int = 24,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    hours = max(1, min(hours, 168))
    since = datetime.utcnow() - timedelta(hours=hours)

    total = db.query(func.count(MonitorRecord.id)).filter(MonitorRecord.checked_at >= since).scalar() or 0
    success_count = (
        db.query(func.count(MonitorRecord.id))
        .filter(MonitorRecord.checked_at >= since, MonitorRecord.success.is_(True))
        .scalar()
        or 0
    )
    failed_count = total - success_count
    success_rate = (success_count / total * 100.0) if total else 0.0

    fail_top = (
        db.query(MonitorRecord.error_message, func.count(MonitorRecord.id).label("cnt"))
        .filter(
            MonitorRecord.checked_at >= since,
            MonitorRecord.success.is_(False),
            MonitorRecord.error_message != "",
        )
        .group_by(MonitorRecord.error_message)
        .order_by(text("cnt DESC"))
        .limit(5)
        .all()
    )

    by_hour_raw = (
        db.query(
            func.strftime("%Y-%m-%d %H:00:00", MonitorRecord.checked_at).label("hour"),
            func.count(MonitorRecord.id).label("cnt"),
        )
        .filter(MonitorRecord.checked_at >= since)
        .group_by("hour")
        .order_by("hour")
        .all()
    )
    by_hour = [{"hour": h, "count": int(c)} for h, c in by_hour_raw]

    return {
        "hours": hours,
        "total_checks": int(total),
        "success_count": int(success_count),
        "failed_count": int(failed_count),
        "success_rate": round(success_rate, 2),
        "fail_top": [{"error": e, "count": int(c)} for e, c in fail_top],
        "checks_by_hour": by_hour,
    }


@app.post("/scheduler/start")
def start_scheduler(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    started = scheduler.start()
    return {"ok": True, "started": started}


@app.post("/scheduler/stop")
def stop_scheduler(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    stopped = scheduler.stop()
    return {"ok": True, "stopped": stopped}


@app.get("/scheduler/status")
def scheduler_status(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return scheduler.status()
