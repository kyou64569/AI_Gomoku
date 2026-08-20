import json

from app.services.room_service import (
    create_room,
    start_game,
    handle_move,
    get_room,
)

SEATS = [
    {"player_id": None, "role": "black"},
    {"player_id": None, "role": "white"},
]


def _new_game(db):
    room = create_room(db, "pve", SEATS)
    game = start_game(db, room.id)
    return room, game


def test_handle_move_win_syncs_room_status(db_session):
    """对局分出胜负后，Room.status 必须同步为 finished，否则「再来一局」会复用旧对局。"""
    room, game = _new_game(db_session)
    board = [[0] * 15 for _ in range(15)]
    for c in range(4):
        board[0][c] = 1
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    result = handle_move(db_session, game.id, 1, 0, 4, "B", expected_player_id=None)

    assert result is not None
    assert result.status == "finished"
    assert result.winner == 1
    assert get_room(db_session, room.id).status == "finished"


def test_handle_move_draw_syncs_room_status(db_session):
    """棋盘填满且未成五连时判和，Room.status 同步为 draw。"""
    room, game = _new_game(db_session)
    board = [[1] * 15 for _ in range(15)]
    board[14][14] = 0  # 唯一空格，由白方落子（不会形成五连）
    game.board = json.dumps(board)
    game.turn = 2
    db_session.commit()

    result = handle_move(db_session, game.id, 2, 14, 14, "W", expected_player_id=None)

    assert result is not None
    # 明确验证：落子后确实没有形成五连（否则应判胜而非和棋）
    from app.services.game_logic import check_win
    final_board = json.loads(result.board)
    assert check_win(final_board, 14, 14, 2) is False, "白方在(14,14)不应形成五连"
    assert result.status == "draw"
    assert get_room(db_session, room.id).status == "draw"


def test_handle_move_illegal_occupied(db_session):
    room, game = _new_game(db_session)
    board = [[0] * 15 for _ in range(15)]
    board[7][7] = 1
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    result = handle_move(db_session, game.id, 1, 7, 7, "B", expected_player_id=None)

    assert result is None  # 已占用，非法落子被拒绝


def test_handle_move_turn_mismatch(db_session):
    room, game = _new_game(db_session)
    board = [[0] * 15 for _ in range(15)]
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    # 当前轮到黑方(1)，白方(2)抢落子应被拒绝
    result = handle_move(db_session, game.id, 2, 3, 3, "W", expected_player_id=None)

    assert result is None


def test_handle_move_forbidden_rejected(db_session):
    """黑方长连禁手（落子形成 6 连）必须被拒绝。"""
    room, game = _new_game(db_session)
    board = [[0] * 15 for _ in range(15)]
    for c in (0, 1, 2, 3, 5):
        board[0][c] = 1  # 在 (0,4) 落子将形成 6 连
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    result = handle_move(db_session, game.id, 1, 0, 4, "B", expected_player_id=None, forbidden=True)

    assert result is None


def test_handle_move_identity_check(db_session):
    """座位声明黑方为人类(player_id=None)，却以 AI player_id 落子应被拒绝。"""
    room = create_room(db_session, "pve", SEATS)
    game = start_game(db_session, room.id)
    board = [[0] * 15 for _ in range(15)]
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    result = handle_move(db_session, game.id, 1, 0, 0, "B", expected_player_id=999, forbidden=False)

    assert result is None


def test_handle_move_identity_ok_human(db_session):
    """expected_player_id='human' 且座位是人类 → 应放行。"""
    room = create_room(db_session, "pve", SEATS)  # SEATS: 黑=人类(None)
    game = start_game(db_session, room.id)
    board = [[0] * 15 for _ in range(15)]
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    result = handle_move(db_session, game.id, 1, 0, 0, "B", expected_player_id="human", forbidden=False)

    assert result is not None
    assert result.status == "playing"


def test_handle_move_identity_ai_matched(db_session):
    """座位声明黑方为 AI(player_id=42)，用正确的 player_id 落子 → 应放行。"""
    seats = [
        {"player_id": 42, "role": "black"},  # AI 黑方
        {"player_id": None, "role": "white"},  # 人类白方
    ]
    room = create_room(db_session, "pve", seats)
    game = start_game(db_session, room.id)
    board = [[0] * 15 for _ in range(15)]
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    result = handle_move(db_session, game.id, 1, 0, 0, "AI-42", expected_player_id=42, forbidden=False)

    assert result is not None
    assert result.status == "playing"


def test_handle_move_identity_none_skips(db_session):
    """expected_player_id=None → 跳过身份校验，直接放行。"""
    room = create_room(db_session, "pve", SEATS)
    game = start_game(db_session, room.id)
    board = [[0] * 15 for _ in range(15)]
    game.board = json.dumps(board)
    game.turn = 1
    db_session.commit()

    # 座位是"人类"，但 expected_player_id=None 时不校验身份 → 放行
    result = handle_move(db_session, game.id, 1, 0, 0, "B", expected_player_id=None, forbidden=False)

    assert result is not None
