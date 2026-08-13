from app.services.llm_service import build_prompt
from app.services.game_logic import create_board, place_stone


def test_build_prompt():
    board = create_board()
    board = place_stone(board, 7, 7, 1)
    history = [{"player": 1, "player_name": "Player", "row": 7, "col": 7}]
    prompt = build_prompt(board, 2, history, "AI")
    assert "AI" in prompt
    assert "五子棋" in prompt
    assert "O" in prompt or "X" in prompt
