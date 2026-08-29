from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
import threading
from ..database import get_db
from ..services.room_service import (create_room as room_service_create, get_room, start_game,
                                     delete_room as delete_room_service, normalize_options)
from ..models import Room, Game
import json

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

# 进程内房间锁：串行化同一 room_id 的 start_room，消除"查询-创建"之间的 TOCTOU 竞态
_room_start_locks = {}
_room_start_locks_guard = threading.Lock()
_ROOM_LOCKS_MAX_SIZE = 500


def _cleanup_room_lock(room_id: int):
    """清理指定房间锁，防止内存泄漏"""
    with _room_start_locks_guard:
        _room_start_locks.pop(room_id, None)


def _get_room_lock(room_id: int) -> threading.Lock:
    with _room_start_locks_guard:
        if len(_room_start_locks) >= _ROOM_LOCKS_MAX_SIZE and room_id not in _room_start_locks:
            # 清理未加锁的空闲锁条目，防止无界膨胀
            for rid in list(_room_start_locks.keys()):
                l = _room_start_locks[rid]
                if not l.locked():
                    _room_start_locks.pop(rid, None)
                if len(_room_start_locks) < _ROOM_LOCKS_MAX_SIZE // 2:
                    break
        lock = _room_start_locks.get(room_id)
        if lock is None:
            lock = threading.Lock()
            _room_start_locks[room_id] = lock
        return lock


VALID_MODES = ("pve", "pvp", "watch")

# 允许在 options 里显式指定的键（其余键一律忽略，避免把无关字段写进房间配置）
OPTION_KEYS = ("size", "forbidden", "difficulty", "first", "swap")


def _extract_options(raw):
    """容忍两种请求体形状。

    FastAPI 对"唯一的 Body 参数"默认 embed=False —— 请求体本身就是该参数的值。
    所以 POST /rooms/{id}/start 的 OpenAPI 里 body 是裸的 {"size":9,...}，
    而前端习惯发 {"options": {...}}，后端拿到的就成了 {"options": {"size":9,...}}。
    旧代码直接把它交给 normalize_options()，由于顶层没有 size/difficulty 等键，
    会静默回落成默认值 —— 于是用户选的 9 路/简单 被覆盖成了 15 路/普通。
    这里把包裹形式解开，两种写法都能用。
    """
    if isinstance(raw, dict) and set(raw.keys()) == {"options"}:
        inner = raw.get("options")
        if inner is None or isinstance(inner, dict):
            return inner
    return raw


def merge_options(base, incoming) -> dict:
    """在既有选项之上叠加传入的键，只覆盖显式提供的那几项。

    关键：绝不整体替换。旧实现 `room.options = normalize_options(payload)`
    在 payload 形状不对时会把整份房间选项冲成默认值，属于破坏性写回。
    改成合并后，无法识别的 payload 对房间选项零影响。
    """
    merged = normalize_options(base)
    if not isinstance(incoming, dict):
        return merged
    for k in OPTION_KEYS:
        if k in incoming:
            merged[k] = incoming[k]
    return normalize_options(merged)


@router.post("/")
def create_room(mode: str = Body(...), seats: List[dict] = Body(...),
                options: dict = Body(None), db: Session = Depends(get_db)):
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail="mode 必须是 pve / pvp / watch")
    if not isinstance(seats, list) or len(seats) < 2:
        raise HTTPException(status_code=400, detail=f"{mode} 模式需要2个座位")

    def _is_human(seat):
        return seat.get("player_id") in (None, 0, "human")

    human_count = sum(1 for s in seats if _is_human(s))
    if mode == "pve" and human_count != 1:
        raise HTTPException(status_code=400, detail="人机模式必须恰好选择一位玩家自己 + 一位AI")
    if mode == "pvp" and human_count != 2:
        raise HTTPException(status_code=400, detail="真人对战模式双方都必须是「玩家自己」")
    if mode == "watch" and human_count > 0:
        raise HTTPException(status_code=400, detail="观战模式不能选择玩家自己")

    normalized = normalize_options(_extract_options(options))
    room = room_service_create(db, mode, seats, options=normalized)
    return {
        "id": room.id,
        "mode": room.mode,
        "seats": json.loads(room.seats),
        "status": room.status,
        "options": normalized,
    }


@router.get("/")
def list_rooms(db: Session = Depends(get_db)):
    # 擂台赛会批量创建 mode=arena 的房间（对局载体），它们不是用户手动建的房间，
    # 在这里过滤掉，避免污染首页房间列表；复盘可通过擂台赛详情页进入。
    rooms = db.query(Room).filter(Room.mode.in_(VALID_MODES)).order_by(Room.id.desc()).all()
    result = []
    for r in rooms:
        # 取该房间最近的一局 game_id 与状态，前端用于"进入"已开始的对局
        game = db.query(Game).filter(Game.room_id == r.id).order_by(Game.id.desc()).first()
        try:
            opts = json.loads(r.options) if r.options else {}
        except (json.JSONDecodeError, TypeError):
            opts = {}
        result.append({
            "id": r.id,
            "mode": r.mode,
            "seats": json.loads(r.seats),
            "status": r.status,
            "game_id": game.id if game else None,
            "game_status": game.status if game else None,
            "options": normalize_options(opts),
        })
    return result


@router.post("/{room_id}/start")
def start_room(room_id: int, options: dict = Body(None), db: Session = Depends(get_db)):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    # 传入 options 时"合并"进房间配置（而不是整体覆盖），让"再来一局"沿用新规则。
    # 注意本端点的 body 是裸 dict（FastAPI 对唯一 Body 参数默认 embed=False），
    # 前端发的 {"options": {...}} 会先被 _extract_options 解开。
    incoming = _extract_options(options)
    if incoming is not None:
        room.options = json.dumps(merge_options(room.options, incoming))
        db.commit()
    # 进程内锁串行化同一房间的开局，消除并发"双查-双建"竞态（TOCTOU）
    with _get_room_lock(room_id):
        # 幂等：若房间内存在"进行中"的对局，直接返回它（兼容前端缓存过期、双标签 race、
        # 重复点击等场景）。以 Game.status 为准，避免 Room.status 未同步时返回已结束的旧局。
        existing_game = db.query(Game).filter(
            Game.room_id == room_id, Game.status == "playing"
        ).order_by(Game.id.desc()).first()
        if existing_game:
            return {"game_id": existing_game.id, "status": "playing"}
        game = start_game(db, room_id)
        room.status = "playing"
        db.commit()
        return {"game_id": game.id, "status": "playing"}


@router.delete("/{room_id}")
def delete_room(room_id: int, db: Session = Depends(get_db)):
    success = delete_room_service(db, room_id)
    if not success:
        raise HTTPException(status_code=404, detail="房间不存在")
    _cleanup_room_lock(room_id)
    return {"status": "deleted"}
