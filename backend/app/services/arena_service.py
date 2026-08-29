"""AI 擂台赛（2.1）：让两个已配置的 AI 玩家自动对弈 N 局，产出模型棋力对比。

设计取舍：
- 对局在**单个后台线程里串行**跑，而不是并发开 N 局。原因是 LLM 端点普遍有 RPM /
  并发限制（实测商汤 5h 500 次），并发跑会直接把账号打进 429；串行虽然总时长更长，
  但结果稳定、可预期，且进度可实时观测。
- 双方**轮流执黑**（第 i 局交换先手），消除五子棋先手优势带来的偏差。
- 每局有 max_moves 上限，超过判和，避免 LLM 在残局里互相绕圈无限拖长。
- 对局复用真实的 Game 记录，结束后可直接跳到复盘页看逐手回放。

并发控制说明：
- 擂台赛游戏使用 mode="arena" 标识，与常规游戏隔离
- 擂台赛在单一后台线程中串行执行，不存在并发访问同一游戏的情况
- 因此不需要使用常规游戏的 ai_move_locks 机制（该锁是为防止人类 POST 与 AI daemon
  线程并发竞态而设计）
- 擂台赛自身的并发控制通过 _active_lock 保证同一时刻只有一个擂台赛运行
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Room, Game, AIPlayer, ModelConfig, ArenaRun, ArenaGame
from .config_helper import resolve_api_key
from .room_service import start_game, ai_turn, get_game, game_options, normalize_options

logger = logging.getLogger(__name__)

# 同一时刻只允许一个擂台赛在跑（保护 LLM 配额，也让进度语义明确）
_active_lock = threading.Lock()
_active_run_id = None
_abort_events = {}


def is_running() -> bool:
    with _active_lock:
        return _active_run_id is not None


def current_run_id():
    with _active_lock:
        return _active_run_id


def _set_active(run_id):
    global _active_run_id
    with _active_lock:
        _active_run_id = run_id


def abort_run(run_id: int) -> bool:
    """请求中止擂台赛：置 Event，后台线程在下一手开始前退出。"""
    ev = _abort_events.get(run_id)
    if ev:
        ev.set()
        return True
    return False


def _build_model_config(db: Session, player: AIPlayer):
    cfg = db.query(ModelConfig).filter(ModelConfig.id == player.model_config_id).first()
    if not cfg:
        return None
    return {
        "base_url": cfg.base_url,
        "api_key": resolve_api_key(cfg),
        "model_id": player.model_id,
        "temperature": player.temperature,
        "reasoning_effort": getattr(player, "reasoning_effort", "") or "",
    }


def start_run(db: Session, player_a_id: int, player_b_id: int, rounds: int = 4,
              size: int = 15, forbidden: int = 0, difficulty: str = "normal",
              max_moves: int = 80) -> dict:
    """创建一个擂台赛并在后台线程开始对弈。"""
    if is_running():
        return {"status": "error", "detail": f"已有擂台赛正在运行（run #{current_run_id()}），请先中止或等待结束"}
    if player_a_id == player_b_id:
        return {"status": "error", "detail": "请选择两个不同的 AI 玩家"}
    pa = db.query(AIPlayer).filter(AIPlayer.id == player_a_id).first()
    pb = db.query(AIPlayer).filter(AIPlayer.id == player_b_id).first()
    if not pa or not pb:
        return {"status": "error", "detail": "AI 玩家不存在"}

    rounds = max(1, min(20, int(rounds)))
    max_moves = max(10, min(225, int(max_moves)))
    opts = normalize_options({"size": size, "forbidden": forbidden,
                              "difficulty": difficulty, "first": 1})

    run = ArenaRun(
        player_a_id=player_a_id,
        player_b_id=player_b_id,
        rounds=rounds,
        size=opts["size"],
        forbidden=opts["forbidden"],
        difficulty=opts["difficulty"],
        max_moves=max_moves,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    ev = threading.Event()
    _abort_events[run.id] = ev
    _set_active(run.id)

    thread = threading.Thread(target=_run_worker, args=(run.id, ev), daemon=True)
    thread.start()
    return {"status": "ok", "run_id": run.id}


def _run_worker(run_id: int, abort_event: threading.Event):
    db = SessionLocal()
    try:
        run = db.query(ArenaRun).filter(ArenaRun.id == run_id).first()
        if not run:
            return
        pa = db.query(AIPlayer).filter(AIPlayer.id == run.player_a_id).first()
        pb = db.query(AIPlayer).filter(AIPlayer.id == run.player_b_id).first()
        if not pa or not pb:
            run.status = "failed"
            run.error = "AI 玩家不存在"
            db.commit()
            return

        cfg_a = _build_model_config(db, pa)
        cfg_b = _build_model_config(db, pb)
        if not cfg_a or not cfg_b:
            run.status = "failed"
            run.error = "AI 玩家缺少可用的模型配置"
            db.commit()
            return

        for i in range(run.rounds):
            if abort_event.is_set():
                break
            # 轮流执黑：偶数局 A 执黑，奇数局 B 执黑
            a_color = 1 if i % 2 == 0 else 2
            try:
                winner = _play_one_game(db, run, i, pa, pb, cfg_a, cfg_b, a_color, abort_event)
            except Exception as e:
                logger.exception("[arena] run=%s round=%s 异常", run_id, i)
                run.error = f"第 {i + 1} 局异常: {type(e).__name__}: {e}"
                db.rollback()

            run.finished_games = i + 1
            run.current_game = i + 1
            db.commit()
            if run.error:
                break

        run.status = "aborted" if abort_event.is_set() else "finished"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as e:
        logger.exception("[arena] run=%s worker 崩溃", run_id)
        try:
            run = db.query(ArenaRun).filter(ArenaRun.id == run_id).first()
            if run:
                run.status = "failed"
                run.error = str(e)
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()
    finally:
        _set_active(None)
        _abort_events.pop(run_id, None)
        db.close()


def _play_one_game(db: Session, run: ArenaRun, index: int, pa: AIPlayer, pb: AIPlayer,
                   cfg_a: dict, cfg_b: dict, a_color: int, abort_event: threading.Event) -> int:
    """打一局，返回 winner（1/2/0）。"""
    seats = []
    seats.append({"player_id": pa.id if a_color == 1 else pb.id, "role": "black"})
    seats.append({"player_id": pb.id if a_color == 1 else pa.id, "role": "white"})
    room = Room(mode="arena", seats=json.dumps(seats), status="playing",
                options=json.dumps({"size": run.size, "forbidden": run.forbidden,
                                    "difficulty": run.difficulty, "first": 1}))
    db.add(room)
    db.commit()
    db.refresh(room)

    game = start_game(db, room.id)
    if not game:
        room.status = "failed"
        db.commit()
        return 0

    name_black = pa.name if a_color == 1 else pb.name
    name_white = pb.name if a_color == 1 else pa.name
    cfg_black = cfg_a if a_color == 1 else cfg_b
    cfg_white = cfg_b if a_color == 1 else cfg_a
    id_black = pa.id if a_color == 1 else pb.id
    id_white = pb.id if a_color == 1 else pa.id

    forbidden = bool(run.forbidden)
    while game.status == "playing":
        if abort_event.is_set():
            break
        history = json.loads(game.history or "[]")
        if len(history) >= run.max_moves:
            game.status = "draw"
            game.ended_reason = "cap"
            game.winner = 0
            db.commit()
            break
        turn = game.turn
        if turn == 1:
            ai_turn(db, game.id, cfg_black, name_black,
                    expected_player_id=id_black, forbidden=forbidden,
                    difficulty=run.difficulty)
        else:
            ai_turn(db, game.id, cfg_white, name_white,
                    expected_player_id=id_white, forbidden=forbidden,
                    difficulty=run.difficulty)
        db.refresh(game)

    winner = game.winner or 0
    run.current_moves = len(json.loads(game.history or "[]"))

    if a_color == 1:
        result = "a" if winner == 1 else ("b" if winner == 2 else "draw")
    else:
        result = "b" if winner == 1 else ("a" if winner == 2 else "draw")

    if result == "a":
        run.wins_a += 1
    elif result == "b":
        run.wins_b += 1
    else:
        run.draws += 1

    record = ArenaGame(
        run_id=run.id,
        game_id=game.id,
        round_index=index + 1,
        a_color=a_color,
        winner=winner,
        result=result,
        moves=run.current_moves,
    )
    db.add(record)
    room.status = game.status
    db.commit()
    return winner


def delete_runs_for_player(db: Session, player_id: int) -> tuple[int, int]:
    """删除某位 AI 玩家参与的全部擂台赛（删除玩家时的级联清理）。

    返回 (success_count, failure_count)。
    """
    runs = db.query(ArenaRun).filter(
        (ArenaRun.player_a_id == player_id) | (ArenaRun.player_b_id == player_id)
    ).all()
    success_count = 0
    failure_count = 0
    for run in runs:
        if delete_run(db, run.id, drop_games=True):
            success_count += 1
        else:
            failure_count += 1
            logger.warning("[arena] 删除玩家 %s 的擂台赛 %s 失败", player_id, run.id)
    return success_count, failure_count


def delete_run(db: Session, run_id: int, drop_games: bool = True) -> bool:
    """删除一次擂台赛记录（含其单局记录；可选一并删除底层对局与房间）。"""
    run = db.query(ArenaRun).filter(ArenaRun.id == run_id).first()
    if not run:
        return False
    if run.status == "running":
        return False  # 运行中的擂台赛需先中止
    records = db.query(ArenaGame).filter(ArenaGame.run_id == run_id).all()
    game_ids = [r.game_id for r in records if r.game_id]
    db.query(ArenaGame).filter(ArenaGame.run_id == run_id).delete(synchronize_session=False)
    if drop_games and game_ids:
        from ..services.room_service import delete_room
        room_ids = [g[0] for g in db.query(Game.room_id).filter(Game.id.in_(game_ids)).all()]
        db.query(Game).filter(Game.id.in_(game_ids)).delete(synchronize_session=False)
        for rid in set(room_ids):
            try:
                delete_room(db, rid)
            except Exception as e:
                logger.warning("[arena] 清理房间 %s 失败: %s", rid, e)
                db.rollback()
    db.delete(run)
    db.commit()
    return True


def _run_payload(db: Session, run: ArenaRun, with_games: bool = False) -> dict:
    pa = db.query(AIPlayer).filter(AIPlayer.id == run.player_a_id).first()
    pb = db.query(AIPlayer).filter(AIPlayer.id == run.player_b_id).first()
    payload = {
        "id": run.id,
        "player_a": {"id": pa.id, "name": pa.name, "model": pa.model_id} if pa else {"id": run.player_a_id, "name": "?", "model": ""},
        "player_b": {"id": pb.id, "name": pb.name, "model": pb.model_id} if pb else {"id": run.player_b_id, "name": "?", "model": ""},
        "rounds": run.rounds,
        "size": run.size,
        "forbidden": run.forbidden,
        "difficulty": run.difficulty,
        "max_moves": run.max_moves,
        "status": run.status,
        "finished_games": run.finished_games,
        "current_game": run.current_game,
        "current_moves": run.current_moves,
        "wins_a": run.wins_a,
        "wins_b": run.wins_b,
        "draws": run.draws,
        "error": run.error or "",
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    if with_games:
        games = db.query(ArenaGame).filter(ArenaGame.run_id == run.id).order_by(ArenaGame.round_index).all()
        payload["games"] = [
            {
                "round_index": g.round_index,
                "game_id": g.game_id,
                "a_color": g.a_color,
                "winner": g.winner,
                "result": g.result,
                "moves": g.moves,
            }
            for g in games
        ]
    return payload


def list_runs(db: Session, limit: int = 20, with_games: bool = False) -> dict:
    runs = db.query(ArenaRun).order_by(ArenaRun.id.desc()).limit(max(1, min(50, limit))).all()
    return {
        "running_id": current_run_id(),
        "runs": [_run_payload(db, r, with_games=with_games) for r in runs],
    }


def get_run(db: Session, run_id: int) -> dict:
    run = db.query(ArenaRun).filter(ArenaRun.id == run_id).first()
    if not run:
        return None
    return _run_payload(db, run, with_games=True)


def leaderboard(db: Session) -> dict:
    """跨所有擂台赛聚合出模型棋力榜。"""
    # 只统计仍然存在的对局：房间被删除后 Game 行会一起删掉，
    # 历史遗留的悬空 ArenaGame 会污染排行榜，这里在聚合时一并剔除。
    live_ids = {g[0] for g in db.query(Game.id).all()}
    all_games = db.query(ArenaGame).all()
    games = [g for g in all_games if g.game_id in live_ids]
    runs = db.query(ArenaRun).all()
    players = {p.id: p for p in db.query(AIPlayer).all()}

    board = {}
    head2head = {}

    def touch(pid):
        p = players.get(pid)
        if pid not in board:
            board[pid] = {
                "player_id": pid,
                "name": p.name if p else f"#{pid}(已删除)",
                "model": p.model_id if p else "",
                "games": 0, "wins": 0, "losses": 0, "draws": 0, "points": 0,
                "opponents": {},
            }
        return board[pid]

    for g in games:
        run = next((r for r in runs if r.id == g.run_id), None)
        if not run:
            continue
        a, b = run.player_a_id, run.player_b_id
        ra, rb = touch(a), touch(b)
        ra["games"] += 1
        rb["games"] += 1
        if g.result == "a":
            ra["wins"] += 1
            rb["losses"] += 1
            ra["points"] += 3
        elif g.result == "b":
            rb["wins"] += 1
            ra["losses"] += 1
            rb["points"] += 3
        else:
            ra["draws"] += 1
            rb["draws"] += 1
            ra["points"] += 1
            rb["points"] += 1
        # 交手记录（按无序对归并）
        key = tuple(sorted((a, b)))
        slot = head2head.setdefault(key, {"a": a, "b": b, "a_wins": 0, "b_wins": 0, "draws": 0})
        if g.result == "draw":
            slot["draws"] += 1
        elif (g.result == "a" and key[0] == a) or (g.result == "b" and key[0] == b):
            slot["a_wins"] += 1
        else:
            slot["b_wins"] += 1
        ra["opponents"][str(b)] = ra["opponents"].get(str(b), 0) + (1 if g.result == "a" else 0)
        rb["opponents"][str(a)] = rb["opponents"].get(str(a), 0) + (1 if g.result == "b" else 0)

    rows = []
    for rec in board.values():
        rec["win_rate"] = round(rec["wins"] / rec["games"] * 100, 1) if rec["games"] else 0.0
        rows.append(rec)
    rows.sort(key=lambda r: (-r["points"], -r["wins"], r["games"], r["name"]))

    h2h = []
    for (x, y), slot in head2head.items():
        px, py = players.get(x), players.get(y)
        h2h.append({
            "a": {"id": x, "name": px.name if px else f"#{x}"},
            "b": {"id": y, "name": py.name if py else f"#{y}"},
            "a_wins": slot["a_wins"],
            "b_wins": slot["b_wins"],
            "draws": slot["draws"],
        })
    h2h.sort(key=lambda h: -(h["a_wins"] + h["b_wins"] + h["draws"]))

    return {
        "ranking": rows,
        "head2head": h2h,
        "total_games": len(games),
        "total_runs": len(runs),
        "running_id": current_run_id(),
    }
