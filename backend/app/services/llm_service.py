import json
import random
import time
import threading
import httpx
from openai import OpenAI
from openai import BadRequestError, UnprocessableEntityError, NotFoundError, APITimeoutError, APIConnectionError
from . import game_logic
from . import metrics
from .game_logic import (DIRECTIONS, N, count_line, place_stone, is_valid_move, is_forbidden_move,
                         get_candidate_positions, find_win_line)

# ============ 确定性评分引擎（不依赖 LLM 也能下好棋） ============
SCORE_WIN = 10_000_000      # 连五
SCORE_LIVE_FOUR = 1_000_000  # 活四（对方无解）
SCORE_DEAD_FOUR = 100_000    # 冲四
SCORE_LIVE_THREE = 50_000    # 活三
SCORE_DEAD_THREE = 5_000     # 眠三
SCORE_LIVE_TWO = 500         # 活二
SCORE_DEAD_TWO = 100         # 眠二

# 攻防权重：进攻略优先（五子棋先手必争，光守不下永远不赢）
DEFEND_WEIGHT = 0.9


def shape_score(length: int, open_ends: int) -> int:
    """把 (连子长度, 两端开放数) 映射为棋型分。"""
    if length >= 5:
        return SCORE_WIN
    if length == 4:
        if open_ends == 2:
            return SCORE_LIVE_FOUR
        if open_ends == 1:
            return SCORE_DEAD_FOUR
        return 0
    if length == 3:
        if open_ends == 2:
            return SCORE_LIVE_THREE
        if open_ends == 1:
            return SCORE_DEAD_THREE
        return 0
    if length == 2:
        if open_ends == 2:
            return SCORE_LIVE_TWO
        if open_ends == 1:
            return SCORE_DEAD_TWO
        return 0
    return 0


def score_point(board, row, col, me, enemy):
    """给空位 (row,col) 打分：攻分（己方落子棋型）+ 防分（封堵对方棋型）。

    返回 (attack, defend)：
    - attack: 我在这落子能形成的威胁总分
    - defend: 对方如果在这落子能形成的威胁总分（= 我堵在这里的价值）
    """
    # 攻击分：模拟己方落子
    sim = place_stone(board, row, col, me)
    attack = 0
    attack_shapes = []
    for dr, dc in DIRECTIONS:
        length, open_ends = count_line(sim, row, col, me, dr, dc)
        s = shape_score(length, open_ends)
        attack += s
        attack_shapes.append(s)
    # 双威胁检测：两个方向同时达到"活三+"（活三/冲四/活四），形成必胜双三/双四/四三
    strong = [s for s in attack_shapes if s >= SCORE_LIVE_THREE]
    if len(strong) >= 2:
        attack += SCORE_LIVE_FOUR  # 双威胁加成：等价于多一个活四
    # 防守分：模拟对方落子
    sim2 = place_stone(board, row, col, enemy)
    defend = 0
    defend_shapes = []
    for dr, dc in DIRECTIONS:
        length, open_ends = count_line(sim2, row, col, enemy, dr, dc)
        s = shape_score(length, open_ends)
        defend += s
        defend_shapes.append(s)
    strong_d = [s for s in defend_shapes if s >= SCORE_LIVE_THREE]
    if len(strong_d) >= 2:
        defend += SCORE_LIVE_FOUR  # 对方双威胁点，堵这里的价值同样加成
    return attack, defend


# 中心偏置上限：仅在没有棋型分时起作用（让开局往中心走），不会盖过活二
CENTER_BIAS_MAX = 60

# 困难难度的 2 层搜索参数：对静态分 top-K 候选，模拟落子后看对手最强应手
HARD_TOP_K = 10
HARD_OPPONENT_WEIGHT = 0.85


def _center_bias(board, r, c) -> int:
    n = len(board)
    center = (n - 1) / 2.0
    return max(0, CENTER_BIAS_MAX - int((abs(r - center) + abs(c - center)) * (8 if n <= 13 else 8)))


def evaluate_point(board, row, col, player):
    """空位 (row,col) 对 player 的综合价值，口径与 _static_candidates 完全一致
    （攻 + 防 × DEFEND_WEIGHT + 中心偏置），供复盘评分对比"这一手 vs 最优手"。

    返回 (total, attack, defend)。
    """
    attack, defend = score_point(board, row, col, player, 3 - player)
    return attack + int(defend * DEFEND_WEIGHT) + _center_bias(board, row, col), attack, defend


