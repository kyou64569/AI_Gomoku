import json
import random
import threading
import logging
from sqlalchemy.orm import Session
from ..models import Room, Game
from ..services import game_logic
from .llm_service import ai_move, DIFFICULTY_NORMAL, DIFFICULTIES

logger = logging.getLogger(__name__)

# 进程内 per-game 落子锁：串行化同一 game_id 的 handle_move（人类 POST 与 AI daemon 线程并发时，
# 消除"读棋盘 → 校验 → 写回"之间的 TOCTOU 竞态，避免一步覆盖另一步）。
_move_locks = {}
_move_locks_guard = threading.Lock()
_MOVE_LOCKS_MAX_SIZE = 1000

# 规则选项默认值与取值域
DEFAULT_OPTIONS = {
    "size": 15,
    "forbidden": 0,
    "difficulty": DIFFICULTY_NORMAL,
    "first": 1,
    "swap": 0,
}
ALLOWED_SIZES = (9, 13, 15)


def normalize_options(raw) -> dict:
    """把前端传入的 options 规整为合法值，非法项回落到默认。

    目的是让"房间选项"永远可信：尺寸只能是 9/13/15，difficulty 只能是三档，
    first 只能是 1/2，forbidden 归一为 0/1。
    """
    opts = dict(DEFAULT_OPTIONS)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = None
    if not isinstance(raw, dict):
        return opts
    try:
        size = int(raw.get("size", opts["size"]))
        if size in ALLOWED_SIZES:
            opts["size"] = size
    except (TypeError, ValueError):
        pass
    opts["forbidden"] = 1 if raw.get("forbidden") in (1, True, "1", "true") else 0
    difficulty = raw.get("difficulty", opts["difficulty"])
    opts["difficulty"] = difficulty if difficulty in DIFFICULTIES else DIFFICULTY_NORMAL
    try:
        first = int(raw.get("first", opts["first"]))
        if first in (1, 2):
            opts["first"] = first
    except (TypeError, ValueError):
        pass
    opts["swap"] = 1 if raw.get("swap") in (1, True, "1", "true") else 0
    return opts


def game_options(game) -> dict:
    """读取一局已固化的规则选项（容错：老数据缺列时回落默认）。"""
    return {
        "size": int(getattr(game, "size", 15) or 15),
        "forbidden": 1 if getattr(game, "forbidden", 0) else 0,
        "difficulty": getattr(game, "difficulty", DIFFICULTY_NORMAL) or DIFFICULTY_NORMAL,
        "first": int(getattr(game, "first", 1) or 1),
        "swap": 1 if getattr(game, "swap", 0) else 0,
    }


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


def create_room(db: Session, mode: str, seats: list, options: dict = None):
    room = Room(mode=mode, seats=json.dumps(seats), options=json.dumps(normalize_options(options)))
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
        # 擂台赛单局记录引用了 Game（arena_games.game_id 外键 + PRAGMA foreign_keys=ON）。
        # 必须先删引用行再删 Game，否则触发 IntegrityError；漏删则会留下悬空记录，
        # 让棋力排行榜把已删除房间的对局继续算进战绩。
        if game_ids:
            from ..models import ArenaGame
            db.query(ArenaGame).filter(ArenaGame.game_id.in_(game_ids)).delete(
                synchronize_session=False
            )
        # 先删除关联的 Game
        db.query(Game).filter(Game.room_id == room_id).delete()
        db.delete(room)
        db.commit()
        return True
    return False


