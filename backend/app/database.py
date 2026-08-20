import os
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# 使用基于 __file__ 的绝对路径，避免依赖进程 CWD（不同目录启动不会产生"另一份数据库"）
BACKEND_DIR = Path(__file__).resolve().parent.parent
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'gomoku.db'}"
)
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enable_sqlite_pragmas(dbapi_connection, connection_record):
    """每个新连接都开启外键约束与忙等待超时。

    - foreign_keys=ON：让模型上声明的 ondelete="CASCADE" 真正生效，
      避免删除模型配置后留下悬空的 AIPlayer（孤儿），否则相关对局 AI 会卡死。
    - busy_timeout：AI 守护线程写库 + SSE 读库 + POST 写库并发时，
      遇到 'database is locked' 自动重试而非直接报错。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