def _static_candidates(board, turn, forbidden=False):
    """对所有候选空位做静态评分，返回按 total 降序的列表。

    每项为 (row, col, total, attack, defend)。候选点用 get_candidate_positions
    裁剪（只保留棋子周围 2 格），把 15 路 225 个空位降到几十个，
    使困难难度的 2 层搜索具备实时性。
    """
    me = turn
    enemy = 3 - turn
    candidates = get_candidate_positions(board, radius=2)
    if not candidates:
        candidates = game_logic.get_empty_positions(board)
    scored = []
    for r, c in candidates:
        if board[r][c] != 0:
            continue
        if forbidden and is_forbidden_move(board, r, c, me):
            continue
        attack, defend = score_point(board, r, c, me, enemy)
        total = attack + int(defend * DEFEND_WEIGHT) + _center_bias(board, r, c)
        scored.append((r, c, total, attack, defend))
    scored.sort(key=lambda x: (-x[2], x[0], x[1]))
    return scored


def _reason_for(row, col, attack, defend, total) -> str:
    """把攻防分翻译成人类可读的落子理由（引擎日志 / 前端展示）。"""
    if attack >= SCORE_WIN:
        return f"一步连五 ({row},{col})"
    if defend >= SCORE_WIN:
        return f"堵对方连五 ({row},{col})"
    if attack >= SCORE_LIVE_FOUR:
        return f"形成活四 ({row},{col})"
    if defend >= SCORE_LIVE_FOUR:
        return f"堵对方活四 ({row},{col})"
    if attack >= SCORE_DEAD_FOUR:
        return f"形成冲四 ({row},{col})"
    if defend >= SCORE_DEAD_FOUR:
        return f"堵对方冲四 ({row},{col})"
    if attack >= SCORE_LIVE_THREE:
        return f"形成活三 ({row},{col})"
    if defend >= SCORE_LIVE_THREE:
        return f"堵对方活三 ({row},{col})"
    return f"攻{attack}/防{defend}"


def engine_top_moves(board, turn, k=3, forbidden=False):
    """返回引擎评分最高的 k 个候选，用于「AI 在这几个点之间权衡」的思考日志。

    每项为 {"row","col","total","attack","defend","reason"}，按 total 降序。
    """
    scored = _static_candidates(board, turn, forbidden=forbidden)
    return [
        {
            "row": r, "col": c, "total": t, "attack": a, "defend": d,
            "reason": _reason_for(r, c, a, d, t),
        }
        for r, c, t, a, d in scored[:max(1, k)]
    ]


def engine_best_move(board, turn, forbidden=False, depth=1):
    """纯确定性评分：返回 (row, col, total_score, attack, defend, reason)。

    优先级天然被权重覆盖：
    - 己方能连五 → 攻分最高（SCORE_WIN）
    - 对方能连五 → 该点防分最高（SCORE_WIN × DEFEND_WEIGHT），必然选它堵
    - 己方活四/对方活四 → 次高分
    - 常规局面 → 攻防总和高者胜出

    depth=2（困难难度）在 top-K 静态候选上再做一层对手应手模拟：
    落子后让对手走最优一步，用「我的静态收益 − 0.85 × 对手最强应手」排序，
    能过滤掉"看似得分高、其实送给对手机会"的假好点。
    """
    scored = _static_candidates(board, turn, forbidden=forbidden)
    if not scored:
        return None
    if depth < 2 or len(scored) == 1:
        r, c, t, a, d = scored[0]
        return r, c, t, a, d, _reason_for(r, c, a, d, t)

    me = turn
    enemy = 3 - turn
    best = None
    for r, c, t, a, d in scored[:HARD_TOP_K]:
        if a >= SCORE_WIN:
            # 能一步连五，无需再算
            return r, c, t, a, d, _reason_for(r, c, a, d, t)
        sim = place_stone(board, r, c, me)
        if game_logic.check_win(sim, r, c, me):
            return r, c, SCORE_WIN * 2, SCORE_WIN, d, f"一步连五 ({r},{c})"
        opp = _static_candidates(sim, enemy, forbidden=False)
        opp_best = opp[0][2] if opp else 0
        value = int(t - HARD_OPPONENT_WEIGHT * opp_best)
        # 对手下一步能连五 → 这手是败着，重罚
        if opp and opp[0][3] >= SCORE_WIN and a < SCORE_WIN:
            value -= SCORE_WIN
        if best is None or value > best[0]:
            best = (value, r, c, t, a, d)
    _, r, c, t, a, d = best
    return r, c, t, a, d, _reason_for(r, c, a, d, t)


