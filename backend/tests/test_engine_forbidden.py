import sys
sys.path.insert(0, ".")
from app.services.game_logic import create_board, place_stone, is_forbidden_move, check_win, is_valid_move, count_line
from app.services.llm_service import engine_best_move, ai_move, shape_score, SCORE_WIN, SCORE_LIVE_FOUR, SCORE_DEAD_FOUR, SCORE_LIVE_THREE


# ============ 禁手规则 ============
def test_forbidden_overline():
    # 黑棋 6 连 = 长连禁手
    board = create_board()
    for c in range(5):
        board[7][c] = 1
    assert is_forbidden_move(board, 7, 5, 1) is True


def test_forbidden_five_not_forbidden():
    # 黑棋落子成 5 连 = 允许（获胜）
    board = create_board()
    for c in range(4):
        board[7][c] = 1
    board[7][5] = 2  # 白堵一端
    assert is_forbidden_move(board, 7, 4, 1) is False


def test_forbidden_double_three():
    # 构造横活三 + 竖活三交叉点 (7,5)，黑棋落子成双三 = 禁手
    board = create_board()
    # 横：_1_1_1_ 形态，(7,5) 补位 → _11111_? 不，构造 (7,3),(7,4),(7,6) 活三
    board[7][3] = 1
    board[7][4] = 1
    board[7][6] = 1
    # 竖：(5,5),(6,5),(8,5) 活三
    board[5][5] = 1
    board[6][5] = 1
    board[8][5] = 1
    assert is_forbidden_move(board, 7, 5, 1) is True


def test_forbidden_double_four():
    # 构造横活四 + 竖活四交叉 (7,5)：横 (7,2),(7,3),(7,4) 落 (7,5) 成活四；
    # 竖 (4,5),(5,5),(6,5) 落 (7,5) 也成活四 → 双四禁手
    board = create_board()
    board[7][2] = 1
    board[7][3] = 1
    board[7][4] = 1
    board[4][5] = 1
    board[5][5] = 1
    board[6][5] = 1
    assert is_forbidden_move(board, 7, 5, 1) is True


def test_white_no_forbidden():
    # 白棋不受禁手限制
    board = create_board()
    for c in range(4):
        board[7][c] = 2
    board[7][5] = 1
    assert is_forbidden_move(board, 7, 4, 2) is False


# ============ 评分引擎棋力 ============
def test_engine_takes_win():
    # 己方黑棋已有 4 连，且有一端开放 → 引擎必须选连五点
    board = create_board()
    for c in range(4):
        board[7][c] = 1
    board[7][5] = 2  # 一端白堵，另一端 (7,4) 开放
    row, col, total, attack, defend, reason = engine_best_move(board, 1)
    assert (row, col) == (7, 4), f"应选 (7,4) 完成五连，实际选了 ({row},{col}) reason={reason}"
    assert attack >= SCORE_WIN


def test_engine_blocks_enemy_win():
    # 对方白棋已有 4 连，只差一步 → 引擎必须堵
    board = create_board()
    for c in range(4):
        board[7][c] = 2
    board[7][5] = 1  # 黑堵一端
    row, col, total, attack, defend, reason = engine_best_move(board, 1)
    assert (row, col) == (7, 4), f"应堵 (7,4)，实际 ({row},{col}) reason={reason}"
    assert defend >= SCORE_WIN


def test_engine_makes_live_four():
    # 己方活三，一端补子成活四（两端都空）
    board = create_board()
    board[7][3] = 1
    board[7][4] = 1
    board[7][5] = 1
    # (7,2) 或 (7,6) 补子成 _1111_ 活四
    row, col, total, attack, defend, reason = engine_best_move(board, 1)
    assert (row, col) in [(7, 2), (7, 6)], f"应扩活三成活四，实际 ({row},{col})"
    assert attack >= SCORE_LIVE_FOUR


def test_engine_blocks_enemy_live_three():
    # 对方白棋活三 _222_ 两端，黑棋应堵一端
    board = create_board()
    board[7][3] = 2
    board[7][4] = 2
    board[7][5] = 2
    row, col, total, attack, defend, reason = engine_best_move(board, 1)
    assert (row, col) in [(7, 2), (7, 6)], f"应堵对方活三，实际 ({row},{col})"
    assert defend >= SCORE_LIVE_THREE


def test_ai_move_without_llm():
    # LLM 配置不可用（bad url）时，ai_move 也应通过引擎给出合法落子
    board = create_board()
    board[7][7] = 1  # 黑开局
    model_config = {"base_url": "http://127.0.0.1:9", "api_key": "x", "model_id": "x", "temperature": 0.5}
    row, col, reason = ai_move(board, 2, [], model_config, "AI", forbidden=False)
    assert is_valid_move(board, row, col), f"非法落子 ({row},{col})"
    assert reason.startswith("引擎"), f"应走引擎，实际 reason={reason}"


def test_ai_move_respects_forbidden():
    # 黑棋面对禁手局面时，引擎不应选中禁手点
    board = create_board()
    board[7][3] = 1
    board[7][4] = 1
    board[7][6] = 1
    board[5][5] = 1
    board[6][5] = 1
    board[8][5] = 1
    model_config = {"base_url": "http://127.0.0.1:9", "api_key": "x", "model_id": "x", "temperature": 0.5}
    row, col, reason = ai_move(board, 1, [], model_config, "AI", forbidden=True)
    assert is_valid_move(board, row, col)
    assert not is_forbidden_move(board, row, col, 1), f"选中禁手点 ({row},{col})"
