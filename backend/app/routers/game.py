import json
import asyncio
import threading
import time
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from ..database import get_db, SessionLocal
from ..services.room_service import get_game, handle_move, ai_turn, get_room
from ..services.config_helper import resolve_api_key
from ..models import Game, AIPlayer, ModelConfig, Room

router = APIRouter(prefix="/api/games", tags=["games"])

# 全局锁：防止同一个 game_id 的 AI 落子被同时触发
# 存储格式：{game_id: lock_timestamp}，timestamp 为锁获取时间（monotonic 时间）
AI_LOCK_TTL = 60.0  # 锁 TTL：60 秒后自动释放（防止线程崩溃导致锁死）
ai_move_locks = {}
ai_move_locks_lock = threading.Lock()


def cleanup_ai_move_lock(game_id: int):
    """清理已结束游戏的 AI 移动锁，防止内存泄漏"""
    with ai_move_locks_lock:
        ai_move_locks.pop(game_id, None)


def is_ai_lock_expired(timestamp: float) -> bool:
    """检查锁是否已过期（超过 TTL）"""
    return time.monotonic() - timestamp > AI_LOCK_TTL


def acquire_ai_lock(game_id: int) -> bool:
    """尝试获取 AI 落子锁，返回是否成功"""
    with ai_move_locks_lock:
        current = ai_move_locks.get(game_id)
        # 如果锁不存在或已过期，可以获取
        if current is None or is_ai_lock_expired(current):
            ai_move_locks[game_id] = time.monotonic()
            return True
        return False


def cleanup_all_ai_move_locks():
    """清理所有 AI 移动锁，用于服务器关闭或定期清理"""
    with ai_move_locks_lock:
        ai_move_locks.clear()


def get_seat_by_role(db, game_id, role):
    game = get_game(db, game_id)
    if not game:
        return None
    room = get_room(db, game.room_id)
    if not room:
        return None
    seats = json.loads(room.seats)
    return next((s for s in seats if s.get("role") == role), None)


def is_human_turn(db, game_id, turn):
    role = "black" if turn == 1 else "white"
    seat = get_seat_by_role(db, game_id, role)
    if not seat:
        return False
    player_id = seat.get("player_id")
    return player_id is None or player_id == 0 or player_id == "human"


def trigger_ai_if_needed(game_id, db):
    global ai_move_locks
    if not acquire_ai_lock(game_id):
        print(f"[AI] game={game_id} skipped: lock held")
        return

    def release_lock():
        with ai_move_locks_lock:
            ai_move_locks.pop(game_id, None)
    
    try:
        game = get_game(db, game_id)
        if not game or game.status != "playing":
            print(f"[AI] game={game_id} skipped: game not playing (status={game.status if game else 'None'})")
            release_lock()
            return
        if is_human_turn(db, game_id, game.turn):
            print(f"[AI] game={game_id} skipped: human turn (turn={game.turn})")
            release_lock()
            return
        role = "black" if game.turn == 1 else "white"
        seat = get_seat_by_role(db, game_id, role)
        if not seat:
            print(f"[AI] game={game_id} skipped: no seat for {role}")
            release_lock()
            return
        player_id = seat.get("player_id")
        if not player_id:
            print(f"[AI] game={game_id} skipped: no player_id for {role}")
            release_lock()
            return
        player = db.query(AIPlayer).filter(AIPlayer.id == player_id).first()
        if not player:
            print(f"[AI] game={game_id} skipped: player {player_id} not found")
            release_lock()
            return
        config = db.query(ModelConfig).filter(ModelConfig.id == player.model_config_id).first()
        if not config:
            print(f"[AI] game={game_id} skipped: config {player.model_config_id} not found")
            release_lock()
            return
        
        print(f"[AI] game={game_id} starting AI move: player={player.name}, model={player.model_id}, turn={game.turn}")
        model_config = {
            "base_url": config.base_url,
            "api_key": resolve_api_key(config),
            "model_id": player.model_id,
            "temperature": player.temperature,
            "reasoning_effort": getattr(player, "reasoning_effort", "") or ""
        }

        def do_ai_move():
            db_local = SessionLocal()
            try:
                # 重新检查游戏状态，防止竞态条件
                game_check = get_game(db_local, game_id)
                if not game_check or game_check.status != "playing":
                    print(f"[AI] game={game_id} skipped: game not playing in thread (status={game_check.status if game_check else 'None'})")
                    return
                result = ai_turn(db_local, game_id, model_config, player.name,
                                 expected_player_id=player_id, forbidden=True)
                print(f"[AI] game={game_id} move completed: {result}")
            except Exception as e:
                print(f"[AI] game={game_id} error: {e}")
            finally:
                db_local.close()
                release_lock()

        try:
            thread = threading.Thread(target=do_ai_move, daemon=True)
            thread.start()
            print(f"[AI] game={game_id} thread started")
        except Exception as e:
            print(f"[AI] game={game_id} thread start error: {e}")
            # 线程启动失败，必须释放锁
            release_lock()
            raise
    except Exception as e:
        # 捕获任何未处理的异常，确保锁被释放。
        # 不再 raise：该函数可能在 SSE 事件循环里被调用，raise 会打断 SSE 连接，
        # 导致前端长时间收不到 turn 更新（表现为"卡在 AI 回合/思考中"）。
        print(f"[AI] game={game_id} unexpected error (ignored): {e}")
        release_lock()


