from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import and_, false, func, inspect, or_, text
from sqlalchemy.orm import Session

from .database import Base, engine
from .deps import get_current_user, get_db
from .models import Device, MonitorRecord, MonitorTask, ReachAlert, User
from .urlnorm import normalize_douyin_url_safe
from .schemas import (
    AdminDeviceOut,
    AdminMetaOut,
    AdminMeOut,
    LoginRequest,
    MyRecordRow,
    MonitorSettingsPatch,
    MonitorStatusOut,
    PaginatedDevicesOut,
    PaginatedRecordsOut,
    PaginatedUsersOut,
    ReachAlertOut,
    RecordOut,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    TenantAdminBrief,
    TenantAdminCreate,
    TokenResponse,
    UserCreate,
    UserNotifySettingsOut,
    UserNotifySettingsPatch,
    UserOut,
    UserUpdate,
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
            if "admin_role" not in ucols:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN admin_role VARCHAR(16) DEFAULT 'none'")
                )
            if "created_by_admin_id" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN created_by_admin_id INTEGER"))
            if "staff_group" not in ucols:
                conn.execute(text("ALTER TABLE users ADD COLUMN staff_group VARCHAR(64)"))
            # 旧库可能为 NULL，会导致登录时 count >= None 抛 TypeError → 500
            conn.execute(text("UPDATE users SET max_devices = 2 WHERE max_devices IS NULL"))
            # admin_role 为 NULL 时，列表接口用 =='none'/'tenant' 会查不到任何行 → 后台用户表全空
            conn.execute(text("UPDATE users SET admin_role = 'none' WHERE admin_role IS NULL"))
            conn.execute(text("UPDATE users SET admin_role = 'main' WHERE username = 'admin'"))
        if "devices" in insp.get_table_names():
            conn.execute(text("UPDATE devices SET is_active = 1 WHERE is_active IS NULL"))


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
                    max_devices=5,
                    is_active=True,
                    admin_role="main",
                )
            )
            db.commit()
        elif (admin.admin_role or "none") not in ("main", "tenant"):
            admin.admin_role = "main"
            db.commit()
    finally:
        db.close()


ADMIN_CONSOLE_DEVICE_ID = "admin-web-console"


def _is_main_admin(u: User) -> bool:
    return (u.admin_role or "none") == "main"


def _is_tenant_admin(u: User) -> bool:
    return (u.admin_role or "none") == "tenant"


def _can_admin_console(u: User) -> bool:
    return _is_main_admin(u) or _is_tenant_admin(u)


def _require_admin_console(user: User) -> None:
    if not _can_admin_console(user):
        raise HTTPException(status_code=403, detail="仅管理员后台账号可访问")


def _require_main_admin(user: User) -> None:
    if not _is_main_admin(user):
        raise HTTPException(status_code=403, detail="仅主管理员可操作")


def _sql_user_is_staff_employee():
    """员工账号（含旧库 admin_role 为 NULL 的行）。"""
    return or_(User.admin_role == "none", User.admin_role.is_(None))


def _tenant_staff_ids(db: Session, tenant: User) -> list[int]:
    rows = (
        db.query(User.id)
        .filter(
            _sql_user_is_staff_employee(),
            User.created_by_admin_id == tenant.id,
        )
        .all()
    )
    return [r[0] for r in rows]


def _admin_may_access_user(db: Session, admin: User, target: User | None) -> bool:
    if not target:
        return False
    if _is_main_admin(admin):
        if target.admin_role == "main":
            return target.id == admin.id
        return True
    if _is_tenant_admin(admin):
        if target.id == admin.id:
            return True
        if (target.admin_role or "none") != "none":
            return False
        return target.created_by_admin_id == admin.id
    return False


def _records_query_scoped(db: Session, admin: User):
    q = db.query(MonitorRecord).join(MonitorTask, MonitorTask.id == MonitorRecord.task_id)
    if _is_tenant_admin(admin):
        ids = _tenant_staff_ids(db, admin)
        if not ids:
            return q.filter(false())
        q = q.filter(MonitorTask.user_id.in_(ids))
    return q


def _admin_user_search_filter(q, search: str | None):
    if not search or not (t := search.strip()):
        return q
    if t.isdigit():
        return q.filter(or_(User.id == int(t), User.username.contains(t)))
    return q.filter(User.username.contains(t))


def _fill_creator_names(db: Session, users: list[User]) -> dict[int, str]:
    ids = {u.created_by_admin_id for u in users if u.created_by_admin_id}
    if not ids:
        return {}
    rows = db.query(User.id, User.username).filter(User.id.in_(ids)).all()
    return {r[0]: r[1] for r in rows}