def start_game(db: Session, room_id: int, options: dict = None):
    """开新局。规则选项优先取入参，其次取 Room.options，最后回落默认。"""
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        return None
    raw_opts = normalize_options(options if options is not None else room.options)
    size = raw_opts["size"]
    game = Game(
        room_id=room_id,
        board=json.dumps(game_logic.create_board(size)),
        turn=raw_opts["first"],
        size=size,
        forbidden=raw_opts["forbidden"],
        difficulty=raw_opts["difficulty"],
        first=raw_opts["first"],
        pending_draw=0,
        pending_swap=0,
        swap=raw_opts["swap"],
        ended_reason="",
        review="",
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


def get_game(db: Session, game_id: int):
    return db.query(Game).filter(Game.id == game_id).first()


def _load_history(game) -> list:
    try:
        history = json.loads(game.history)
        return history if isinstance(history, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _load_logs(game) -> list:
    try:
        logs = json.loads(game.logs)
        return logs if isinstance(logs, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _rebuild_board(size: int, history: list):
    """从落子序列重建棋盘（悔棋用：比存快照更省空间且天然自洽）。"""
    board = game_logic.create_board(size)
    for mv in history:
        r, c, p = mv.get("row"), mv.get("col"), mv.get("player")
        if isinstance(r, int) and isinstance(c, int) and p in (1, 2) and game_logic.is_valid_move(board, r, c):
            board = game_logic.place_stone(board, r, c, p)
    return board


def _sync_room_status(db: Session, game):
    """对局结束/恢复时把 Room.status 同步为与 Game 一致。

    Room.status 是 start_room 幂等分支（"是否还在进行中"）的依据：
    若对局结束后不置 finished/draw，「再来一局」会一直命中幂等分支返回旧 game_id。
    """
    room = get_room(db, game.room_id)
    if room:
        room.status = game.status


def update_game_board(db: Session, game_id: int, board: list, turn: int, history: list, logs: list, winner: int = 0, status: str = "playing"):
    game = get_game(db, game_id)
    if game:
        game.board = json.dumps(board)
        game.turn = turn
        game.history = json.dumps(history)
        game.winner = winner
        game.logs = json.dumps(logs[-800:])  # 800 条：每手 3 条（落子/决策/权衡），满盘 225 手 = 675 条，取 800 留余量
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
    size = int(getattr(game, "size", 15) or 15)
    try:
        board = json.loads(game.board)
    except json.JSONDecodeError:
        board = game_logic.create_board(size)
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
    history = _load_history(game)
    history.append({"player": player, "player_name": player_name, "row": row, "col": col})
    logs = _load_logs(game)
    logs.append(f"{player_name} 落子 ({row},{col})")
    winner = 0
    ended_reason = ""
    if game_logic.check_win(board, row, col, player):
        winner = player
        game.status = "finished"
        ended_reason = "normal"
    elif not game_logic.get_empty_positions(board):
        game.status = "draw"
        ended_reason = "full"
    # 同步房间状态：Room.status 是 start_room 幂等分支（"是否还在进行中"）的依据。
    # 若对局结束后不把 room.status 置为 finished/draw，room 会一直停留在 "playing"，
    # 导致「再来一局」时 start_room 永远命中幂等分支、返回已结束的旧 game_id，无法开新局。
    if game.status in ("finished", "draw"):
        game.ended_reason = ended_reason
        game.pending_draw = 0
        _sync_room_status(db, game)
    turn = 3 - player if game.status == "playing" else player
    game.board = json.dumps(board)
    game.turn = turn
    game.history = json.dumps(history)
    game.winner = winner
    game.logs = json.dumps(logs[-800:])  # 800 条：每手 3 条（落子/决策/权衡），满盘 225 手 = 675 条，取 800 留余量
    # 三手交换：黑走完第 3 手（黑-白-黑）后，轮到白方决定要不要换边
    if game.status == "playing" and getattr(game, "swap", 0) and len(history) == 3:
        game.pending_swap = 1
        logs.append("三手交换：白方可以选择与黑方交换棋子")
        game.logs = json.dumps(logs[-800:])
    db.commit()
    db.refresh(game)
    return game


def resolve_swap(db: Session, game_id: int, accept: bool):
    """三手交换裁决。

    accept=True：把棋盘与棋谱上的黑白全部对调，并交换房间两个座位的 player_id，
    于是"原本执白的一方"变成执黑继续走。棋子的物理位置不变，变的只是归属颜色，
    因此 identity 校验（按座位比对 player_id）无需任何改动即可继续生效。
    """
    with _get_move_lock(game_id):
        game = db.query(Game).filter(Game.id == game_id).first()
        if not game or game.status != "playing" or not getattr(game, "pending_swap", 0):
            return None
        # 三手交换只能由白方（turn=2）决定
        if game.turn != 2:
            return None
        logs = _load_logs(game)
        if accept:
            size = int(getattr(game, "size", 15) or 15)
            board = _rebuild_board(size, _load_history(game))
            flipped = [[3 - cell if cell in (1, 2) else 0 for cell in row] for row in board]
            history = [
                {**mv, "player": 3 - mv["player"]} if mv.get("player") in (1, 2) else mv
                for mv in _load_history(game)
            ]
            room = get_room(db, game.room_id)
            if room:
                try:
                    seats = json.loads(room.seats or "[]")
                except (json.JSONDecodeError, TypeError):
                    seats = []
                black = next((s for s in seats if s.get("role") == "black"), None)
                white = next((s for s in seats if s.get("role") == "white"), None)
                if black and white:
                    black["player_id"], white["player_id"] = white["player_id"], black["player_id"]
                    room.seats = json.dumps(seats)
            game.board = json.dumps(flipped)
            game.history = json.dumps(history)
            logs.append("三手交换：白方选择交换，双方棋子对调")
        else:
            logs.append("三手交换：白方选择不交换")
        game.pending_swap = 0
        game.logs = json.dumps(logs[-800:])
        db.commit()
        db.refresh(game)
        return game


# ============ 中途操作：悔棋 / 认输 / 求和 ============
def undo_moves(db: Session, game_id: int, count: int = 1):
    """悔棋：按 history 回退 count 手并重建棋盘。

    人机模式通常传 2（退掉"AI 应手 + 自己那手"），真人对战传 1。
    终局后也允许悔棋（把对局拉回进行中），方便"这手不该这么下"的复盘式回退。
    """
    with _get_move_lock(game_id):
        game = db.query(Game).filter(Game.id == game_id).first()
        if not game:
            return None
        history = _load_history(game)
        if not history:
            return None
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, len(history)))
        history = history[: len(history) - count]

        opts = game_options(game)
        board = _rebuild_board(opts["size"], history)
        logs = _load_logs(game)
        logs.append(f"↩︎ 悔棋：回退 {count} 手")

        game.board = json.dumps(board)
        game.history = json.dumps(history)
        game.logs = json.dumps(logs[-800:])
        game.winner = 0
        game.status = "playing"
        game.ended_reason = ""
        game.pending_draw = 0
        game.review = ""
        # 回退后轮到"下一手该走的人"：有历史则取最后一手的对手，否则取先手方
        game.turn = (3 - history[-1]["player"]) if history else opts["first"]
        _sync_room_status(db, game)
        db.commit()
        db.refresh(game)
        return game


def resign_game(db: Session, game_id: int, player: int):
    """认输：player 判负，对手直接获胜。"""
    with _get_move_lock(game_id):
        game = db.query(Game).filter(Game.id == game_id).first()
        if not game or game.status not in ("playing", "finished", "draw"):
            return None
        if game.status in ("finished", "draw"):
            return game  # 已结束，保持原结果
        if player not in (1, 2):
            return None
        logs = _load_logs(game)
        logs.append(f"{'黑方' if player == 1 else '白方'}认输")
        game.logs = json.dumps(logs[-800:])
        game.winner = 3 - player
        game.status = "finished"
        game.ended_reason = "resign"
        game.pending_draw = 0
        _sync_room_status(db, game)
        db.commit()
        db.refresh(game)
        return game


def offer_draw(db: Session, game_id: int, player: int):
    """提出求和：记录提出方，等待对手回应。

    返回 (game, resolved)：resolved=True 表示已即时判定（AI 已自动回应）。
    """
    with _get_move_lock(game_id):
        game = db.query(Game).filter(Game.id == game_id).first()
        if not game or game.status != "playing":
            return None, False
        if player not in (1, 2):
            return None, False
        game.pending_draw = player
        logs = _load_logs(game)
        logs.append(f"{'黑方' if player == 1 else '白方'}提出求和")
        game.logs = json.dumps(logs[-800:])
        db.commit()
        db.refresh(game)
        return game, False


def respond_draw(db: Session, game_id: int, player: int, accept: bool):
    """回应求和：accept=True 判和，False 则撤销提议继续对局。"""
    with _get_move_lock(game_id):
        game = db.query(Game).filter(Game.id == game_id).first()
        if not game or game.status != "playing" or not game.pending_draw:
            return None
        if player == game.pending_draw:
            return None  # 提出方不能自己接受
        logs = _load_logs(game)
        if accept:
            game.status = "draw"
            game.winner = 0
            game.ended_reason = "agreement"
            logs.append("双方同意和棋")
            _sync_room_status(db, game)
        else:
            logs.append(f"{'黑方' if player == 1 else '白方'}拒绝求和，对局继续")
        game.pending_draw = 0
        game.logs = json.dumps(logs[-800:])
        db.commit()
        db.refresh(game)
        return game


def ai_draw_decision(board, turn) -> bool:
    """人机对弈里 AI 对求和的裁决：自己没有明显攻势就接受，否则拒绝。

    用引擎静态分判断：己方最强一手若连"活三"都够不上，说明局面不占优 → 接受和棋。
    """
    from .llm_service import _static_candidates, SCORE_LIVE_THREE
    try:
        top = _static_candidates(board, turn, forbidden=False)
    except Exception:
        return True
    if not top:
        return True
    return top[0][3] < SCORE_LIVE_THREE


def _deliberation_log(board, turn, chosen, forbidden=False, k=3):
    """生成「AI 在这几个点之间权衡」的解说日志（改进建议 3.2）。

    把引擎 top-K 候选摊开给观众看，而不只是甩一个坐标：能直观看到
    AI 为什么放弃次优点、LLM 与引擎的分歧在哪。
    """
    try:
        from .llm_service import engine_top_moves
        top = engine_top_moves(board, turn, k=k, forbidden=forbidden)
    except Exception:
        return []
    if len(top) < 2:
        return []
    parts = []
    for i, t in enumerate(top):
        mark = "← 选择" if (t["row"], t["col"]) == chosen else ("★引擎首选" if i == 0 else "")
        parts.append(f"({t['row']},{t['col']}){t['reason'].split(' ')[0]}{mark}")
    return ["　└ 权衡：" + "，".join(parts)]


def ai_turn(db: Session, game_id: int, model_config: dict, player_name: str,
            expected_player_id=None, forbidden: bool = False,
            difficulty: str = DIFFICULTY_NORMAL):
    """AI 落子：先确定性评分引擎决策，LLM 作增强，任何异常/非法坐标都会被兜底。
    兜底时把原因写到 game.logs，前端日志面板能看见 AI 实际怎么选的。
    """
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game or game.status != "playing":
        return None
    size = int(getattr(game, "size", 15) or 15)
    try:
        board = json.loads(game.board)
    except json.JSONDecodeError:
        board = game_logic.create_board(size)
    turn = game.turn
    history = _load_history(game)

    row, col, reason = None, None, ""
    ai_error = None
    try:
        row, col, reason = ai_move(board, turn, history, model_config, player_name,
                                   forbidden=forbidden, difficulty=difficulty)
    except Exception as e:
        ai_error = f"{type(e).__name__}: {e}"

    needs_random_fallback = (
        row is None
        or not (0 <= row < size and 0 <= col < size)
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
        # 把 AI 决策 reason 追加到 logs，前端日志面板可见。
        # 困难档是纯引擎，不再重复展示候选（引擎自己已在 reason 里说明棋型）。
        extras = []
        if difficulty != "hard":
            extras.extend(_deliberation_log(board, turn, (row, col), forbidden))
        try:
            game = get_game(db, game_id)
            if game:
                logs = _load_logs(game)
                logs.append(f"[{player_name}] {reason}")
                logs.extend(extras)
                game.logs = json.dumps(logs[-800:])  # 800 条：每手 3 条（落子/决策/权衡），满盘 225 手 = 675 条，取 800 留余量
                db.commit()
        except Exception as e:
            # 日志追加失败不影响主流程，但必须记录，避免静默丢失决策原因
            logger.warning("AI 日志追加失败 game_id=%s: %s", game_id, e)
    else:
        logger.warning("AI 落子被拒绝 game_id=%s player=%s (%s,%s) reason=%s forbidden=%s",
                       game_id, player_name, row, col, reason, forbidden)
    return result
