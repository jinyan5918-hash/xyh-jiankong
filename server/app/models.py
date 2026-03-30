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
    # 后台权限：none=仅客户端员工；main=主管理员；tenant=部门超级管理员（仅 /admin）
    admin_role: Mapped[str] = mapped_column(String(16), default="none", index=True)
    # 员工账号由哪位超级管理员创建（主管理员直接创建的员工为 NULL，仅主管理员可见）
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # 超级管理员自建员工可选小组标签，便于后台筛选
    staff_group: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    devices: Mapped[list["Device"]] = relationship(back_populates="user")
    monitor_tasks: Mapped[list["MonitorTask"]] = relationship(back_populates="user")


class StaffGroup(Base):
    """小组名称表：name 全库唯一。creator_tenant_id 为空表示主管理员维护的全局小组；否则为创建该条的超级管理员。"""

    __tablename__ = "staff_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    creator_tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )


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
    # 旧字段：曾用于「目标点赞」。现已改为「每增长多少赞提醒」，仍保留该列避免破坏旧库/旧客户端。
    target_likes: Mapped[int] = mapped_column(Integer)
    # 新功能：每增长多少赞提醒（可自定义）。<=0 表示不提醒。
    notify_step_likes: Mapped[int] = mapped_column(Integer, default=10)
    # 上次已提醒时的点赞数（持久化，避免服务重启后重复提醒）
    last_notified_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 单任务暂停：仍为启用任务，但调度器跳过，直至恢复（与 enabled=False 长期停用区分）
    task_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # 评论监控：上次看到的评论数与签名（持久化，避免服务重启后重复提醒）
    last_comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_comment_sig: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str] = mapped_column(Text, default="")

    task: Mapped["MonitorTask"] = relationship(back_populates="records")


class ReachAlert(Base):
    """点赞提醒（现为：每增长多少赞提醒；历史上为目标点赞）。"""

    __tablename__ = "reach_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_name: Mapped[str] = mapped_column(String(128))
    likes: Mapped[int] = mapped_column(Integer)
    target_likes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class CommentAlert(Base):
    """新评论提醒（客户端轮询后桌面通知 + 企微推送）。"""

    __tablename__ = "comment_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_name: Mapped[str] = mapped_column(String(128))
    comment_count: Mapped[int] = mapped_column(Integer)
    comment_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
