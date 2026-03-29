from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    max_devices: Mapped[int] = mapped_column(Integer, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # 客户端「开始 / 暂停 / 停止」：仅影响当前用户任务是否参与调度。
    monitoring_active: Mapped[bool] = mapped_column(Boolean, default=True)
    monitoring_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # 为空则使用服务端全局 SCHED_INTERVAL_* 环境变量。
    interval_min_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_max_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 企业微信群机器人 Webhook，达标时服务端推送（手机企业微信可见）。
    wecom_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    devices: Mapped[list["Device"]] = relationship(back_populates="user")
    monitor_tasks: Mapped[list["MonitorTask"]] = relationship(back_populates="user")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_user_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    device_name: Mapped[str] = mapped_column(String(128), default="unknown")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_login_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="devices")


class MonitorTask(Base):
    __tablename__ = "monitor_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    video_url: Mapped[str] = mapped_column(Text)
    target_likes: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="monitor_tasks")
    records: Mapped[list["MonitorRecord"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class MonitorRecord(Base):
    __tablename__ = "monitor_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("monitor_tasks.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str] = mapped_column(Text, default="")

    task: Mapped["MonitorTask"] = relationship(back_populates="records")


class ReachAlert(Base):
    """点赞达标待推送（客户端轮询后桌面通知）。"""

    __tablename__ = "reach_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_name: Mapped[str] = mapped_column(String(128))
    likes: Mapped[int] = mapped_column(Integer)
    target_likes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
