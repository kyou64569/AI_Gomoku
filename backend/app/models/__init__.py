from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime, timezone
import json


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类（替代废弃的 declarative_base）。"""


class ModelConfig(Base):
    __tablename__ = "model_configs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(255), nullable=False)
    # 空字符串表示"密钥不入库，从环境变量 LLM_API_KEY_{id} / LLM_API_KEY 读取"
    api_key = Column(String(255), nullable=False, default="")
    models = Column(Text, default="[]")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AIPlayer(Base):
    __tablename__ = "ai_players"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    model_config_id = Column(Integer, ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False)
    model_id = Column(String(100), nullable=False)
    temperature = Column(Integer, default=70)
    # 思考等级（reasoning 模型）：""=默认 / low / medium / high，映射到 reasoning_effort 参数。
    # 按玩家配置（而非模型配置）：列表中的模型不一定都支持思考等级。
    reasoning_effort = Column(String(20), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    config = relationship("ModelConfig")


class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String(20), nullable=False)
    seats = Column(Text, default="[]")
    status = Column(String(20), default="waiting")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    board = Column(Text, default=json.dumps([[0]*15 for _ in range(15)]))
    turn = Column(Integer, default=1)
    history = Column(Text, default="[]")
    winner = Column(Integer, default=0)
    logs = Column(Text, default="[]")
    scores = Column(Text, default='{"black":0,"white":0}')
    status = Column(String(20), default="playing")
    room = relationship("Room")
