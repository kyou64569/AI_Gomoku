"""新增功能回归测试：PVP / 悔棋 / 认输 / 求和 / 棋盘规格 / 难度 / 复盘分析 / 擂台赛。"""
import json
from unittest.mock import patch

import pytest

import app.services.llm_service as llm_service_module
from app.models import Room, Game
from app.services import game_logic
from app.services.room_service import (
    create_room, start_game, handle_move, get_game, get_room,
    undo_moves, resign_game, offer_draw, respond_draw, normalize_options, resolve_swap,
)
from app.services.analysis_service import analyze_game

HUMAN_SEATS = [
    {"player_id": None, "role": "black"},
    {"player_id": None, "role": "white"},
]


# ============ 规则选项 ============
def test_normalize_options_clamps_invalid():
    assert normalize_options({"size": 7, "difficulty": "god", "first": 9}) == {
        "size": 15, "forbidden": 0, "difficulty": "normal", "first": 1, "swap": 0,
    }
    assert normalize_options({"size": 9, "forbidden": True, "difficulty": "hard",
                              "first": 2, "swap": 1}) == {
        "size": 9, "forbidden": 1, "difficulty": "hard", "first": 2, "swap": 1,
    }
    assert normalize_options({"swap": "true"})["swap"] == 1
    assert normalize_options(None)["size"] == 15
    assert normalize_options("not json")["size"] == 15


def test_start_game_applies_size_and_first(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS,
                       options={"size": 9, "forbidden": 1, "difficulty": "hard", "first": 2})
    game = start_game(db_session, room.id)
    board = json.loads(game.board)
    assert len(board) == 9 and all(len(r) == 9 for r in board)
    assert game.size == 9 and game.forbidden == 1 and game.difficulty == "hard"
    assert game.turn == 2  # 白先


def test_small_board_win_and_bounds(db_session):
    """9 路棋盘：连五照常判胜，越界落子被拒。"""
    room = create_room(db_session, "pvp", HUMAN_SEATS, options={"size": 9})
    game = start_game(db_session, room.id)
    board = game_logic.create_board(9)
    for c in range(4):
        board[0][c] = 1
    game.board = json.dumps(board)
    db_session.commit()

    assert handle_move(db_session, game.id, 1, 0, 20, "B") is None  # 越界
    result = handle_move(db_session, game.id, 1, 0, 4, "B")
    assert result is not None and result.status == "finished" and result.winner == 1


# ============ PVP ============
def test_pvp_room_accepts_two_humans(client, db_session):
    resp = client.post("/api/rooms/", json={"mode": "pvp", "seats": HUMAN_SEATS})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "pvp"


def test_pvp_room_rejects_ai_seat(client):
    seats = [
        {"player_id": None, "role": "black"},
        {"player_id": 42, "role": "white"},
    ]
    resp = client.post("/api/rooms/", json={"mode": "pvp", "seats": seats})
    assert resp.status_code == 400


def test_pvp_both_humans_can_move(client):
    """真人对战：黑白双方都由人类交替落子，无需 AI 介入。"""
    r = client.post("/api/rooms/", json={"mode": "pvp", "seats": HUMAN_SEATS}).json()
    started = client.post(f"/api/rooms/{r['id']}/start").json()
    gid = started["game_id"]

    assert client.post(f"/api/games/{gid}/move", json={"row": 7, "col": 7}).status_code == 200
    assert client.post(f"/api/games/{gid}/move", json={"row": 7, "col": 7}).status_code == 400  # 重复位置
    assert client.post(f"/api/games/{gid}/move", json={"row": 7, "col": 8}).status_code == 200

    state = client.get(f"/api/games/{gid}/state").json()
    assert state["history"][0]["player"] == 1
    assert state["history"][1]["player"] == 2
    assert state["mode"] == "pvp"


