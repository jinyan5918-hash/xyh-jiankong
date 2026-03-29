from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 固定为 server/app.db，避免「在仓库根目录启动 uvicorn」与「在 server 目录跑脚本」用到两个不同的库文件
_DB_FILE = Path(__file__).resolve().parent.parent / "app.db"
DATABASE_URL = "sqlite:///" + _DB_FILE.as_posix()


class Base(DeclarativeBase):
    pass


# 调度器线程与登录等请求会并发写 SQLite；默认易触发 database is locked → 500
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_wal(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
