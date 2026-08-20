import json
import random
import threading
import logging
from sqlalchemy.orm import Session
from ..models import Room, Game
from ..services import game_logic
from .llm_service import ai_move

logger = logging.getLogger(__name__)

# 进程内 per-game 落子锁：串行化同一 game_id 的 handle_move（人类 POST 与 AI daemon 线程并发时，
# 消除"读棋盘 → 校验 → 写回"之间的 TOCTOU 竞态，避免一步覆盖另一步）。
_move_locks = {}
_move_locks_guard = threading.Lock()
_MOVE_LOCKS_MAX_SIZE = 1000


def cleanup_move_lock(game_id: int):
    """清理指定对局的落子锁，防止内存泄漏"""
    with _move_locks_guard:
        _move_locks.pop(game_id, None)


def _get_move_lock(game_id: int) -> threading.Lock:
    with _move_locks_guard:
        if len(_move_locks) >= _MOVE_LOCKS_MAX_SIZE and game_id not in _move_locks:
            # 清理未加锁的空闲锁条目，防止无界膨胀
            for gid in list(_move_locks.keys()):
                l = _move_locks[gid]
                if not l.locked():
                    _move_locks.pop(gid, None)
                if len(_move_locks) < _MOVE_LOCKS_MAX_SIZE // 2:
                    break
        lock = _move_locks.get(game_id)
        if lock is None:
            lock = threading.Lock()
            _move_locks[game_id] = lock
        return lock


def create_room(db: Session, mode: str, seats: list):
    room = Room(mode=mode, seats=json.dumps(seats))
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def get_room(db: Session, room_id: int):
    room = db.query(Room).filter(Room.id == room_id).first()
    if room and room.seats:
        try:
            # Validate JSON is parseable
            json.loads(room.seats)
        except json.JSONDecodeError:
            # If corrupted, return None to prevent crashes
            return None
    return room


def delete_room(db: Session, room_id: int):
    room = db.query(Room).filter(Room.id == room_id).first()
    if room:
        # 先清理关联对局的落子锁
        game_ids = [g[0] for g in db.query(Game.id).filter(Game.room_id == room_id).all()]
        for gid in game_ids:
            cleanup_move_lock(gid)
        # 先删除关联的 Game
        db.query(Game).filter(Game.room_id == room_id).delete()
        db.delete(room)
        db.commit()
        return True
    return False


def start_game(db: Session, room_id: int):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        return None
    game = Game(room_id=room_id)
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


def get_game(db: Session, game_id: int):
    return db.query(Game).filter(Game.id == game_id).first()


def update_game_board(db: Session, game_id: int, board: list, turn: int, history: list, logs: list, winner: int = 0, status: str = "playing"):
    game = get_game(db, game_id)
    if game:
        game.board = json.dumps(board)
        game.turn = turn
        game.history = json.dumps(history)
        game.winner = winner
        game.logs = json.dumps(logs[-500:])  # 500 条 ≈ 整局（225 手×2）完整日志
        game.status = status
        db.commit()
        db.refresh(game)
    return game


def handle_move(db: Session, game_id: int, player: int, row: int, col: int,
                player_name: str = "Player", expected_player_id=None, forbidden: bool = False):
    # 串行化同一对局的落子：锁内完成"读-校验-写"，杜绝并发覆盖（TOCTOU）
    with _get_move_lock(game_id):
        return _handle_move_locked(db, game_id, player, row, col, player_name,
                                   expected_player_id, forbidden)