# ============ 棋型分析（供 prompt 使用，含跳型识别） ============
def analyze_line(line):
    """分析一条连续线上的棋型，返回 (player, length, open_ends)。

    对线上每个同色子，向两侧延伸统计连续段（允许跨过一个空位的跳型，
    如 X_XX 记为 length=4 冲四）。-1 表示边界，视为封闭。
    """
    best = (0, 0, 0)  # (player, length, open_ends)
    n = len(line)
    for i, val in enumerate(line):
        if val not in (1, 2):
            continue
        player = val
        length = 1
        open_left = False
        open_right = False
        # 向左延伸
        j = i - 1
        while j >= 0:
            if line[j] == player:
                length += 1
                j -= 1
            elif line[j] == 0:
                # 空位：若再左边还是同色子则视为跳型跨过，否则记开放
                if j - 1 >= 0 and line[j - 1] == player:
                    length += 1
                    j -= 2
                else:
                    open_left = True
                    break
            else:
                break  # -1 边界或异色
        # 向右延伸
        j = i + 1
        while j < n:
            if line[j] == player:
                length += 1
                j += 1
            elif line[j] == 0:
                if j + 1 < n and line[j + 1] == player:
                    length += 1
                    j += 2
                else:
                    open_right = True
                    break
            else:
                break
        open_ends = int(open_left) + int(open_right)
        if length > best[1] or (length == best[1] and open_ends > best[2]):
            best = (player, length, open_ends)
    return best


def analyze_board(board, player):
    """分析 player 所有可落子点的棋型潜力，返回 dict of lists。

    对每个空位模拟落子，统计四个方向能形成的最大威胁，归类：
    five / live_four / dead_four / live_three / dead_three / live_two
    """
    result = {
        "five": [], "live_four": [], "dead_four": [],
        "live_three": [], "dead_three": [], "live_two": []
    }
    n = len(board)
    for r in range(n):
        for c in range(n):
            if board[r][c] != 0:
                continue
            sim = place_stone(board, r, c, player)
            best_len = 0
            best_open = 0
            for dr, dc in DIRECTIONS:
                length, open_ends = count_line(sim, r, c, player, dr, dc)
                if length > best_len or (length == best_len and open_ends > best_open):
                    best_len, best_open = length, open_ends
            if best_len >= 5:
                result["five"].append((r, c))
            elif best_len == 4 and best_open >= 1:
                (result["live_four"] if best_open == 2 else result["dead_four"]).append((r, c))
            elif best_len == 3 and best_open >= 1:
                (result["live_three"] if best_open == 2 else result["dead_three"]).append((r, c))
            elif best_len == 2 and best_open == 2:
                result["live_two"].append((r, c))
    return result


def get_threats_and_opportunities(board, turn):
    """返回当前局面的关键信息（用于 prompt）：
    - can_win: 己方能直接获胜的坐标
    - must_block: 必须堵截的敌方坐标（敌方下一步能赢 / 活四 / 冲四 / 活三）
    - my_best / enemy_best: 双方最好的棋型
    """
    me = turn
    enemy = 3 - turn

    my_analysis = analyze_board(board, me)
    enemy_analysis = analyze_board(board, enemy)

    can_win = my_analysis.get("five", [])[:1]
    must_block = enemy_analysis.get("five", [])[:1]
    if not must_block:
        must_block = enemy_analysis.get("live_four", [])[:1]
    if not must_block:
        must_block = enemy_analysis.get("dead_four", [])[:1]
    if not must_block:
        must_block = enemy_analysis.get("live_three", [])[:1]

    return {
        "can_win": can_win,
        "must_block": must_block,
        "my_live_four": my_analysis.get("live_four", [])[:3],
        "my_dead_four": my_analysis.get("dead_four", [])[:3],
        "my_live_three": my_analysis.get("live_three", [])[:3],
        "my_dead_three": my_analysis.get("dead_three", [])[:3],
        "enemy_live_four": enemy_analysis.get("live_four", [])[:3],
        "enemy_dead_four": enemy_analysis.get("dead_four", [])[:3],
        "enemy_live_three": enemy_analysis.get("live_three", [])[:3],
        "enemy_dead_three": enemy_analysis.get("dead_three", [])[:3],
    }


