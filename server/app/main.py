from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .database import Base, engine
from .deps import get_current_user, get_db
from .models import Device, MonitorRecord, MonitorTask, User
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
)
from .security import create_access_token, hash_password, verify_password
from .scheduler import scheduler


app = FastAPI(title="Douyin Monitor Auth Server", version="0.1.0")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
    _ensure_admin()


def _run_lightweight_migrations() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        if "devices" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("devices")}
            if "is_active" not in cols:
                conn.execute(text("ALTER TABLE devices ADD COLUMN is_active BOOLEAN DEFAULT 1"))


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
    return FileResponse("app/static/admin.html")


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
    task = MonitorTask(
        user_id=current_user.id,
        name=payload.name,
        video_url=payload.video_url,
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
    db.delete(task)
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
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        max_devices=payload.max_devices,
        is_active=True,
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
    for key, value in updates.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


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
