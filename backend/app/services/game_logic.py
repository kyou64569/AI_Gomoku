from typing import List, Tuple

DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]

# 默认棋盘规格（15 路）。棋盘逻辑本身不再硬编码尺寸：
# 所有函数都从传入的 board 推导 size = len(board)，因此 9/13/15 路共用同一套实现。
# 这里保留 N 仅作为 create_board() 的默认值与历史调用兼容。
N = 15

# 允许的可选棋盘规格（前端房间创建下拉用）
BOARD_SIZES = (9, 13, 15)


def board_size(board: List[List[int]]) -> int:
    """从棋盘推导尺寸（行数）。空棋盘视为 0，调用方需自行保证非空。"""
    return len(board) if board else 0


def create_board(size: int = N) -> List[List[int]]:
    return [[0 for _ in range(size)] for _ in range(size)]


def is_valid_move(board: List[List[int]], row: int, col: int) -> bool:
    n = len(board)
    return 0 <= row < n and 0 <= col < n and board[row][col] == 0


def place_stone(board: List[List[int]], row: int, col: int, player: int) -> List[List[int]]:
    new_board = [r[:] for r in board]
    new_board[row][col] = player
    return new_board


def check_win(board: List[List[int]], row: int, col: int, player: int) -> bool:
    """自由规则胜负判定：任意方向连 5 子即胜（含长连）。"""
    n = len(board)
    for dr, dc in DIRECTIONS:
        count = 1
        for d in [1, -1]:
            r, c = row + dr * d, col + dc * d
            while 0 <= r < n and 0 <= c < n and board[r][c] == player:
                count += 1
                r += dr * d
                c += dc * d
        if count >= 5:
            return True
    return False


def get_empty_positions(board: List[List[int]]) -> List[Tuple[int, int]]:
    return [(r, c) for r in range(len(board)) for c in range(len(board)) if board[r][c] == 0]


def get_candidate_positions(board: List[List[int]], radius: int = 2) -> List[Tuple[int, int]]:
    """只返回"已有棋子周围 radius 格内"的空位，用于加速搜索。

    远离战场的空位在评分体系里得分恒为 0（除中心偏置外），跳过它们不影响决策质量，
    却能把 15 路的候选点从 225 降到几十个，让 2 层搜索（困难难度）具备实时性。
    """
    n = len(board)
    has_stone = any(cell != 0 for row in board for cell in row)
    if not has_stone:
        mid = n // 2
        return [(mid, mid)]
    marked = [[False] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            if board[r][c] == 0:
                continue
            for dr in range(-radius, radius + 1):
                rr = r + dr
                if rr < 0 or rr >= n:
                    continue
                for dc in range(-radius, radius + 1):
                    cc = c + dc
                    if 0 <= cc < n:
                        marked[rr][cc] = True
    return [(r, c) for r in range(n) for c in range(n) if board[r][c] == 0 and marked[r][c]]


def count_line(board: List[List[int]], row: int, col: int, player: int, dr: int, dc: int) -> Tuple[int, int]:
    """统计 (row,col) 沿 (dr,dc) 方向的连子信息（假设 (row,col) 已经是 player）。

    返回 (length, open_ends)：
    - length: 以 (row,col) 为中心向两侧延伸的连续同色子数（含自身）
    - open_ends: 两端开放数（0/1/2），-1 越界或异色视为封闭
    """
    n = len(board)
    length = 1
    open_ends = 0
    for d in [1, -1]:
        r, c = row + dr * d, col + dc * d
        while 0 <= r < n and 0 <= c < n and board[r][c] == player:
            length += 1
            r += dr * d
            c += dc * d
        if 0 <= r < n and 0 <= c < n and board[r][c] == 0:
            open_ends += 1
    return length, open_ends


def is_forbidden_move(board: List[List[int]], row: int, col: int, player: int) -> bool:
    """专业五子棋禁手判定（仅对黑方 player=1 生效，白方无禁手）。

    返回 True 表示 (row,col) 是禁手点：
    - 长连禁手：落子后形成 6 连及以上
    - 双三禁手：落子后同时形成两个或以上活三
    - 双四禁手：落子后同时形成两个或以上冲四/活四
    注意：直接连五（5 连）不是禁手，是获胜。

    已知限制（设计取舍，PRD 中禁手为可选功能）：
    - 活三/冲四按"连续段 + 两端开放数"判定，跳型棋形（如 X_XX / XX_X
      中补位形成的跳三、跳四）未纳入识别；该简化在常规棋局下足够，
      完整标准规则需引入 VCF/VCT 检测，权衡复杂度后未实现。
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


def find_win_line(board: List[List[int]], player: int) -> List[Tuple[int, int]]:
    """返回 player 的获胜五连坐标（用于前端高亮 / 复盘展示），无则返回 None。"""
    n = len(board)
    for r in range(n):
        for c in range(n):
            if board[r][c] != player:
                continue
            for dr, dc in DIRECTIONS:
                cells = [(r, c)]
                for k in (1, 2, 3, 4):
                    nr, nc = r + dr * k, c + dc * k
                    if 0 <= nr < n and 0 <= nc < n and board[nr][nc] == player:
                        cells.append((nr, nc))
                    else:
                        break
                if len(cells) == 5:
                    return cells
    return None
