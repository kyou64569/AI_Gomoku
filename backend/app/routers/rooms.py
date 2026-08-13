from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..services.room_service import create_room as room_service_create, get_room, start_game, delete_room as delete_room_service
from ..models import Room, Game
import json

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.post("/")
def create_room(mode: str = Body(...), seats: List[dict] = Body(...), db: Session = Depends(get_db)):
    if mode not in ["pve", "watch"]:
        raise HTTPException(status_code=400, detail="mode 必须是 pve 或 watch")
    if mode == "pve" and len(seats) < 2:
        raise HTTPException(status_code=400, detail="pve 模式需要2个座位")
    if mode == "watch" and len(seats) < 2:
        raise HTTPException(status_code=400, detail="watch 模式需要2个座位")

    def _is_human(seat):
        return seat.get("player_id") in (None, 0, "human")

    human_count = sum(1 for s in seats if _is_human(s))
    if mode == "pve" and human_count != 1:
        raise HTTPException(status_code=400, detail="人机模式必须恰好选择一位玩家自己 + 一位AI")
    if mode == "watch" and human_count > 0:
        raise HTTPException(status_code=400, detail="观战模式不能选择玩家自己")

    room = room_service_create(db, mode, seats)
    return {"id": room.id, "mode": room.mode, "seats": json.loads(room.seats), "status": room.status}


@router.get("/")
def list_rooms(db: Session = Depends(get_db)):
    rooms = db.query(Room).order_by(Room.id.desc()).all()
    result = []
    for r in rooms:
        # 取该房间最近的一局 game_id 与状态，前端用于"进入"已开始的对局
        game = db.query(Game).filter(Game.room_id == r.id).order_by(Game.id.desc()).first()
        result.append({
            "id": r.id,
            "mode": r.mode,
            "seats": json.loads(r.seats),
            "status": r.status,
            "game_id": game.id if game else None,
            "game_status": game.status if game else None,
        })
    return result


@router.post("/{room_id}/start")
def start_room(room_id: int, db: Session = Depends(get_db)):
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
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
    return {"status": "deleted"}