def build_prompt(board, turn, history, player_name):
    n = len(board)
    lo, hi = 0, n - 1
    mid = n // 2
    board_str = "\n".join([" ".join(["X" if cell == 1 else "O" if cell == 2 else "." for cell in row]) for row in board])
    history_str = "\n".join([f"{'黑' if h['player']==1 else '白'} ({h['player_name']}) 落子 ({h['row']},{h['col']})" for h in history[-5:]])

    my_color = "黑" if turn == 1 else "白"
    enemy_color = "白" if turn == 1 else "黑"
    my_stone = "X" if turn == 1 else "O"
    enemy_stone = "O" if turn == 1 else "X"

    analysis = get_threats_and_opportunities(board, turn)

    analysis_text = ""
    if analysis["can_win"]:
        r, c = analysis["can_win"][0]
        analysis_text += f"【获胜机会】你可以在({r},{c})落子直接获胜！必须下这里！\n"
    if analysis["must_block"]:
        r, c = analysis["must_block"][0]
        analysis_text += f"【必须防守】敌方在({r},{c})落子就能形成重大威胁，你必须立刻堵这里！\n"
    if analysis["my_live_four"]:
        coords = ", ".join([f"({r},{c})" for r, c in analysis["my_live_four"]])
        analysis_text += f"【你的活四】你在 {coords} 能形成活四（几乎必胜），优先下这里！\n"
    if analysis["enemy_live_four"]:
        coords = ", ".join([f"({r},{c})" for r, c in analysis["enemy_live_four"]])
        analysis_text += f"【敌方活四】敌方在 {coords} 能形成活四，必须立即堵截！\n"
    if analysis["my_dead_four"]:
        coords = ", ".join([f"({r},{c})" for r, c in analysis["my_dead_four"]])
        analysis_text += f"【你的冲四】你在 {coords} 能形成冲四，考虑下这里。\n"
    if analysis["enemy_dead_four"]:
        coords = ", ".join([f"({r},{c})" for r, c in analysis["enemy_dead_four"]])
        analysis_text += f"【敌方冲四】敌方在 {coords} 有冲四威胁，需要关注。\n"
    if analysis["my_live_three"]:
        coords = ", ".join([f"({r},{c})" for r, c in analysis["my_live_three"]])
        analysis_text += f"【你的活三】你在 {coords} 有活三（可形成活四），可以下这里扩张。\n"
    if analysis["enemy_live_three"]:
        coords = ", ".join([f"({r},{c})" for r, c in analysis["enemy_live_three"]])
        analysis_text += f"【敌方活三】敌方在 {coords} 有活三，需要防守。\n"
    if not analysis_text:
        analysis_text = "【局面分析】当前没有明显的 immediate 获胜或威胁点，优先在已有棋子附近落子，向中心发展。\n"

    prompt = f"""你是五子棋AI玩家"{player_name}"，当前执{my_color}（{my_stone}）。
敌方是{enemy_color}（{enemy_stone}）。

【棋盘坐标】
- {n}×{n} 棋盘，左上角为({lo},{lo})，右下角为({hi},{hi})
- 行号(row)范围 {lo}-{hi}，列号(col)范围 {lo}-{hi}

【当前棋盘状态】
{board_str}

【落子历史（最近5步）】
{history_str if history_str else "（暂无）"}

{analysis_text}

【核心规则】
- 横、竖、斜任意方向连续5子获胜
- 先连成5子者获胜
- 棋盘下满无人获胜则为平局

【决策优先级（严格按顺序）】
1. 如果【获胜机会】非空，必须选择那里（直接获胜）
2. 如果【必须防守】非空，必须选择那里（堵敌方威胁）
3. 否则，在【你的活四/冲四/活三】中选择最有利的
4. 否则，在已有棋子附近落子，不要下在孤立位置
5. 如果没有任何棋子，优先下在中心附近({mid},{mid})

【输出要求】
- 思考过程请控制在 150 tokens 以内，直接给出结论，不要长篇分析
- 只返回一个合法的 JSON 对象，不要输出任何其他文字（不要用 markdown 代码块）
- 格式如下（row 和 col 必须是 {lo}-{hi} 的整数，且必须是棋盘上的空位）：
{{"row": {mid}, "col": {mid}, "reason": "形成活三，威胁对方"}}
"""
    return prompt


# 固定采样温度：0.2（低随机，落子稳定；不再从数据库/前端读取）
TEMPERATURE = 0.2

# 输出 token 预算：1024。实测 reasoning 模型 effort=low 时思考仍可达 858 字（≈500+ token），
# 512 必截断 → 触发 8192 重试反而更慢（30s+）；1024 让一次调用成功（deepseek ~10s 内）。
# 极端思考失控（2000+ token）时仍用 8192 重试救活。
MAX_TOKENS_NORMAL = 1024
MAX_TOKENS_RETRY = 8192

# 分类超时：建连 5s / 思考(读响应) 30s / 发送请求体 15s / 连接池 5s
TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=5.0)

# AI 落子单次 LLM 调用的总时长上限（含全部降级/重试）。
# 之前无上限：降级链 4 组合 × 30s + 2 次预算重试，最坏 240s，
# 期间 turn 停在 AI 回合、前端锁定，表现为"AI 思考特别慢、轮不到我"。
LLM_CALL_DEADLINE = 45.0

# ============ 参数组合缓存（Fix A：消除重复 LLM 调用） ============
# 同一 (base_url, model_id, reasoning_effort) 的模型，首战后记住哪个参数组合成功、
# 把返回过 4xx 的组合标记失效。后续落子只发那 1 个成功组合 → LLM 真实请求数从
# 1~8× 降到精确 1×，避免商汤等按次计费/限流（RPM、5h 500 次）被隐性放大。
# 根因：之前每次落子因 response_format 兼容性问题会逐级降级试遍 2~4 个组合，每个被拒
# 组合都算 1 次真实 HTTP 请求 → 观战双 AI 自动连下时轻松突破 RPM 触发 429。
_combo_cache = {}  # key: (base_url, model_id, reasoning_effort) -> {"good": sig|None, "bad": set(sig), "last_used": timestamp}
_combo_cache_lock = threading.Lock()
_COMBO_CACHE_MAX_SIZE = 100  # 最多缓存 100 个不同模型的组合
_COMBO_CACHE_TTL = 3600.0  # 缓存 TTL：1 小时后失效（应对 API 兼容性变化）

