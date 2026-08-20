import sys
sys.path.insert(0, ".")
from app.services.game_logic import create_board, is_valid_move, place_stone, check_win, get_empty_positions, is_forbidden_move


def test_create_board():
    board = create_board()
    assert len(board) == 15
    assert all(len(row) == 15 for row in board)
    assert all(board[r][c] == 0 for r in range(15) for c in range(15))


def test_place_and_win():
    board = create_board()
    for r in range(5):
        board = place_stone(board, r, 0, 1)
    assert check_win(board, 4, 0, 1) is True


def test_valid_move():
    board = create_board()
    assert is_valid_move(board, 0, 0) is True
    board = place_stone(board, 0, 0, 1)
    assert is_valid_move(board, 0, 0) is False
    assert is_valid_move(board, 0, 1) is True


def test_empty_positions():
    board = create_board()
    assert len(get_empty_positions(board)) == 225
    board = place_stone(board, 0, 0, 1)
    assert len(get_empty_positions(board)) == 224


# ============ 补充覆盖（P2：多方向胜利 / 越界 / 重复落子 / 边界） ============
def test_win_horizontal():
    board = create_board()
    for c in range(5):
        board = place_stone(board, 7, c, 1)
    assert check_win(board, 7, 4, 1) is True


def test_win_vertical():
    board = create_board()
    for r in range(5):
        board = place_stone(board, r, 7, 1)
    assert check_win(board, 4, 7, 1) is True


def test_win_diagonal_down_right():
    board = create_board()
    for i in range(5):
        board = place_stone(board, i, i, 1)
    assert check_win(board, 4, 4, 1) is True


def test_win_diagonal_down_left():
    board = create_board()
    for i in range(5):
        board = place_stone(board, i, 4 - i, 1)
    assert check_win(board, 4, 0, 1) is True


def test_no_win_four_only():
    board = create_board()
    for c in range(4):
        board = place_stone(board, 7, c, 1)
    assert check_win(board, 7, 3, 1) is False  # 4 连不算胜


def test_six_is_win_free_rule():
    # 自由规则下六连也判胜（check_win 只管 >=5）
    board = create_board()
    for c in range(6):
        board = place_stone(board, 7, c, 1)
    assert check_win(board, 7, 5, 1) is True


def test_out_of_bounds_move_rejected():
    board = create_board()
    assert is_valid_move(board, -1, 0) is False
    assert is_valid_move(board, 0, 15) is False
    assert is_valid_move(board, 15, 15) is False


def test_place_occupied_rejected():
    board = create_board()
    board = place_stone(board, 3, 3, 1)
    assert is_valid_move(board, 3, 3) is False


def test_win_at_board_edge():
    # 边界行五连：最后一行的胜利检测
    board = create_board()
    for c in range(5):
        board = place_stone(board, 14, c, 2)
    assert check_win(board, 14, 4, 2) is True
