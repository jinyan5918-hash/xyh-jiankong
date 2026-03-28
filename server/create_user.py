import argparse

from app.database import SessionLocal
from app.models import User
from app.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="创建或更新员工账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--max-devices", type=int, default=2)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == args.username).first()
        if user:
            user.password_hash = hash_password(args.password)
            user.max_devices = args.max_devices
            user.is_active = True
            action = "updated"
        else:
            user = User(
                username=args.username,
                password_hash=hash_password(args.password),
                max_devices=args.max_devices,
                is_active=True,
            )
            db.add(user)
            action = "created"
        db.commit()
        print(f"{action}: username={args.username}, max_devices={args.max_devices}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