def _users_to_out_batch(db: Session, users: list[User]) -> list[UserOut]:
    names = _fill_creator_names(db, users)
    outs: list[UserOut] = []
    for u in users:
        cname = None
        if (u.admin_role or "none") == "none" and u.created_by_admin_id:
            cname = names.get(u.created_by_admin_id)
        outs.append(
            UserOut.model_validate(u).model_copy(update={"created_by_username": cname})
        )
    return outs


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/admin")
def admin_console():
    return FileResponse(_ADMIN_HTML)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")

    role = user.admin_role or "none"
    dev = payload.device_id.strip()
    if role in ("main", "tenant"):
        if dev != ADMIN_CONSOLE_DEVICE_ID:
            raise HTTPException(
                status_code=403,
                detail="该账号仅限浏览器打开「管理员后台」登录，不可用于 Windows 客户端",
            )
    elif dev == ADMIN_CONSOLE_DEVICE_ID:
        raise HTTPException(
            status_code=403,
            detail="员工账号请使用 Windows 客户端登录；管理员后台请使用主管理员或超级管理员账号",
        )

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
        max_slots = user.max_devices
        if max_slots is None:
            max_slots = 5 if role in ("main", "tenant") else 2
        max_slots = max(1, min(int(max_slots), 10))
        if count >= max_slots:
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
    return TokenResponse(access_token=token, admin_role=role)


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
    # 仅员工（客户端）强制 Webhook；主管理员 / 超级管理员不强制
    staff = (u.admin_role or "none") == "none"
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
        if (u.admin_role or "none") == "none" and not w:
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
    staff = (u.admin_role or "none") == "none"
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


def _purge_user_data(db: Session, uid: int) -> None:
    db.query(ReachAlert).filter(ReachAlert.user_id == uid).delete(synchronize_session=False)
    db.query(Device).filter(Device.user_id == uid).delete(synchronize_session=False)
    for t in db.query(MonitorTask).filter(MonitorTask.user_id == uid).all():
        db.delete(t)
    u = db.query(User).filter(User.id == uid).first()
    if u:
        db.delete(u)


@app.get("/admin/me", response_model=AdminMeOut)
def admin_me(current_user: User = Depends(get_current_user)):
    _require_admin_console(current_user)
    return AdminMeOut(
        username=current_user.username,
        admin_role=current_user.admin_role or "none",
    )


