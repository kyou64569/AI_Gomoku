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
