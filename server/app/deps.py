from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Device, User
from .security import decode_access_token


bearer = HTTPBearer(auto_error=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
        device_id = str(payload.get("device_id", ""))
    except Exception:
        raise HTTPException(status_code=401, detail="无效或过期的登录状态")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")
    if device_id:
        device = (
            db.query(Device)
            .filter(
                Device.user_id == user.id,
                Device.device_id == device_id,
                Device.is_active.is_(True),
            )
            .first()
        )
        if not device:
            raise HTTPException(status_code=401, detail="设备已下线，请重新登录")
    return user
