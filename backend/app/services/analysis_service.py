"""复盘分析：棋力评估报告（2.4）+ AI 点评复盘（2.3）。

- analyze_game()：逐手用确定性引擎打分，算出"这一手相对最优手的损失"，
  产出失误分级（最佳/好手/疑问手/失误/败着）、按棋手与阶段（开局/中盘/收官）的统计，
  以及一个 0-100 的水平评分。完全不依赖 LLM，秒级返回。
- review_game()：终局后调一次 LLM，传入棋谱 + 关键失误手，产出结构化点评
  （总评 + 关键手分析 + 改进建议），结果缓存在 games.review 避免重复烧 token。
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Game, Room, AIPlayer, ModelConfig
from . import game_logic
from .config_helper import resolve_api_key
from .llm_service import (
    _static_candidates, evaluate_point, engine_top_moves, chat_json,
    SCORE_DEAD_THREE, SCORE_LIVE_THREE, SCORE_LIVE_FOUR, DEFEND_WEIGHT,
)

logger = logging.getLogger(__name__)

# 失误分级阈值（按"相对最优手的分数损失"划分）
LABELS = {
    "best": "最佳",
    "good": "好手",
    "inaccuracy": "疑问手",
    "mistake": "失误",
    "blunder": "败着",
}
# 分档权重：用于折算水平评分（败着扣得最狠）
_LABEL_PENALTY = {"best": 0, "good": 0.5, "inaccuracy": 2, "mistake": 5, "blunder": 12}


def _classify(loss: int) -> str:
    if loss <= 0:
        return "best"
    if loss < SCORE_DEAD_THREE:
        return "good"
    if loss < SCORE_LIVE_THREE:
        return "inaccuracy"
    if loss < SCORE_LIVE_FOUR:
        return "mistake"
    return "blunder"


def _phase(index: int, total: int) -> str:
    """阶段划分：开局前 10 手，收官为最后 25%，其余为中盘。"""
    if index < 10:
        return "opening"
    if index >= max(12, int(total * 0.75)):
        return "endgame"
    return "middlegame"


PHASE_LABELS = {"opening": "开局", "middlegame": "中盘", "endgame": "收官"}


def _load(game, attr, default):
    try:
        val = json.loads(getattr(game, attr) or "")
        return val if isinstance(val, list) else default
    except (json.JSONDecodeError, TypeError):
        return default


def analyze_game(db: Session, game_id: int) -> dict:
    """逐手评分，产出本局的棋力评估报告。"""
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return None
    size = int(getattr(game, "size", 15) or 15)
    history = _load(game, "history", [])
    if not history:
        return {"moves": [], "summary": {}, "players": {}, "size": size}

    total = len(history)
    board = game_logic.create_board(size)
    moves = []
    per_player = {
        1: {"moves": 0, "loss_sum": 0, "labels": {}, "phases": {}},
        2: {"moves": 0, "loss_sum": 0, "labels": {}, "phases": {}},
    }

    for idx, mv in enumerate(history):
        player = mv.get("player")
        row, col = mv.get("row"), mv.get("col")
        if player not in (1, 2) or not isinstance(row, int) or not isinstance(col, int):
            continue
        if not game_logic.is_valid_move(board, row, col):
            # 数据异常（悔棋/并发残留）：跳过，避免分析崩溃
            continue
        enemy = 3 - player

        # 该手之前的局面 -> 最优手得分
        ranked = _static_candidates(board, player, forbidden=False)
        best_total = ranked[0][2] if ranked else 0
        best_row, best_col = (ranked[0][0], ranked[0][1]) if ranked else (None, None)

        # 实际落子的得分（口径与最优手一致：攻 + 防×权重 + 中心偏置）
        actual_total, attack, defend = evaluate_point(board, row, col, player)
        loss = max(0, best_total - actual_total)
        label = _classify(loss)
        phase = _phase(idx, total)

        moves.append({
            "index": idx,
            "move_no": idx // 2 + 1,
            "player": player,
            "player_name": mv.get("player_name") or ("黑方" if player == 1 else "白方"),
            "row": row,
            "col": col,
            "score": actual_total,
            "best_score": best_total,
            "best_row": best_row,
            "best_col": best_col,
            "loss": loss,
            "label": label,
            "label_text": LABELS[label],
            "phase": phase,
            "phase_text": PHASE_LABELS[phase],
        })

        bucket = per_player[player]
        bucket["moves"] += 1
        bucket["loss_sum"] += loss
        bucket["labels"][label] = bucket["labels"].get(label, 0) + 1
        ph = bucket["phases"].setdefault(phase, {"moves": 0, "loss_sum": 0, "penalty": 0})
        ph["moves"] += 1
        ph["loss_sum"] += loss
        ph["penalty"] += _LABEL_PENALTY[label]

        board = game_logic.place_stone(board, row, col, player)

    players = {}
    for p, b in per_player.items():
        if b["moves"] == 0:
            players[str(p)] = None
            continue
        penalty = sum(_LABEL_PENALTY[k] * v for k, v in b["labels"].items())
        rating = max(0, min(100, round(100 - penalty * 100 / max(1, b["moves"]))))
        good_moves = b["labels"].get("best", 0) + b["labels"].get("good", 0)
        players[str(p)] = {
            "player": p,
            "name": next((m["player_name"] for m in moves if m["player"] == p), "—"),
            "moves": b["moves"],
            "avg_loss": int(b["loss_sum"] / b["moves"]),
            "accuracy": round(good_moves / b["moves"] * 100, 1),
            "rating": rating,
            "labels": {LABELS[k]: v for k, v in b["labels"].items() if v},
            "phases": {
                PHASE_LABELS[k]: {
                    "moves": v["moves"],
                    "avg_loss": int(v["loss_sum"] / v["moves"]),
                    "rating": max(0, min(100, round(100 - v["penalty"] * 100 / max(1, v["moves"])))),
                }
                for k, v in b["phases"].items()
            },
        }

    blunders = [m for m in moves if m["label"] == "blunder"]
    summary = {
        "size": size,
        "total_moves": len(moves),
        "status": game.status,
        "winner": game.winner,
        "ended_reason": getattr(game, "ended_reason", "") or "",
        "blunders": len(blunders),
        "worst": sorted(moves, key=lambda m: -m["loss"])[:3],
    }
    return {"moves": moves, "players": players, "summary": summary}


# ============ AI 点评复盘 ============
REVIEW_SYSTEM = "你是资深五子棋教练。只返回合法 JSON，不要输出任何其他文字，不要用 markdown 代码块。"

REVIEW_TEMPLATE = """请点评这局{n}路五子棋（黑=X 先手，白=O）。

