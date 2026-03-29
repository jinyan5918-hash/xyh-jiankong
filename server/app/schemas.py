from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    device_id: str = Field(min_length=4, max_length=128)
    device_name: str = Field(default="unknown", max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    max_devices: int = Field(default=2, ge=1, le=10)
    # 新员工必填：企业微信群机器人 Webhook（一对一通知到该员工所在群）
    wecom_webhook_url: str = Field(min_length=20, max_length=1024)


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    max_devices: int | None = Field(default=None, ge=1, le=10)
    is_active: bool | None = None
    wecom_webhook_url: str | None = Field(default=None, max_length=1024)


class UserOut(BaseModel):
    id: int
    username: str
    max_devices: int
    is_active: bool
    wecom_webhook_url: str | None = None
    admin_role: str = "none"
    created_by_admin_id: int | None = None

    class Config:
        from_attributes = True


class TenantAdminCreate(BaseModel):
    """主管理员创建部门超级管理员（仅登录 /admin，不可用客户端）。"""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class AdminMeOut(BaseModel):
    username: str
    admin_role: str

    class Config:
        from_attributes = True


class DeviceOut(BaseModel):
    id: int
    user_id: int
    device_id: str
    device_name: str
    is_active: bool
    last_login_at: datetime

    class Config:
        from_attributes = True


class RecordOut(BaseModel):
    id: int
    task_id: int
    checked_at: datetime
    likes: int | None
    success: bool
    error_message: str

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    video_url: str = Field(min_length=10, max_length=1024)
    target_likes: int = Field(gt=0)
    enabled: bool = True


class TaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    video_url: str | None = Field(default=None, min_length=10, max_length=1024)
    target_likes: int | None = Field(default=None, gt=0)
    enabled: bool | None = None


class TaskOut(BaseModel):
    id: int
    name: str
    video_url: str
    target_likes: int
    enabled: bool

    class Config:
        from_attributes = True


class MonitorStatusOut(BaseModel):
    monitoring_active: bool
    monitoring_paused: bool
    interval_min_sec: int | None = None
    interval_max_sec: int | None = None
    global_scheduler_running: bool


class MonitorSettingsPatch(BaseModel):
    interval_min_sec: int | None = Field(default=None, ge=30, le=3600)
    interval_max_sec: int | None = Field(default=None, ge=30, le=7200)


class MyRecordRow(BaseModel):
    id: int
    task_id: int
    task_name: str
    checked_at: datetime
    likes: int | None
    success: bool
    error_message: str


class ReachAlertOut(BaseModel):
    id: int
    task_id: int
    task_name: str
    likes: int
    target_likes: int
    created_at: datetime
    video_url: str | None = None

    class Config:
        from_attributes = True


class UserNotifySettingsOut(BaseModel):
    """企业微信等通知配置（仅本人可见）。"""

    wecom_webhook_url: str | None = None
    wecom_configured: bool = False
    # 非 admin 账号未配置 Webhook 时客户端应锁定任务/监控等操作
    block_operations_until_wecom: bool = False


class UserNotifySettingsPatch(BaseModel):
    wecom_webhook_url: str | None = Field(default=None, max_length=1024)
