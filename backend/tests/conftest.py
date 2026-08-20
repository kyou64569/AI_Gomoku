import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models import Base
from app.main import app


@pytest.fixture(scope="session")
def engine():
    # 使用内存库 + StaticPool 让所有连接共享同一份数据，便于在测试中
    # 通过 service 直接构造数据、再用 TestClient 走 HTTP 层校验。
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def _clean_db(engine):
    """每个测试前后清空所有表，消除测试间数据泄漏（session scope 内存库共享实例）。"""
    yield
    with engine.begin() as conn:
        # 严格按外键依赖顺序清空表，确保即使启用 PRAGMA foreign_keys=ON 也不会触发约束冲突：
        # 1. games (外键引用 rooms) -> 2. rooms (父表)
        # 3. ai_players (外键引用 model_configs) -> 4. model_configs (父表)
        for table in ("games", "rooms", "ai_players", "model_configs"):
            conn.execute(Base.metadata.tables[table].delete())


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
