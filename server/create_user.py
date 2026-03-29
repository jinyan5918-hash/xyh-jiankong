import argparse

from app.database import SessionLocal
from app.models import User
from app.security import hash_password
from app.wecom import is_valid_wecom_webhook_url


def main() -> None:
    parser = argparse.ArgumentParser(description="创建或更新员工账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--max-devices", type=int, default=2)
    parser.add_argument(
        "--wecom-webhook",
        default="",
        help="企业微信群机器人 Webhook（新建员工必填；更新时可省略不改）",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == args.username).first()
        if user:
            user.password_hash = hash_password(args.password)
            user.max_devices = args.max_devices
            user.is_active = True
            if args.wecom_webhook.strip():
                user.wecom_webhook_url = args.wecom_webhook.strip()
            action = "updated"
        else:
            wh = args.wecom_webhook.strip()
            if not wh or not is_valid_wecom_webhook_url(wh):
                raise SystemExit("新建用户须提供有效的 --wecom-webhook（qyapi 群机器人地址）")
            user = User(
                username=args.username,
                password_hash=hash_password(args.password),
                max_devices=args.max_devices,
                is_active=True,
                wecom_webhook_url=wh,
                admin_role="none",
                created_by_admin_id=None,
            )
            db.add(user)
            action = "created"
        db.commit()
        print(f"{action}: username={args.username}, max_devices={args.max_devices}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
