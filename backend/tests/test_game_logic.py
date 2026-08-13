import sys
sys.path.insert(0, ".")
from app.services.game_logic import create_board, is_valid_move, place_stone, check_win, get_empty_positions


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