@app.post("/admin/tenant-admins", response_model=UserOut)
def admin_create_tenant_admin(
    payload: TenantAdminCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_main_admin(current_user)
    exists = db.query(User).filter(User.username == payload.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        max_devices=5,
        is_active=True,
        wecom_webhook_url=None,
        admin_role="tenant",
        created_by_admin_id=None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/admin/meta", response_model=AdminMetaOut)
def admin_meta(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    groups_q = db.query(User.staff_group).filter(
        _sql_user_is_staff_employee(),
        User.staff_group.isnot(None),
        User.staff_group != "",
    )
    if _is_tenant_admin(current_user):
        groups_q = groups_q.filter(User.created_by_admin_id == current_user.id)
    raw_groups = [
        r[0] for r in groups_q.distinct().order_by(User.staff_group.asc()).all()
    ]
    tenants: list[TenantAdminBrief] = []
    if _is_main_admin(current_user):
        trows = (
            db.query(User.id, User.username)
            .filter(User.admin_role == "tenant")
            .order_by(User.id.asc())
            .all()
        )
        tenants = [TenantAdminBrief(id=r[0], username=r[1]) for r in trows]
    return AdminMetaOut(tenant_admins=tenants, staff_groups=raw_groups)


@app.get("/admin/users", response_model=PaginatedUsersOut)
def admin_list_users(
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    staff_owner: str | None = None,
    staff_group: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """staff_owner：主管理员用。空=全部；unassigned=主管理员直接创建的员工；数字=某超级管理员及其员工。"""
    _require_admin_console(current_user)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    q = db.query(User)
    if _is_main_admin(current_user):
        q = q.filter(or_(User.admin_role == "tenant", _sql_user_is_staff_employee()))
        so = (staff_owner or "").strip().lower()
        if so == "unassigned":
            q = q.filter(
                or_(
                    User.admin_role == "tenant",
                    and_(_sql_user_is_staff_employee(), User.created_by_admin_id.is_(None)),
                )
            )
        elif (sow := (staff_owner or "").strip()) and sow.isdigit():
            oid = int(sow)
            q = q.filter(
                or_(
                    and_(User.admin_role == "tenant", User.id == oid),
                    and_(_sql_user_is_staff_employee(), User.created_by_admin_id == oid),
                )
            )
    else:
        q = q.filter(
            or_(User.admin_role == "none", User.admin_role.is_(None)),
            User.created_by_admin_id == current_user.id,
        )
    sg = (staff_group or "").strip()
    if sg:
        q = q.filter(
            or_(
                User.admin_role == "tenant",
                and_(_sql_user_is_staff_employee(), User.staff_group == sg),
            )
        )
    q = _admin_user_search_filter(q, search)
    total = q.count()
    rows = (
        q.order_by(User.admin_role.desc(), User.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedUsersOut(
        items=_users_to_out_batch(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@app.post("/admin/users", response_model=UserOut)
def admin_create_user(
    payload: UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    if _is_main_admin(current_user):
        owner_id = None
    elif _is_tenant_admin(current_user):
        owner_id = current_user.id
    else:
        raise HTTPException(status_code=403, detail="无权创建员工")
    exists = db.query(User).filter(User.username == payload.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    w = payload.wecom_webhook_url.strip()
    if not is_valid_wecom_webhook_url(w):
        raise HTTPException(
            status_code=400,
            detail="企业微信 Webhook 须以 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key= 开头",
        )
    sg = (payload.staff_group or "").strip() or None
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        max_devices=payload.max_devices,
        is_active=True,
        wecom_webhook_url=w,
        admin_role="none",
        created_by_admin_id=owner_id,
        staff_group=sg,
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
    _require_admin_console(current_user)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not _admin_may_access_user(db, current_user, user):
        raise HTTPException(status_code=403, detail="无权操作该用户")
    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        user.password_hash = hash_password(updates.pop("password"))
    if "wecom_webhook_url" in updates:
        w = updates["wecom_webhook_url"]
        w = w.strip() if isinstance(w, str) else ""
        if not w and (user.admin_role or "none") == "none":
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
    if "staff_group" in updates:
        if (user.admin_role or "none") != "none":
            updates.pop("staff_group")
        else:
            raw = updates.pop("staff_group")
            user.staff_group = (
                raw.strip() if isinstance(raw, str) and raw.strip() else None
            )
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
    _require_admin_console(current_user)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if (user.admin_role or "none") == "main":
        raise HTTPException(status_code=400, detail="不可删除主管理员")
    if not _admin_may_access_user(db, current_user, user):
        raise HTTPException(status_code=403, detail="无权删除该用户")
    if (user.admin_role or "none") == "tenant":
        if not _is_main_admin(current_user):
            raise HTTPException(status_code=403, detail="仅主管理员可删除超级管理员")
        for (sid,) in db.query(User.id).filter(User.created_by_admin_id == user.id).all():
            _purge_user_data(db, sid)
        _purge_user_data(db, user.id)
    else:
        _purge_user_data(db, user.id)
    db.commit()
    return {"ok": True}


@app.get("/admin/devices", response_model=PaginatedDevicesOut)
def admin_list_devices(
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    q = db.query(Device, User.username).join(User, User.id == Device.user_id)
    if _is_tenant_admin(current_user):
        ids = _tenant_staff_ids(db, current_user)
        if not ids:
            return PaginatedDevicesOut(
                items=[], total=0, page=page, page_size=page_size
            )
        q = q.filter(Device.user_id.in_(ids))
    if search and (t := search.strip()):
        if t.isdigit():
            q = q.filter(
                or_(Device.user_id == int(t), User.username.contains(t))
            )
        else:
            q = q.filter(User.username.contains(t))
    total = q.count()
    rows = (
        q.order_by(Device.last_login_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        AdminDeviceOut(
            id=d.id,
            user_id=d.user_id,
            owner_username=uname,
            device_id=d.device_id,
            device_name=d.device_name,
            is_active=d.is_active,
            last_login_at=d.last_login_at,
        )
        for d, uname in rows
    ]
    return PaginatedDevicesOut(
        items=items, total=total, page=page, page_size=page_size
    )


@app.patch("/admin/devices/{device_pk}/deactivate")
def admin_deactivate_device(
    device_pk: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    device = db.query(Device).filter(Device.id == device_pk).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    owner = db.query(User).filter(User.id == device.user_id).first()
    if not _admin_may_access_user(db, current_user, owner):
        raise HTTPException(status_code=403, detail="无权操作该设备")
    device.is_active = False
    db.commit()
    return {"ok": True}


@app.patch("/admin/devices/{device_pk}/activate")
def admin_activate_device(
    device_pk: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    device = db.query(Device).filter(Device.id == device_pk).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    owner = db.query(User).filter(User.id == device.user_id).first()
    if not _admin_may_access_user(db, current_user, owner):
        raise HTTPException(status_code=403, detail="无权操作该设备")
    device.is_active = True
    db.commit()
    return {"ok": True}


@app.get("/admin/records", response_model=PaginatedRecordsOut)
def admin_recent_records(
    page: int = 1,
    page_size: int = 10,
    user_id: int | None = None,
    task_id: int | None = None,
    success: bool | None = None,
    hours: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    q = _records_query_scoped(db, current_user)
    if user_id is not None:
        if _is_tenant_admin(current_user):
            allowed = set(_tenant_staff_ids(db, current_user))
            if user_id not in allowed:
                raise HTTPException(status_code=403, detail="无权查看该用户数据")
        q = q.filter(MonitorTask.user_id == user_id)
    if task_id is not None:
        q = q.filter(MonitorRecord.task_id == task_id)
    if success is not None:
        q = q.filter(MonitorRecord.success.is_(success))
    if hours is not None and hours > 0:
        since = datetime.utcnow() - timedelta(hours=hours)
        q = q.filter(MonitorRecord.checked_at >= since)
    total = q.count()
    records = (
        q.order_by(MonitorRecord.checked_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedRecordsOut(
        items=records,
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/admin/stats")
def admin_stats(
    hours: int = 24,
    fail_page: int = 1,
    fail_page_size: int = 10,
    hour_page: int = 1,
    hour_page_size: int = 12,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin_console(current_user)
    hours = max(1, min(hours, 168))
    fail_page = max(1, fail_page)
    fail_page_size = max(1, min(fail_page_size, 50))
    hour_page = max(1, hour_page)
    hour_page_size = max(1, min(hour_page_size, 48))
    since = datetime.utcnow() - timedelta(hours=hours)

    def _stats_base():
        return _records_query_scoped(db, current_user).filter(
            MonitorRecord.checked_at >= since
        )

    total = _stats_base().count()
    success_count = _stats_base().filter(MonitorRecord.success.is_(True)).count()
    failed_count = total - success_count
    success_rate = (success_count / total * 100.0) if total else 0.0

    fail_top_all = (
        _stats_base()
        .filter(
            MonitorRecord.success.is_(False),
            MonitorRecord.error_message != "",
        )
        .with_entities(
            MonitorRecord.error_message,
            func.count(MonitorRecord.id).label("cnt"),
        )
        .group_by(MonitorRecord.error_message)
        .order_by(text("cnt DESC"))
        .limit(200)
        .all()
    )
    fail_list = [{"error": e, "count": int(c)} for e, c in fail_top_all]
    fail_total = len(fail_list)
    f0 = (fail_page - 1) * fail_page_size
    fail_top_page = fail_list[f0 : f0 + fail_page_size]

    by_hour_raw = (
        _stats_base()
        .with_entities(
            func.strftime("%Y-%m-%d %H:00:00", MonitorRecord.checked_at).label("hour"),
            func.count(MonitorRecord.id).label("cnt"),
        )
        .group_by("hour")
        .order_by("hour")
        .all()
    )
    by_hour = [{"hour": h, "count": int(c)} for h, c in by_hour_raw]
    hour_total = len(by_hour)
    h0 = (hour_page - 1) * hour_page_size
    by_hour_page = by_hour[h0 : h0 + hour_page_size]

    return {
        "hours": hours,
        "total_checks": int(total),
        "success_count": int(success_count),
        "failed_count": int(failed_count),
        "success_rate": round(success_rate, 2),
        "fail_top": fail_top_page,
        "fail_top_total": fail_total,
        "fail_page": fail_page,
        "fail_page_size": fail_page_size,
        "checks_by_hour": by_hour_page,
        "checks_by_hour_total": hour_total,
        "hour_page": hour_page,
        "hour_page_size": hour_page_size,
    }


@app.post("/scheduler/start")
def start_scheduler(current_user: User = Depends(get_current_user)):
    _require_main_admin(current_user)
    started = scheduler.start()
    return {"ok": True, "started": started}


@app.post("/scheduler/stop")
def stop_scheduler(current_user: User = Depends(get_current_user)):
    _require_main_admin(current_user)
    stopped = scheduler.stop()
    return {"ok": True, "stopped": stopped}


@app.get("/scheduler/status")
def scheduler_status(current_user: User = Depends(get_current_user)):
    _require_admin_console(current_user)
    return scheduler.status()
