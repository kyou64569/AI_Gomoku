from typing import List, Tuple

DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]
N = 15


def create_board() -> List[List[int]]:
    return [[0 for _ in range(N)] for _ in range(N)]


def is_valid_move(board: List[List[int]], row: int, col: int) -> bool:
    return 0 <= row < N and 0 <= col < N and board[row][col] == 0


def place_stone(board: List[List[int]], row: int, col: int, player: int) -> List[List[int]]:
    new_board = [r[:] for r in board]
    new_board[row][col] = player
    return new_board


def check_win(board: List[List[int]], row: int, col: int, player: int) -> bool:
    """自由规则胜负判定：任意方向连 5 子即胜（含长连）。"""
    for dr, dc in DIRECTIONS:
        count = 1
        for d in [1, -1]:
            r, c = row + dr * d, col + dc * d
            while 0 <= r < N and 0 <= c < N and board[r][c] == player:
                count += 1
                r += dr * d
                c += dc * d
        if count >= 5:
            return True
    return False


def get_empty_positions(board: List[List[int]]) -> List[Tuple[int, int]]:
    return [(r, c) for r in range(N) for c in range(N) if board[r][c] == 0]


def count_line(board: List[List[int]], row: int, col: int, player: int, dr: int, dc: int) -> Tuple[int, int]:
    """统计 (row,col) 沿 (dr,dc) 方向的连子信息（假设 (row,col) 已经是 player）。

    返回 (length, open_ends)：
    - length: 以 (row,col) 为中心向两侧延伸的连续同色子数（含自身）
    - open_ends: 两端开放数（0/1/2），-1 越界或异色视为封闭
    """
    length = 1
    open_ends = 0
    for d in [1, -1]:
        r, c = row + dr * d, col + dc * d
        while 0 <= r < N and 0 <= c < N and board[r][c] == player:
            length += 1
            r += dr * d
            c += dc * d
        if 0 <= r < N and 0 <= c < N and board[r][c] == 0:
            open_ends += 1
    return length, open_ends


def is_forbidden_move(board: List[List[int]], row: int, col: int, player: int) -> bool:
    """专业五子棋禁手判定（仅对黑方 player=1 生效，白方无禁手）。

    返回 True 表示 (row,col) 是禁手点：
    - 长连禁手：落子后形成 6 连及以上
    - 双三禁手：落子后同时形成两个或以上活三
    - 双四禁手：落子后同时形成两个或以上冲四/活四
    注意：直接连五（5 连）不是禁手，是获胜。
    """
    if player != 1:
        return False
    if not is_valid_move(board, row, col):
        return False
    sim = place_stone(board, row, col, player)
    threes = 0
    fours = 0
    for dr, dc in DIRECTIONS:
        length, open_ends = count_line(sim, row, col, player, dr, dc)
        if length >= 6:
            return True  # 长连
        if length == 5:
            continue  # 直接连五，允许
        if length == 4 and open_ends >= 1:
            fours += 1
        elif length == 3 and open_ends == 2:
            threes += 1
    if threes >= 2:
        return True
    if fours >= 2:
        return True
    return False