@router.get("/{game_id}/state")
def get_state(game_id: int, db: Session = Depends(get_db)):
    game = get_game(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="对局不存在")
    return {
        "id": game.id,
        "room_id": game.room_id,
        "board": json.loads(game.board),
        "turn": game.turn,
        "history": json.loads(game.history),
        "winner": game.winner,
        "logs": json.loads(game.logs),
        "status": game.status,
        "scores": json.loads(game.scores)
    }


@router.post("/{game_id}/move")
def make_move(game_id: int, row: int = Body(...), col: int = Body(...), db: Session = Depends(get_db)):
    # 输入验证：坐标必须在 0-14 范围内
    if not (0 <= row <= 14 and 0 <= col <= 14):
        raise HTTPException(status_code=400, detail="坐标超出范围（0-14）")
    
    game = get_game(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="对局不存在")
    if game.status != "playing":
        raise HTTPException(status_code=400, detail="对局未在进行中")
    if not is_human_turn(db, game_id, game.turn):
        role = "黑方" if game.turn == 1 else "白方"
        raise HTTPException(status_code=400, detail=f"当前是{role}回合，请等待")
    result = handle_move(db, game_id, game.turn, row, col, "Player",
                         expected_player_id="human", forbidden=True)
    if not result:
        raise HTTPException(status_code=400, detail="非法落子（可能位置已被占用）")
    if result.status == "playing":
        trigger_ai_if_needed(game_id, db)
    return {"status": "ok"}


@router.get("/{game_id}/stream")
def stream_game(game_id: int, db: Session = Depends(get_db)):
    async def event_stream():
        last_board = None
        last_status = None
        last_ai_pending = None  # 新增：跟踪上一次推送的 AI 思考状态

        def get_ai_pending() -> bool:
            """AI 是否仍在后台线程里跑（已触发但还没落完）。"""
            with ai_move_locks_lock:
                timestamp = ai_move_locks.get(game_id)
                return timestamp is not None and not is_ai_lock_expired(timestamp)

        try:
            while True:
                # 关键修复：SSE 这个 db session 是长连接的，主线程（POST /move）
                # 和 AI daemon 线程（用 SessionLocal）提交后不会自动失效本 session 的
                # identity map 缓存。先 expire_all 让下条 SELECT 真实发 SQL 拿最新。
                try:
                    db.expire_all()
                    game = get_game(db, game_id)
                    if not game:
                        yield f"event: error\ndata: 对局不存在\n\n"
                        break

                    board = json.loads(game.board)
                    status = game.status
                    winner = game.winner
                    logs = json.loads(game.logs)

                    ai_pending = False
                    # 人机模式和观战模式：如果轮到 AI，自动触发 AI 落子
                    if status == "playing":
                        room = db.query(Room).filter(Room.id == game.room_id).first()
                        if room and room.mode in ("pve", "watch"):
                            if not is_human_turn(db, game_id, game.turn):
                                if not get_ai_pending():
                                    print(f"[SSE] game={game_id} triggering AI, turn={game.turn}")
                                    trigger_ai_if_needed(game_id, db)
                                db.refresh(game)
                                board = json.loads(game.board)
                                status = game.status
                                winner = game.winner
                                logs = json.loads(game.logs)
                                print(f"[SSE] game={game_id} after AI trigger: stones={sum(1 for r in board for c in r if c!=0)}, turn={game.turn}, status={status}")
                        # 重新查询 ai_pending：触发后到下一次循环才刷新时，可能还在算
                        ai_pending = get_ai_pending()

                    if board != last_board or status != last_status or ai_pending != last_ai_pending:
                        print(f"[SSE] game={game_id} sending update: stones={sum(1 for r in board for c in r if c!=0)}, turn={game.turn}, status={status}, ai_pending={ai_pending}")
                        payload = json.dumps({
                            "board": board,
                            "turn": game.turn,
                            "status": status,
                            "winner": winner,
                            "logs": logs[-10:],
                            "ai_pending": ai_pending,  # 新增：前端可显示「AI 思考中」
                        })
                        yield f"event: game_update\ndata: {payload}\n\n"
                        last_board = board
                        last_status = status
                        last_ai_pending = ai_pending
                    else:
                        print(f"[SSE] game={game_id} no change, skipping update (ai_pending={ai_pending})")

                    if status in ["finished", "draw"]:
                        yield f"event: game_over\ndata: {json.dumps({'winner': winner, 'status': status})}\n\n"
                        cleanup_ai_move_lock(game_id)
                        break

                    await asyncio.sleep(0.5)
                except Exception as loop_e:
                    # 单次循环异常不中断 SSE：记录后继续，避免前端收不到 turn 更新
                    # （之前任何 db/触发异常都会让整个流断开，前端卡死在旧状态）
                    print(f"[SSE] game={game_id} loop error (continue): {type(loop_e).__name__}: {loop_e}")
                    await asyncio.sleep(1)
        finally:
            # 不在 SSE 断开时清理 AI 锁：锁生命周期由 do_ai_move 线程负责
            # （受 LLM_CALL_DEADLINE=45s 总上限约束，最坏 ~46s 内必然释放）。
            # 若在此清理，SSE 断连→清锁→重连会重复触发 AI（双线程竞态，AI 反复慢思考）。
            pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")