# ============ OpenAI 客户端复用缓存 ============
# 避免每次调用都新建 OpenAI 客户端（内部含 httpx 连接池，频繁创建导致 TCP/TLS 无法复用）。
# 按 (base_url, api_key) 缓存，带 TTL、容量上限与线程安全保护。
_clients = {}  # key: (base_url, api_key) -> {"client": OpenAI, "last_used": timestamp}
_clients_lock = threading.Lock()
_CLIENTS_CACHE_MAX_SIZE = 50
_CLIENTS_CACHE_TTL = 3600.0


def clear_client_cache(base_url: str = None, api_key: str = None):
    """主动清理 OpenAI 客户端缓存，释放底层连接池。"""
    with _clients_lock:
        if base_url is None and api_key is None:
            for item in list(_clients.values()):
                try:
                    item["client"].close()
                except Exception:
                    pass
            _clients.clear()
        else:
            keys_to_del = [
                k for k in _clients
                if (base_url is None or k[0] == base_url) and (api_key is None or k[1] == api_key)
            ]
            for k in keys_to_del:
                item = _clients.pop(k, None)
                if item:
                    try:
                        item["client"].close()
                    except Exception:
                        pass


def _get_client(base_url: str, api_key: str) -> OpenAI:
    cache_key = (base_url, api_key)
    now = time.monotonic()
    with _clients_lock:
        # 惰性清理过期条目
        expired_keys = [k for k, v in _clients.items() if now - v["last_used"] > _CLIENTS_CACHE_TTL]
        for k in expired_keys:
            old = _clients.pop(k, None)
            if old:
                try:
                    old["client"].close()
                except Exception:
                    pass

        item = _clients.get(cache_key)
        if item is not None:
            item["last_used"] = now
            return item["client"]

        # 容量淘汰（LRU）：若达到上限，淘汰最久未使用的
        if len(_clients) >= _CLIENTS_CACHE_MAX_SIZE:
            oldest_key = min(_clients.keys(), key=lambda k: _clients[k]["last_used"])
            old = _clients.pop(oldest_key, None)
            if old:
                try:
                    old["client"].close()
                except Exception:
                    pass

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT)
        _clients[cache_key] = {"client": client, "last_used": now}
        return client


def _combo_signature(combo: dict) -> tuple:
    """组合的结构化指纹（排除 messages / max_tokens：每次落子内容与预算不同，
    但参数兼容性的差异轴只有 model / response_format / reasoning_effort / temperature / stream 等）。"""
    parts = []
    for k in sorted(combo.keys()):
        if k in ("messages", "max_tokens"):
            continue
        v = combo[k]
        try:
            s = json.dumps(v, sort_keys=True, ensure_ascii=False)
        except Exception:
            s = repr(v)
        parts.append(f"{k}={s}")
    return tuple(parts)


def chat_json(base_url, api_key, model_id, system_prompt, user_prompt,
              reasoning_effort="", max_tokens=2048):
    """通用 JSON 对话（供「AI 复盘点评」这类一次性分析使用）。

    复用与落子相同的参数降级链（response_format / reasoning_effort 兼容性），
    返回 dict；失败返回 {"_error": "原因"}。
    """
    try:
        client = _get_client(base_url, api_key)
    except Exception as e:
        return {"_error": f"客户端创建失败: {e}"}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    kwargs = dict(model=model_id, messages=messages, temperature=TEMPERATURE, max_tokens=max_tokens)
    combos = []
    if reasoning_effort:
        combos = [
            {**kwargs, "response_format": {"type": "json_object"}, "reasoning_effort": reasoning_effort},
            {**kwargs, "reasoning_effort": reasoning_effort},
        ]
    combos += [{**kwargs, "response_format": {"type": "json_object"}}, kwargs]

    deadline = time.monotonic() + LLM_CALL_DEADLINE
    last_exc = None
    response = None
    for combo in combos:
        if time.monotonic() >= deadline:
            break
        try:
            response = client.chat.completions.create(**combo)
            break
        except (BadRequestError, UnprocessableEntityError, NotFoundError, TypeError) as e:
            last_exc = e
            metrics.record_degraded(base_url, model_id)
            continue
        except Exception as e:
            last_exc = e
            break
    if response is None:
        return {"_error": str(last_exc) if last_exc else "LLM 请求失败"}
    content = response.choices[0].message.content
    if not content or not content.strip():
        return {"_error": "LLM 返回空内容"}
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start:end + 1]
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return {"_error": f"点评 JSON 解析失败: {e}"}
    return data if isinstance(data, dict) else {"_error": "点评返回不是 JSON 对象"}