# ============ 悔棋 / 认输 / 求和 ============
def test_undo_rebuilds_board_and_turn(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    handle_move(db_session, game.id, 1, 7, 7, "B")
    handle_move(db_session, game.id, 2, 7, 8, "W")

    undone = undo_moves(db_session, game.id, 2)
    board = json.loads(undone.board)
    assert board[7][7] == 0 and board[7][8] == 0
    assert len(json.loads(undone.history)) == 0
    assert undone.turn == room_first(room) and undone.status == "playing"
    assert "悔棋" in json.loads(undone.logs)[-1]


def room_first(room):
    try:
        return normalize_options(room.options)["first"]
    except Exception:
        return 1


def test_undo_one_restores_previous_turn(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    handle_move(db_session, game.id, 1, 7, 7, "B")
    handle_move(db_session, game.id, 2, 7, 8, "W")

    undone = undo_moves(db_session, game.id, 1)
    board = json.loads(undone.board)
    assert board[7][7] == 1 and board[7][8] == 0
    assert undone.turn == 2  # 轮到白方重下


def test_undo_after_finish_returns_to_playing(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    board = game_logic.create_board(15)
    for c in range(4):
        board[0][c] = 1
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()
    handle_move(db_session, game.id, 1, 0, 4, "B")
    assert get_game(db_session, game.id).status == "finished"

    undone = undo_moves(db_session, game.id, 1)
    assert undone.status == "playing" and undone.winner == 0
    assert get_room(db_session, room.id).status == "playing"


def test_resign_sets_opponent_winner(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    handle_move(db_session, game.id, 1, 7, 7, "B")
    done = resign_game(db_session, game.id, 1)
    assert done.status == "finished"
    assert done.winner == 2
    assert done.ended_reason == "resign"
    assert get_room(db_session, room.id).status == "finished"


def test_draw_flow_offer_accept(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    updated, _ = offer_draw(db_session, game.id, 1)
    assert updated.pending_draw == 1
    # 提出方不能自己接受
    assert respond_draw(db_session, game.id, 1, True) is None
    done = respond_draw(db_session, game.id, 2, True)
    assert done.status == "draw" and done.ended_reason == "agreement"
    assert done.pending_draw == 0


def test_draw_flow_decline_continues(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    offer_draw(db_session, game.id, 2)
    cont = respond_draw(db_session, game.id, 1, False)
    assert cont.status == "playing" and cont.pending_draw == 0


def test_undo_resign_draw_endpoints(client):
    r = client.post("/api/rooms/", json={"mode": "pvp", "seats": HUMAN_SEATS}).json()
    gid = client.post(f"/api/rooms/{r['id']}/start").json()["game_id"]

    assert client.post(f"/api/games/{gid}/move", json={"row": 7, "col": 7}).status_code == 200
    assert client.post(f"/api/games/{gid}/undo", json={"count": 1}).status_code == 200

    offer = client.post(f"/api/games/{gid}/draw/offer", json={"player": 1}).json()
    assert offer["resolved"] is False
    resp = client.post(f"/api/games/{gid}/draw/respond", json={"player": 2, "accept": False}).json()
    assert resp["game"]["status"] == "playing"

    resign = client.post(f"/api/games/{gid}/resign", json={"player": 1}).json()
    assert resign["game"]["winner"] == 2
    assert resign["game"]["ended_reason"] == "resign"


# ============ 难度档位 ============
def test_ai_turn_logs_deliberation(db_session):
    """思考日志增强（3.2）：AI 落子后应留下"在哪些候选点之间权衡"的解说。"""
    from app.services.room_service import ai_turn, _deliberation_log
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    # 摆一个有多个像样候选的局面：黑活三 + 白一子
    board = game_logic.create_board(15)
    board[7][5] = 1
    board[7][6] = 1
    board[7][7] = 1
    board[3][3] = 2
    game.board = json.dumps(board)
    game.turn = 2
    db_session.commit()

    # 用一个会失败的 LLM 配置，确保走引擎分支也能产出权衡日志
    with patch.object(llm_service_module, "call_llm", return_value=(None, None, "mock: 不可用")):
        ai_turn(db_session, game.id, {"base_url": "http://127.0.0.1:9", "api_key": "x",
                                      "model_id": "m"}, "AI", forbidden=False,
                difficulty="normal")

    logs = json.loads(get_game(db_session, game.id).logs)
    joined = "\n".join(logs)
    assert any("权衡" in line for line in logs), joined
    assert "AI" in joined

    # 困难档是纯引擎，不再重复附加候选列表
    assert _deliberation_log(game_logic.create_board(15), 1, (7, 7)) == []


# ============ 三手交换 ============
def test_swap_pending_after_third_move(db_session):
    """开启三手交换后，黑走完第 3 手应挂起等待白方裁决。"""
    room = create_room(db_session, "pvp", HUMAN_SEATS, options={"swap": 1})
    game = start_game(db_session, room.id)
    for r, c in ((7, 7), (7, 8), (6, 6)):
        handle_move(db_session, game.id, game.turn if r != 6 else 1, r, c, "X")
    g = get_game(db_session, game.id)
    assert len(json.loads(g.history)) == 3
    assert g.pending_swap == 1
    assert g.turn == 2  # 轮到白方，但它得先决定换不换


def test_swap_flips_board_and_seats(db_session):
    """接受交换后：棋子颜色与棋谱归属对调，两个座位的 player_id 互换。"""
    seats = [
        {"player_id": 11, "role": "black"},   # AI 黑
        {"player_id": None, "role": "white"},  # 人类白
    ]
    room = create_room(db_session, "pve", seats, options={"swap": 1})
    game = start_game(db_session, room.id)
    handle_move(db_session, game.id, 1, 7, 7, "AI", expected_player_id=11)
    handle_move(db_session, game.id, 2, 7, 8, "Player", expected_player_id="human")
    handle_move(db_session, game.id, 1, 6, 6, "AI", expected_player_id=11)
    assert get_game(db_session, game.id).pending_swap == 1

    swapped = resolve_swap(db_session, game.id, True)
    board = json.loads(swapped.board)
    # 原黑子 (7,7)(6,6) 变白，原白子 (7,8) 变黑
    assert board[7][7] == 2 and board[6][6] == 2 and board[7][8] == 1
    history = json.loads(swapped.history)
    assert [m["player"] for m in history] == [2, 1, 2]
    assert swapped.pending_swap == 0

    # 座位对调：人类现在执黑，AI 执白
    new_seats = json.loads(get_room(db_session, room.id).seats)
    black = next(s for s in new_seats if s["role"] == "black")
    white = next(s for s in new_seats if s["role"] == "white")
    assert black["player_id"] is None
    assert white["player_id"] == 11

    # 交换后轮到"白方座位"（现在是 AI 11）落子，身份校验应放行
    assert handle_move(db_session, game.id, 2, 5, 5, "AI",
                       expected_player_id=11, forbidden=False) is not None


def test_swap_decline_keeps_colors(db_session):
    room = create_room(db_session, "pvp", HUMAN_SEATS, options={"swap": 1})
    game = start_game(db_session, room.id)
    for p, r, c in ((1, 7, 7), (2, 7, 8), (1, 6, 6)):
        handle_move(db_session, game.id, p, r, c, "X")
    kept = resolve_swap(db_session, game.id, False)
    board = json.loads(kept.board)
    assert board[7][7] == 1 and board[7][8] == 2
    assert kept.pending_swap == 0
    assert resolve_swap(db_session, game.id, True) is None  # 已裁决，不能重复


def test_swap_endpoint_and_move_lock(client):
    """待裁决时禁止继续落子，裁决后恢复。"""
    r = client.post("/api/rooms/", json={"mode": "pvp", "seats": HUMAN_SEATS,
                                         "options": {"swap": 1}}).json()
    gid = client.post(f"/api/rooms/{r['id']}/start").json()["game_id"]
    for row, col in ((7, 7), (7, 8), (6, 6)):
        assert client.post(f"/api/games/{gid}/move", json={"row": row, "col": col}).status_code == 200

    state_ = client.get(f"/api/games/{gid}/state").json()
    assert state_["pending_swap"] == 1
    blocked = client.post(f"/api/games/{gid}/move", json={"row": 0, "col": 0})
    assert blocked.status_code == 400 and "三手交换" in blocked.json()["detail"]

    res = client.post(f"/api/games/{gid}/swap", json={"accept": True}).json()
    assert res["swapped"] is True and res["game"]["pending_swap"] == 0
    assert res["game"]["board"][7][7] == 2
    assert client.post(f"/api/games/{gid}/move", json={"row": 0, "col": 0}).status_code == 200


def test_watch_mode_swap_rejected(client, db_session):
    from app.models import AIPlayer, ModelConfig
    cfg = ModelConfig(name="c", base_url="https://x/v1", api_key="", models="[]")
    db_session.add(cfg)
    db_session.commit()
    p1 = AIPlayer(name="a", model_config_id=cfg.id, model_id="m")
    p2 = AIPlayer(name="b", model_config_id=cfg.id, model_id="m")
    db_session.add_all([p1, p2])
    db_session.commit()
    seats = [{"player_id": p1.id, "role": "black"}, {"player_id": p2.id, "role": "white"}]
    r = client.post("/api/rooms/", json={"mode": "watch", "seats": seats,
                                         "options": {"swap": 1}}).json()
    gid = client.post(f"/api/rooms/{r['id']}/start").json()["game_id"]
    assert client.post(f"/api/games/{gid}/swap", json={"accept": True}).status_code == 400


# ============ 房间选项在 start 时的传递（回归：9路/简单被冲成 15路/普通） ============
NINE_EASY = {"size": 9, "forbidden": 0, "difficulty": "easy", "first": 1, "swap": 0}


def _room_with(client, options):
    resp = client.post("/api/rooms/", json={
        "mode": "pvp", "seats": HUMAN_SEATS, "options": options,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _game_of(client, room_id):
    gid = client.post(f"/api/rooms/{room_id}/start").json()["game_id"]
    return client.get(f"/api/games/{gid}/state").json()


def test_start_accepts_wrapped_options(client):
    """前端发的形状是 {"options": {...}}。FastAPI 对唯一 Body 参数默认 embed=False，
    这个包裹形状必须被正确解开并生效，否则用户选的 9 路/简单 会被覆盖成 15 路/普通。"""
    room = _room_with(client, NINE_EASY)
    assert room["options"]["size"] == 9

    gid = client.post(f"/api/rooms/{room['id']}/start", json={"options": NINE_EASY}).json()["game_id"]
    st = client.get(f"/api/games/{gid}/state").json()
    assert st["size"] == 9, f"期望 9 路，实际 {st['size']}"
    assert len(st["board"]) == 9 and len(st["board"][0]) == 9
    assert st["difficulty"] == "easy"


def test_start_accepts_bare_options(client):
    """OpenAPI 声明的裸 dict 形状 {"size":9,...} 同样要生效（兼容直接调 API 的调用方）。"""
    room = _room_with(client, NINE_EASY)
    gid = client.post(f"/api/rooms/{room['id']}/start", json=dict(NINE_EASY)).json()["game_id"]
    st = client.get(f"/api/games/{gid}/state").json()
    assert st["size"] == 9 and st["difficulty"] == "easy"


def test_start_with_unknown_payload_never_wipes_room_options(client):
    """形状不对的 payload 绝不能把房间已保存的选项冲成默认值（旧实现的破坏性写回）。"""
    room = _room_with(client, NINE_EASY)
    for bad in ({}, {"foo": 1}, {"options": {}}, {"options": None}):
        gid = client.post(f"/api/rooms/{room['id']}/start", json=bad).json()["game_id"]
        st = client.get(f"/api/games/{gid}/state").json()
        assert st["size"] == 9, f"payload={bad} 把 size 冲成了 {st['size']}"
        assert st["difficulty"] == "easy", f"payload={bad} 把难度冲成了 {st['difficulty']}"
        # 结束这局，下一轮才能再开新局（幂等分支会复用进行中的对局）
        client.post(f"/api/games/{gid}/resign", json={"player": 1})


def test_create_then_start_keeps_user_choices(client):
    """完整回归用户场景：首页选 9 路 / 自由 / 简单 -> 创建 -> 开局 -> 对局确实是 9 路简单。"""
    room = _room_with(client, NINE_EASY)
    st = _game_of(client, room["id"])
    assert (st["size"], st["forbidden"], st["difficulty"]) == (9, 0, "easy")
    assert len(st["board"]) == 9


def test_start_merges_partial_options(client):
    """只传部分键时，未提供的项沿用房间已保存的值，而不是回落默认值。"""
    room = _room_with(client, NINE_EASY)
    gid = client.post(f"/api/rooms/{room['id']}/start", json={"options": {"size": 13}}).json()["game_id"]
    st = client.get(f"/api/games/{gid}/state").json()
    assert st["size"] == 13            # 显式覆盖
    assert st["difficulty"] == "easy"  # 未提供 -> 保留房间的 easy，而不是变回 normal


def test_restart_without_body_keeps_room_rules(client):
    """「再来一局」不带 body：直接沿用房间已固化的规则。"""
    room = _room_with(client, NINE_EASY)
    st1 = _game_of(client, room["id"])
    client.post(f"/api/games/{st1['id']}/resign", json={"player": 1})
    gid2 = client.post(f"/api/rooms/{room['id']}/start", json={}).json()["game_id"]
    st2 = client.get(f"/api/games/{gid2}/state").json()
    assert st2["size"] == 9 and st2["difficulty"] == "easy"


def test_nine_board_rejects_out_of_range(client):
    """9 路棋盘上，坐标 9+ 必须被拒绝（棋盘真的变小了，不是只有显示变小）。"""
    room = _room_with(client, NINE_EASY)
    gid = client.post(f"/api/rooms/{room['id']}/start").json()["game_id"]
    assert client.post(f"/api/games/{gid}/move", json={"row": 9, "col": 0}).status_code == 400
    assert client.post(f"/api/games/{gid}/move", json={"row": 8, "col": 8}).status_code == 200


def test_hard_difficulty_never_calls_llm(db_session):
    """困难 = 纯引擎深算，不应发起任何 LLM 请求。"""
    from app.services.llm_service import ai_move
    board = game_logic.create_board(15)
    board[7][7] = 1
    with patch.object(llm_service_module, "call_llm", side_effect=AssertionError("困难档不应调用 LLM")):
        row, col, reason = ai_move(board, 2, [], {"base_url": "http://x", "api_key": "k",
                                                   "model_id": "m"}, "AI", difficulty="hard")
    assert game_logic.is_valid_move(board, row, col)
    assert reason.startswith("引擎(深算)")


def test_easy_difficulty_follows_llm():
    """简单 = 听 LLM 的（合法即采纳，不做评分门槛）。"""
    from app.services.llm_service import ai_move
    board = game_logic.create_board(15)
    board[7][7] = 1
    with patch.object(llm_service_module, "call_llm", return_value=(0, 0, "角落好玩")):
        row, col, reason = ai_move(board, 2, [], {"base_url": "http://x", "api_key": "k",
                                                   "model_id": "m"}, "AI", difficulty="easy")
    # 引擎绝不会推荐 (0,0)，能选中说明采纳了 LLM 的建议
    assert (row, col) == (0, 0)


def test_hard_engine_avoids_gifting_opponent_win():
    """困难档 2 层搜索：对手下一手能连五的点必须避开。"""
    from app.services.llm_service import engine_best_move
    board = game_logic.create_board(15)
    # 白方 (7,1)(7,2)(7,3)(7,4) 四连，两端 (7,0) 与 (7,5) 都可连五 -> 黑必须堵
    for c in range(1, 5):
        board[7][c] = 2
    row, col, *_ = engine_best_move(board, 1, forbidden=False, depth=2)
    assert (row, col) in [(7, 0), (7, 5)]


# ============ 复盘分析 ============
def test_analysis_grades_moves(db_session):
    """白方放着黑方活三不堵、连下三手角落 → 引擎必须判为失误/败着。"""
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    # 黑方做活三 (7,3)(7,4)(7,5)，白方全程在角落自顾自地下
    for black_col, white_col in ((3, 0), (4, 1), (5, 2)):
        handle_move(db_session, game.id, 1, 7, black_col, "黑")
        handle_move(db_session, game.id, 2, 0, white_col, "白")

    result = analyze_game(db_session, game.id)
    assert result["summary"]["total_moves"] == 6
    assert len(result["moves"]) == 6

    # 最后一手：黑已活三，白还下 (0,2) → 应被判为失误或败着
    last_white = [m for m in result["moves"] if m["player"] == 2][-1]
    assert last_white["label"] in ("inaccuracy", "mistake", "blunder"), last_white
    assert last_white["best_row"] is not None

    p1, p2 = result["players"]["1"], result["players"]["2"]
    assert p2["rating"] < p1["rating"]
    assert p2["accuracy"] < p1["accuracy"]


def test_analysis_marks_best_move(db_session):
    """走出引擎最优手 → 必须判为 best（loss = 0）。"""
    from app.services.llm_service import engine_best_move
    room = create_room(db_session, "pvp", HUMAN_SEATS)
    game = start_game(db_session, room.id)
    handle_move(db_session, game.id, 1, 7, 7, "黑")
    board = json.loads(get_game(db_session, game.id).board)
    best = engine_best_move(board, 2, forbidden=False, depth=1)
    handle_move(db_session, game.id, 2, best[0], best[1], "白")

    result = analyze_game(db_session, game.id)
    move = result["moves"][1]  # 白方那一手
    assert move["label"] == "best" and move["loss"] == 0


def test_analysis_endpoint(client):
    r = client.post("/api/rooms/", json={"mode": "pvp", "seats": HUMAN_SEATS}).json()
    gid = client.post(f"/api/rooms/{r['id']}/start").json()["game_id"]
    client.post(f"/api/games/{gid}/move", json={"row": 7, "col": 7})

    resp = client.get(f"/api/games/{gid}/analysis")
    assert resp.status_code == 200
    assert resp.json()["summary"]["total_moves"] == 1


# ============ 擂台赛 ============
def test_arena_requires_two_distinct_players(client):
    cfg, player = _mk_player(client, "Solo")
    resp = client.post("/api/arena/", json={"player_a_id": player["id"], "player_b_id": player["id"]})
    assert resp.status_code == 400


def _mk_player(client, name):
    cfg = client.post("/api/configs/", json={
        "name": f"cfg-{name}", "base_url": "https://api.example.com/v1", "api_key": "",
    }).json()
    player = client.post("/api/players/", json={
        "name": name, "model_config_id": cfg["id"], "model_id": "test-model",
    }).json()
    return cfg, player


def test_arena_leaderboard_empty_then_populated(client):
    lb = client.get("/api/arena/leaderboard").json()
    assert lb["ranking"] == [] and lb["total_games"] == 0

    _, pa = _mk_player(client, "Alpha")
    _, pb = _mk_player(client, "Beta")
    # 不真正启动对局（避免联网），只校验启动前的校验分支与排行榜接口可用
    assert client.get("/api/arena/").json()["running_id"] is None


def test_arena_rejects_concurrent_run(client):
    """同一时刻只允许一个擂台赛运行。"""
    from app.services import arena_service
    _, pa = _mk_player(client, "A1")
    _, pb = _mk_player(client, "B1")
    arena_service._set_active(999)
    try:
        resp = client.post("/api/arena/", json={"player_a_id": pa["id"], "player_b_id": pb["id"]})
        assert resp.status_code == 400
    finally:
        arena_service._set_active(None)


# ============ 可观测性 ============
def test_stats_endpoint_shape(client):
    data = client.get("/api/stats/").json()
    assert "models" in data and "totals" in data
    assert data["totals"]["calls"] >= 0


def test_metrics_record_and_reset():
    from app.services import metrics
    metrics.reset()
    metrics.record_call("http://x/v1", "m1", "ok", 120)
    metrics.record_call("http://x/v1", "m1", "timeout", 800)
    metrics.record_outcome("http://x/v1", "m1", "fallback")
    snap = metrics.snapshot()
    row = [r for r in snap if r["model_id"] == "m1"][0]
    assert row["calls"] == 2 and row["ok"] == 1 and row["timeout"] == 1 and row["fallback"] == 1
    assert row["success_rate"] == 50.0 and row["avg_latency_ms"] == 460
    metrics.reset()
    assert metrics.snapshot() == []
