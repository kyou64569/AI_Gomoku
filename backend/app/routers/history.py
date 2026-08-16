import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Game, Room

router = APIRouter(prefix="/api/history", tags=["history"])

# 积分规则：胜 +3 / 平 +1 / 负 0
POINTS_WIN = 3
POINTS_DRAW = 1


def _get_player_names(game, room=None):
    """从对局历史/房间座位解析黑方与白方展示名称。"""
    try:
        history = json.loads(game.history)
        if not isinstance(history, list):
            history = []
    except (json.JSONDecodeError, TypeError):
        history = []
    black_name = None
    white_name = None
    for mv in history:
        name = mv.get("player_name") or "AI"
        if mv.get("player") == 1 and black_name is None:
            black_name = name
        elif mv.get("player") == 2 and white_name is None:
            white_name = name
        if black_name and white_name:
            break
    if black_name is None:
        black_name = "黑方"
    if white_name is None:
        white_name = "白方"
    return black_name, white_name


@router.get("/")
def get_history(
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500, description="最多返回的对局数量"),
    offset: int = Query(0, ge=0, description="分页偏移量")
):
    """返回对战历史（已结束对局）与积分排名。"""
    games = db.query(Game).order_by(Game.id.desc()).limit(limit).offset(offset).all()
    room_ids = {g.room_id for g in games}
    rooms = {r.id: r for r in db.query(Room).filter(Room.id.in_(room_ids)).all()} if room_ids else {}

    history = []
    records = {}  # player_name -> {games, wins, losses, draws, points}

    def touch(name):
        # 大小写不敏感：统一归并人类玩家名称
        name_lower = (name or "").lower()
        key = "玩家" if name_lower in ("player", "人类", "human") else (name or "未知")
        if key not in records:
            records[key] = {"games": 0, "wins": 0, "losses": 0, "draws": 0, "points": 0}
        return records[key]

    for g in games:
        if g.status not in ("finished", "draw"):
            continue
        room = rooms.get(g.room_id)
        black_name, white_name = _get_player_names(g, room)
        try:
            board = json.loads(g.board)
            if not isinstance(board, list) or not all(isinstance(row, list) for row in board):
                raise ValueError("Invalid board format")
            stones = sum(1 for row in board for c in row if c != 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            stones = 0
        created = g.created_at.isoformat() if g.created_at is not None else None
        history.append({
            "id": g.id,
            "room_id": g.room_id,
            "mode": room.mode if room else "pve",
            "winner": g.winner,
            "status": g.status,
            "black": black_name,
            "white": white_name,
            "stones": stones,
            "created_at": created,
        })
        # 积分统计
        r_black = touch(black_name)
        r_white = touch(white_name)
        r_black["games"] += 1
        r_white["games"] += 1
        if g.status == "draw" or g.winner == 0:
            r_black["draws"] += 1
            r_white["draws"] += 1
            r_black["points"] += POINTS_DRAW
            r_white["points"] += POINTS_DRAW
        elif g.winner == 1:
            r_black["wins"] += 1
            r_white["losses"] += 1
            r_black["points"] += POINTS_WIN
        elif g.winner == 2:
            r_white["wins"] += 1
            r_black["losses"] += 1
            r_white["points"] += POINTS_WIN

    ranking = []
    for name, rec in records.items():
        ranking.append({
            "player": name,
            **rec,
            "win_rate": round(rec["wins"] / rec["games"] * 100, 1) if rec["games"] > 0 else 0,
        })
    ranking.sort(key=lambda r: (-r["points"], -r["wins"], -r["games"], r["player"]))

    return {"games": history, "ranking": ranking}