def call_llm(base_url, api_key, model_id, prompt, temperature, reasoning_effort=""):
    """带可观测性埋点的 LLM 调用入口（内部转发到 _call_llm_raw）。"""
    started = time.monotonic()
    try:
        row, col, err = _call_llm_raw(base_url, api_key, model_id, prompt, temperature, reasoning_effort)
    except Exception as e:  # 兜底：任何异常都要计入指标，不能让埋点把调用链打断
        row, col, err = None, None, str(e)
    latency_ms = int((time.monotonic() - started) * 1000)
    if row is not None:
        outcome = "ok"
    else:
        kind = metrics.classify_error(err)
        outcome = kind if kind in ("timeout", "parse_fail") else "timeout"
    metrics.record_call(base_url, model_id, outcome, latency_ms, error=err if row is None else "")
    return row, col, err


def _call_llm_raw(base_url, api_key, model_id, prompt, temperature, reasoning_effort=""):
    """调用 LLM 返回 (row, col, reason)。任何失败返回 (None, None, err_msg)。

    健壮性处理：
    - content 可能为 None（某些兼容端点）→ 兜底
    - 内容可能夹杂说明文字 → 提取 JSON 子串
    - response_format / reasoning_effort 部分端点不支持 → 参数组合逐级降级重试
      （全带 → 只带 effort → 只带 rf → 都不带）
    - reasoning 类模型思考可能超预算：512 截断（finish_reason=length）→ 用 8192 重试一次
    - JSON 解析失败（reasoning 模型偶发输出不完整）→ 自动重试一次
    """
    client = _get_client(base_url, api_key)
    messages = [
        {"role": "system", "content": "你是五子棋AI，严格只返回合法JSON，格式：{\"row\": 0, \"col\": 0, \"reason\": \"理由\"}"},
        {"role": "user", "content": prompt}
    ]
    base_kwargs = dict(
        model=model_id,
        messages=messages,
        temperature=temperature,
    )
    last_err = ""
    deadline = time.monotonic() + LLM_CALL_DEADLINE
    cache_key = (base_url, model_id, reasoning_effort)
    for attempt in range(2):  # 最多重试 1 次（截断升级预算 / 偶发格式不稳）
        max_tokens = MAX_TOKENS_RETRY if attempt == 1 else MAX_TOKENS_NORMAL
        kwargs = {**base_kwargs, "max_tokens": max_tokens}
        # 参数组合降级链：逐步去掉 response_format / reasoning_effort（端点兼容性）
        combos = []
        if reasoning_effort:
            combos = [
                {**kwargs, "response_format": {"type": "json_object"}, "reasoning_effort": reasoning_effort},
                {**kwargs, "reasoning_effort": reasoning_effort},
                {**kwargs, "response_format": {"type": "json_object"}},
                kwargs,
            ]
        else:
            combos = [
                {**kwargs, "response_format": {"type": "json_object"}},
                kwargs,
            ]
        # Fix A：应用参数组合缓存——优先用已知成功的组合（单请求），跳过已知 4xx 的组合。
        # 首战会把每个被拒组合标记失效、把成功组合记为 good，后续落子直接命中 good → 不再逐组合试错。
        with _combo_cache_lock:
            # 清理过期缓存（TTL）
            now = time.monotonic()
            expired_keys = [k for k, v in _combo_cache.items() if now - v.get("last_used", 0) > _COMBO_CACHE_TTL]
            for k in expired_keys:
                del _combo_cache[k]

            # 获取或初始化缓存条目
            cached = _combo_cache.get(cache_key)
            if cached is None:
                cached = {"good": None, "bad": set(), "last_used": now}
                _combo_cache[cache_key] = cached
                # LRU 淘汰：超过最大大小时删除最久未使用的条目
                if len(_combo_cache) > _COMBO_CACHE_MAX_SIZE:
                    oldest_key = min(_combo_cache.keys(), key=lambda k: _combo_cache[k].get("last_used", 0))
                    if oldest_key != cache_key:
                        del _combo_cache[oldest_key]
            else:
                cached["last_used"] = now

            # 在锁内复制引用，避免竞态条件
            good_sig = cached["good"]
            bad_sigs = set(cached["bad"])  # 复制集合，避免并发修改

        def _combo_rank(c):
            sig = _combo_signature(c)
            if good_sig is not None and sig == good_sig:
                return 0
            if sig in bad_sigs:
                return 2
            return 1

        combos.sort(key=_combo_rank)

        response = None
        last_exc = None
        for combo in combos:
            sig = _combo_signature(combo)
            if time.monotonic() >= deadline:
                last_exc = TimeoutError(f"LLM 调用超总时长上限 {int(LLM_CALL_DEADLINE)}s")
                break
            try:
                response = client.chat.completions.create(**combo)
                # 记录成功组合，后续落子只发这 1 个 → 不再逐组合试错（省配额）
                with _combo_cache_lock:
                    _combo_cache[cache_key]["good"] = sig
                break
            except (BadRequestError, UnprocessableEntityError, NotFoundError, TypeError) as e:
                # 参数/格式不兼容（4xx）→ 快速失败，降级下一组合继续尝试；
                # 同时标记该组合失效，下次直接跳过（避免重复浪费配额）
                last_exc = e
                with _combo_cache_lock:
                    _combo_cache[cache_key]["bad"].add(sig)
                metrics.record_degraded(base_url, model_id)
                continue
            except Exception as e:
                # 超时/连接/服务端 5xx：与参数无关，后续组合大概率同样失败，
                # 直接放弃，避免组合 × 超时 白等（AI 落子卡死、用户干等）
                last_exc = e
                break
        if response is None:
            return None, None, str(last_exc) if last_exc else "LLM 请求失败"
        finish = getattr(response.choices[0], "finish_reason", "")
        content = response.choices[0].message.content
        if content is None or not content.strip():
            err = _empty_content_err(response, "content is None" if content is None else "空字符串")
            # 截断型失败：用更大预算重试一次（若仍在总时限内）
            if attempt == 0 and finish == "length" and time.monotonic() < deadline:
                last_err = err
                continue
            return None, None, err
        # 提取 JSON 子串（LLM 可能夹带说明文字）
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end + 1]
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return None, None, f"LLM 返回 JSON 解析失败: {e}"
        if not isinstance(data, dict):
            return None, None, "LLM 返回格式错误：不是 JSON 对象"
        row_raw = data.get("row")
        col_raw = data.get("col")
        if row_raw is None or col_raw is None:
            # 防御：LLM 返回合法 JSON 但缺 row/col（如 {"move": "7,7"}）→ 明确错误信息
            return None, None, "LLM 返回格式错误：缺少 row/col 字段"
        try:
            row, col = int(row_raw), int(col_raw)
        except (TypeError, ValueError):
            return None, None, f"LLM 返回格式错误：row/col 非整数 ({row_raw!r}, {col_raw!r})"
        reason = data.get("reason", "")
        return row, col, reason
    return None, None, last_err


