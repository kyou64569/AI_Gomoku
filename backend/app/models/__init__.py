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
    # pve=人机 / pvp=真人对战 / watch=AI 观战
    mode = Column(String(20), nullable=False)
    seats = Column(Text, default="[]")
    status = Column(String(20), default="waiting")
    # 规则选项 JSON：{size, forbidden, difficulty, first}
    # 开局时复制到 Game，保证对局状态自洽（房间后续改选项不影响已开始的局）
    options = Column(Text, default="{}")
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    room = relationship("Room")

    # ---- 规则选项（开局时从 Room.options 复制）----
    size = Column(Integer, default=15)                 # 棋盘规格：9 / 13 / 15
    forbidden = Column(Integer, default=0)             # 禁手开关：0=自由规则 1=专业禁手
    difficulty = Column(String(20), default="normal")  # easy / normal / hard
    first = Column(Integer, default=1)                 # 先手方：1=黑 2=白
    swap = Column(Integer, default=0)                  # 三手交换开关：0=关 1=开

    # ---- 中途操作 ----
    # pending_draw：0=无待处理的求和；否则为"提出求和的一方"(1/2)
    pending_draw = Column(Integer, default=0)
    # pending_swap：1=第 3 手已落下，等待白方决定是否三手交换
    pending_swap = Column(Integer, default=0)
    # ended_reason：normal(连五) / draw(满盘) / resign(认输) / agreement(双方同意和棋)
    ended_reason = Column(String(40), default="")
    # review：AI 复盘点评（JSON 字符串），终局后按需生成并缓存
    review = Column(Text, default="")


class ArenaRun(Base):
    """AI 擂台赛：两个 AI 玩家自动对弈 N 局，用于产出模型棋力对比。"""
    __tablename__ = "arena_runs"
    id = Column(Integer, primary_key=True, index=True)
    player_a_id = Column(Integer, ForeignKey("ai_players.id"), nullable=False)
    player_b_id = Column(Integer, ForeignKey("ai_players.id"), nullable=False)
    rounds = Column(Integer, default=4)
    size = Column(Integer, default=15)
    forbidden = Column(Integer, default=0)
    difficulty = Column(String(20), default="normal")
    # 单局最大手数，超过判和（防止 LLM 对局无限拖长）
    max_moves = Column(Integer, default=80)
    # running / finished / aborted / failed
    status = Column(String(20), default="running")
    finished_games = Column(Integer, default=0)
    wins_a = Column(Integer, default=0)
    wins_b = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    # 进度明细：当前第几局 / 当前手数
    current_game = Column(Integer, default=0)
    current_moves = Column(Integer, default=0)
    error = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)


class ArenaGame(Base):
    """擂台赛中的单局结果（关联到真实的 Game 记录，可直接跳转复盘）。"""
    __tablename__ = "arena_games"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("arena_runs.id", ondelete="CASCADE"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=True)
    round_index = Column(Integer, default=0)
    # 该局里 player_a 执黑(1) 还是执白(2)
    a_color = Column(Integer, default=1)
    # 1=黑胜 2=白胜 0=和
    winner = Column(Integer, default=0)
    # 'a' / 'b' / 'draw'
    result = Column(String(20), default="")
    moves = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