def _handle_move_locked(db: Session, game_id: int, player: int, row: int, col: int,
                        player_name: str, expected_player_id, forbidden: bool):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game or game.status != "playing":
        return None
    if game.turn != player:
        return None
    try:
        board = json.loads(game.board)
    except json.JSONDecodeError:
        board = game_logic.create_board()
    if not game_logic.is_valid_move(board, row, col):
        return None
    # 落子者身份校验：AI 传入具体 player_id，人类传 "human"，均与座位比对
    if expected_player_id is not None:
        room = get_room(db, game.room_id)
        try:
            seats = json.loads(room.seats) if room else []
        except json.JSONDecodeError:
            seats = []
        role = "black" if player == 1 else "white"
        seat = next((s for s in seats if s.get("role") == role), None)
        if not seat:
            return None
        sid = seat.get("player_id")
        if expected_player_id == "human":
            if sid not in (None, 0, "human"):
                return None
        elif sid != expected_player_id:
            return None
    # 专业规则禁手：黑棋禁手点拒绝落子
    if forbidden and game_logic.is_forbidden_move(board, row, col, player):
        return None
    board = game_logic.place_stone(board, row, col, player)
    try:
        history = json.loads(game.history)
    except json.JSONDecodeError:
        history = []
    history.append({"player": player, "player_name": player_name, "row": row, "col": col})
    try:
        logs = json.loads(game.logs)
    except json.JSONDecodeError:
        logs = []
    logs.append(f"{player_name} 落子 ({row},{col})")
    winner = 0
    if game_logic.check_win(board, row, col, player):
        winner = player
        game.status = "finished"
    elif not game_logic.get_empty_positions(board):
        game.status = "draw"
    # 同步房间状态：Room.status 是 start_room 幂等分支（"是否还在进行中"）的依据。
    # 若对局结束后不把 room.status 置为 finished/draw，room 会一直停留在 "playing"，
    # 导致「再来一局」时 start_room 永远命中幂等分支、返回已结束的旧 game_id，无法开新局。
    if game.status in ("finished", "draw"):
        finished_room = get_room(db, game.room_id)
        if finished_room:
            finished_room.status = game.status
    turn = 3 - player if game.status == "playing" else player
    game.board = json.dumps(board)
    game.turn = turn
    game.history = json.dumps(history)
    game.winner = winner
    game.logs = json.dumps(logs[-500:])  # 500 条 ≈ 整局（225 手×2）完整日志
    db.commit()
    db.refresh(game)
    return game


def ai_turn(db: Session, game_id: int, model_config: dict, player_name: str,
            expected_player_id=None, forbidden: bool = False):
    """AI 落子：先确定性评分引擎决策，LLM 作增强，任何异常/非法坐标都会被兜底。
    兜底时把原因写到 game.logs，前端日志面板能看见 AI 实际怎么选的。
    """
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game or game.status != "playing":
        return None
    try:
        board = json.loads(game.board)
    except json.JSONDecodeError:
        board = game_logic.create_board()
    turn = game.turn
    try:
        history = json.loads(game.history)
    except json.JSONDecodeError:
        history = []

    row, col, reason = None, None, ""
    ai_error = None
    try:
        row, col, reason = ai_move(board, turn, history, model_config, player_name,
                                   forbidden=forbidden)
    except Exception as e:
        ai_error = f"{type(e).__name__}: {e}"

    needs_random_fallback = (
        row is None
        or not (0 <= row < 15 and 0 <= col < 15)
        or board[row][col] != 0
        or (forbidden and game_logic.is_forbidden_move(board, row, col, turn))
    )
    if needs_random_fallback:
        empty = game_logic.get_empty_positions(board)
        if not empty:
            return None  # 棋盘已满，真没法下
        if forbidden:
            legal = [p for p in empty if not game_logic.is_forbidden_move(board, p[0], p[1], turn)]
            if legal:
                empty = legal
        row, col = random.choice(empty)
        causes = []
        if ai_error:
            causes.append(f"LLM 调用异常：{ai_error}")
        elif reason:
            causes.append(f"LLM 结果无效：{reason}")
        else:
            causes.append("未知原因")
        reason = f"强制兜底：random({', '.join(causes)})"

    result = handle_move(db, game_id, turn, row, col, player_name,
                         expected_player_id=expected_player_id, forbidden=forbidden)
    if result:
        # 把 AI 决策 reason 追加到 logs，前端日志面板可见
        try:
            game = get_game(db, game_id)
            if game:
                try:
                    logs = json.loads(game.logs)
                except json.JSONDecodeError:
                    logs = []
                logs.append(f"[{player_name}] {reason}")
                game.logs = json.dumps(logs[-500:])  # 500 条 ≈ 整局（225 手×2）完整日志
                db.commit()
        except Exception as e:
            # 日志追加失败不影响主流程，但必须记录，避免静默丢失决策原因
            logger.warning("AI 日志追加失败 game_id=%s: %s", game_id, e)
    else:
        logger.warning("AI 落子被拒绝 game_id=%s player=%s (%s,%s) reason=%s forbidden=%s",
                       game_id, player_name, row, col, reason, forbidden)
    return result