def _empty_content_err(response, why):
    """content 为空时生成带诊断信息的错误说明（reasoning 截断 / 思考片段）。"""
    try:
        finish = response.choices[0].finish_reason
    except Exception:
        finish = "?"
    note = f"LLM 返回{why}"
    if finish == "length":
        note += "（输出被 max_tokens 截断）"
    try:
        reasoning = response.choices[0].message.reasoning_content
        if reasoning:
            note += f"；思考片段: {str(reasoning)[:60]}"
    except Exception:
        pass
    return note


# 难度档位
DIFFICULTY_EASY = "easy"
DIFFICULTY_NORMAL = "normal"
DIFFICULTY_HARD = "hard"
DIFFICULTIES = (DIFFICULTY_EASY, DIFFICULTY_NORMAL, DIFFICULTY_HARD)

# 简单档："手软"程度——从引擎 top-N 里随机挑，而不是总走最优
EASY_RANDOM_POOL = 5


def _llm_config_of(model_config):
    return (
        (model_config or {}).get("base_url") or "",
        (model_config or {}).get("model_id") or "",
    )


def _easy_choice(board, turn, forbidden, scored, llm_row, llm_col):
    """简单档落子：能一步连五就赢，否则 LLM 说哪就哪，LLM 不灵就在 top-N 里随机。"""
    if scored and scored[0][3] >= SCORE_WIN:
        r, c, t, a, d = scored[0]
        return r, c, _reason_for(r, c, a, d, t)
    if llm_row is not None and is_valid_move(board, llm_row, llm_col):
        if not (forbidden and is_forbidden_move(board, llm_row, llm_col, turn)):
            return llm_row, llm_col, "LLM 建议"
    pool = [s for s in scored[:EASY_RANDOM_POOL]] or list(scored)
    r, c, t, a, d = random.choice(pool)
    return r, c, _reason_for(r, c, a, d, t)