【对局信息】
- 黑方：{black}　白方：{white}
- 结果：{result}
- 共 {total} 手

【完整棋谱】
{history}

【引擎判定的关键失误手】（loss 越大越严重）
{blunders}

请给出 JSON，严格遵循以下结构：
{{
  "summary": "一句话总评（60 字内）",
  "turning_point": "第几手是转折点、为什么（80 字内）",
  "key_moments": [
    {{"move_no": 12, "player": "黑方", "comment": "这手为什么不好（50 字内）", "better": "更优的下法与理由（50 字内）"}}
  ],
  "advice": ["给黑方的 1 条改进建议", "给白方的 1 条改进建议"]
}}
要求：key_moments 最多 3 条，优先评论上面列出的关键失误手；advice 必须是具体的、可执行的下棋建议，不要空泛。
"""


def _board_letters(history, size):
    """把棋谱压缩成"手数 方 坐标"列表，避免把整个棋盘塞进 prompt。"""
    lines = []
    for i, mv in enumerate(history):
        side = "黑" if mv.get("player") == 1 else "白"
        lines.append(f"{i + 1}. {side}({mv.get('row')},{mv.get('col')})")
    return " ".join(lines)


def pick_reviewer(db: Session, game: Game):
    """挑选用于点评的模型：优先用本局执子 AI 的配置，其次任意已配置 AI 玩家。"""
    candidate_ids = []
    room = db.query(Room).filter(Room.id == game.room_id).first()
    if room:
        try:
            seats = json.loads(room.seats or "[]")
        except (json.JSONDecodeError, TypeError):
            seats = []
        candidate_ids = [s.get("player_id") for s in seats
                         if s.get("player_id") not in (None, 0, "human", "")]
    players = []
    if candidate_ids:
        players = db.query(AIPlayer).filter(AIPlayer.id.in_(candidate_ids)).all()
    if not players:
        players = db.query(AIPlayer).order_by(AIPlayer.id.desc()).limit(1).all()
    for p in players:
        cfg = db.query(ModelConfig).filter(ModelConfig.id == p.model_config_id).first()
        if cfg and cfg.base_url:
            return p, cfg
    return None, None


def review_game(db: Session, game_id: int, force: bool = False) -> dict:
    """生成（或读取缓存的）AI 复盘点评。

    返回 {"status": "ok", "review": {...}} 或 {"status": "error", "detail": "..."}
    """
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return {"status": "error", "detail": "对局不存在"}

    if not force and getattr(game, "review", ""):
        try:
            cached = json.loads(game.review)
            if isinstance(cached, dict) and cached:
                return {"status": "ok", "review": cached, "cached": True}
        except (json.JSONDecodeError, TypeError):
            pass

    history = _load(game, "history", [])
    if len(history) < 4:
        return {"status": "error", "detail": "对局手数太少（<4 手），无需点评"}

    player, cfg = pick_reviewer(db, game)
    if not player or not cfg:
        return {"status": "error", "detail": "没有可用的 AI 玩家配置，无法生成点评"}

    analysis = analyze_game(db, game_id) or {}
    moves = analysis.get("moves", [])
    worst = sorted(moves, key=lambda m: -m["loss"])[:3]
    blunders_text = "\n".join(
        f"- 第{m['move_no']}手 {m['player_name']} 下在 ({m['row']},{m['col']})，"
        f"引擎认为最优是 ({m['best_row']},{m['best_col']})，损失 {m['loss']}（{m['label_text']}）"
        for m in worst
    ) or "（本局没有明显的重大失误）"

    size = int(getattr(game, "size", 15) or 15)
    black_name = next((m["player_name"] for m in moves if m["player"] == 1), "黑方")
    white_name = next((m["player_name"] for m in moves if m["player"] == 2), "白方")
    reason = getattr(game, "ended_reason", "") or ""
    if game.status == "draw":
        result = "和棋" + ("（双方同意）" if reason == "agreement" else "（棋盘填满）")
    elif game.winner == 1:
        result = f"黑方 {black_name} 胜" + ("（白方认输）" if reason == "resign" else "")
    elif game.winner == 2:
        result = f"白方 {white_name} 胜" + ("（黑方认输）" if reason == "resign" else "")
    else:
        result = "未结束"

    prompt = REVIEW_TEMPLATE.format(
        n=size,
        black=black_name,
        white=white_name,
        result=result,
        total=len(history),
        history=_board_letters(history, size),
        blunders=blunders_text,
    )

    data = chat_json(
        cfg.base_url, resolve_api_key(cfg), player.model_id,
        REVIEW_SYSTEM, prompt,
        reasoning_effort=getattr(player, "reasoning_effort", "") or "",
        # 协议类型 + 思考方言/能力声明挂在模型配置上（见 thinking_profiles / native_protocols）
        provider=getattr(cfg, "provider", "") or "openai",
        thinking_profile=getattr(cfg, "thinking_profile", "") or "openai-native",
        thinking_capability=getattr(cfg, "thinking_capability", "") or "graded",
    )
    if "_error" in data:
        return {"status": "error", "detail": data["_error"]}

    review = {
        "summary": str(data.get("summary", ""))[:300],
        "turning_point": str(data.get("turning_point", ""))[:300],
        "key_moments": [
            {
                "move_no": m.get("move_no"),
                "player": str(m.get("player", ""))[:20],
                "comment": str(m.get("comment", ""))[:200],
                "better": str(m.get("better", ""))[:200],
            }
            for m in (data.get("key_moments") or [])[:3] if isinstance(m, dict)
        ],
        "advice": [str(a)[:200] for a in (data.get("advice") or [])[:4]],
        "reviewer": player.name,
        "model": player.model_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        game.review = json.dumps(review, ensure_ascii=False)
        db.commit()
    except Exception as e:
        logger.warning("点评缓存写入失败 game_id=%s: %s", game_id, e)
        db.rollback()
    return {"status": "ok", "review": review, "cached": False}