def ai_move(board, turn, history, model_config, player_name, forbidden=False,
            difficulty=DIFFICULTY_NORMAL):
    """AI 决策主入口。难度决定"引擎 vs LLM"的分工：

    - easy   简单：能赢就赢，其余听 LLM 的（不做评分门槛），LLM 不灵就在引擎 top-N 随机
    - normal 普通：引擎先判杀棋（连五/活四），关键手直接落子；其余交给 LLM 增强，
                   LLM 落点评分不低于引擎候选 60% 才采纳
    - hard   困难：纯引擎 + 2 层搜索（模拟对手最强应手），完全不依赖 LLM

    返回 (row, col, reason)：reason 会写进 game.logs，前端思考日志面板可见。
    """
    me = turn
    enemy = 3 - turn
    empty = game_logic.get_empty_positions(board)
    if not empty:
        return None, None, "棋盘已满"
    if difficulty not in DIFFICULTIES:
        difficulty = DIFFICULTY_NORMAL

    base_url, model_id = _llm_config_of(model_config)

    # 1) 困难档：纯引擎深算，不调 LLM（省配额、低延迟、棋力最强）
    if difficulty == DIFFICULTY_HARD:
        engine = engine_best_move(board, turn, forbidden=forbidden, depth=2)
        if engine is None:
            r, c = random.choice(empty)
            return r, c, "引擎无合法点，随机"
        r, c, t, a, d, reason = engine
        return r, c, f"引擎(深算): {reason}"

    # 2) 确定性引擎（1 层）
    engine = engine_best_move(board, turn, forbidden=forbidden, depth=1)
    if engine is None:
        r, c = random.choice(empty)
        return r, c, "引擎无合法点，随机"
    e_row, e_col, e_total, e_attack, e_defend, e_reason = engine
    scored = _static_candidates(board, turn, forbidden=forbidden)

    # 3) 调用 LLM（简单档与普通档都需要它的"风格"）
    llm_row = llm_col = None
    llm_reason = ""
    llm_note = ""
    if model_config and model_config.get("base_url"):
        try:
            prompt = build_prompt(board, turn, history, player_name)
            llm_row, llm_col, llm_reason = call_llm(
                model_config["base_url"], model_config["api_key"],
                model_config["model_id"], prompt, TEMPERATURE,
                reasoning_effort=model_config.get("reasoning_effort", "")
            )
        except Exception as e:
            llm_note = f"（LLM 异常: {type(e).__name__}: {e}）"

    # 4) 简单档：不做评分门槛，最大化 LLM 的"人味儿"
    if difficulty == DIFFICULTY_EASY:
        if llm_row is None and not llm_note:
            llm_note = f"（LLM 无有效落点: {llm_reason or '无返回'}）"
        r, c, reason = _easy_choice(board, turn, forbidden, scored, llm_row, llm_col)
        if llm_row is not None and (r, c) != (llm_row, llm_col):
            # LLM 给了点但被合法性/禁手过滤掉 → 记为 rejected
            metrics.record_outcome(base_url, model_id, "rejected")
            metrics.record_outcome(base_url, model_id, "fallback")
        if len(llm_note) > 120:
            llm_note = llm_note[:117] + "…"
        return r, c, f"{reason}{llm_note}"

    # 5) 普通档：引擎有绝对把握就直接返回（不问 LLM，快且稳）
    decisive = (
        e_attack >= SCORE_WIN            # 己方连五
        or e_defend >= SCORE_WIN         # 对方连五必须堵
        or e_attack >= SCORE_LIVE_FOUR   # 己方活四
        or e_defend >= SCORE_LIVE_FOUR   # 对方活四必须堵
    )
    if decisive:
        return e_row, e_col, f"引擎: {e_reason}"

    if llm_row is not None:
        if not is_valid_move(board, llm_row, llm_col):
            llm_note = f"（LLM 返回非法坐标 ({llm_row},{llm_col})）"
            metrics.record_outcome(base_url, model_id, "rejected")
        elif forbidden and is_forbidden_move(board, llm_row, llm_col, turn):
            llm_note = f"（LLM 落点为禁手 ({llm_row},{llm_col})）"
            metrics.record_outcome(base_url, model_id, "rejected")
        else:
            # 校验 LLM 落点评分：不低于引擎候选的 60% 才采纳
            # 60% 阈值理由：允许 LLM 在非关键局面（无连五/活四/冲四）进行风格化决策，
            # 同时防止 LLM 选择明显劣着（如完全放弃防守、孤立落子）。
            llm_attack, llm_defend = score_point(board, llm_row, llm_col, me, enemy)
            llm_total = llm_attack + int(llm_defend * DEFEND_WEIGHT)
            if llm_total >= e_total * 0.6:
                # top-N 权衡说明由 room_service._deliberation_log 统一附加，此处只给结论
                return llm_row, llm_col, f"LLM: {llm_reason or '策略落子'}"
            llm_note = f"（LLM 评分不足: {llm_total} < 引擎 {int(e_total * 0.6)}）"
            metrics.record_outcome(base_url, model_id, "rejected")
    elif not llm_note:
        llm_note = f"（LLM 调用失败: {llm_reason or '无返回'}）"

    # 6) 引擎兜底（附加 LLM 诊断信息，截断过长错误）
    metrics.record_outcome(base_url, model_id, "fallback")
    if len(llm_note) > 120:
        llm_note = llm_note[:117] + "…"
    return e_row, e_col, f"引擎: {e_reason}{llm_note}"
