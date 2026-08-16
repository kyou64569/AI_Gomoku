import json
import random
import time
import threading
import httpx
from openai import OpenAI
from openai import BadRequestError, UnprocessableEntityError, NotFoundError, APITimeoutError, APIConnectionError
from . import game_logic
from .game_logic import DIRECTIONS, N, count_line, place_stone, is_valid_move, is_forbidden_move

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


def engine_best_move(board, turn, forbidden=False):
    """纯确定性评分：遍历所有空位，返回 (row, col, total_score, attack, defend, reason)。

    优先级天然被权重覆盖：
    - 己方能连五 → 攻分最高（SCORE_WIN）
    - 对方能连五 → 该点防分最高（SCORE_WIN × DEFEND_WEIGHT），必然选它堵
    - 己方活四/对方活四 → 次高分
    - 常规局面 → 攻防总和高者胜出
    """
    me = turn
    enemy = 3 - turn
    center = (N - 1) / 2.0
    best = None
    for r in range(N):
        for c in range(N):
            if board[r][c] != 0:
                continue
            if forbidden and is_forbidden_move(board, r, c, me):
                continue
            attack, defend = score_point(board, r, c, me, enemy)
            total = attack + int(defend * DEFEND_WEIGHT)
            # 中心偏置：空盘/早期无棋型时优先往中心发展（不超过 1 个活二的分值）
            center_bias = max(0, 60 - int((abs(r - center) + abs(c - center)) * 8))
            total += center_bias
            if best is None or total > best[2]:
                best = (r, c, total, attack, defend, "")
    if best is None:
        return None
    row, col, total, attack, defend, _ = best
    # 生成人类可读的 reason
    if attack >= SCORE_WIN:
        reason = f"一步连五 ({row},{col})"
    elif defend >= SCORE_WIN:
        reason = f"堵对方连五 ({row},{col})"
    elif attack >= SCORE_LIVE_FOUR:
        reason = f"形成活四 ({row},{col})"
    elif defend >= SCORE_LIVE_FOUR:
        reason = f"堵对方活四 ({row},{col})"
    elif attack >= SCORE_DEAD_FOUR:
        reason = f"形成冲四 ({row},{col})"
    elif defend >= SCORE_DEAD_FOUR:
        reason = f"堵对方冲四 ({row},{col})"
    elif attack >= SCORE_LIVE_THREE:
        reason = f"形成活三 ({row},{col})"
    elif defend >= SCORE_LIVE_THREE:
        reason = f"堵对方活三 ({row},{col})"
    else:
        reason = f"攻{attack}/防{defend}"
    return row, col, total, attack, defend, reason


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
    for r in range(N):
        for c in range(N):
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
- 15×15 棋盘，左上角为(0,0)，右下角为(14,14)
- 行号(row)范围 0-14，列号(col)范围 0-14

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
5. 如果没有任何棋子，优先下在中心附近(7,7)

【输出要求】
- 思考过程请控制在 150 tokens 以内，直接给出结论，不要长篇分析
- 只返回一个合法的 JSON 对象，不要输出任何其他文字（不要用 markdown 代码块）
- 格式如下（row 和 col 必须是 0-14 的整数，且必须是棋盘上的空位）：
{{"row": 7, "col": 7, "reason": "形成活三，威胁对方"}}
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


def call_llm(base_url, api_key, model_id, prompt, temperature, reasoning_effort=""):
    """调用 LLM 返回 (row, col, reason)。任何失败返回 (None, None, err_msg)。

    健壮性处理：
    - content 可能为 None（某些兼容端点）→ 兜底
    - 内容可能夹杂说明文字 → 提取 JSON 子串
    - response_format / reasoning_effort 部分端点不支持 → 参数组合逐级降级重试
      （全带 → 只带 effort → 只带 rf → 都不带）
    - reasoning 类模型思考可能超预算：512 截断（finish_reason=length）→ 用 8192 重试一次
    - JSON 解析失败（reasoning 模型偶发输出不完整）→ 自动重试一次
    """
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT)
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
        data = json.loads(content)
        row, col = int(data["row"]), int(data["col"])
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


def ai_move(board, turn, history, model_config, player_name, forbidden=False):
    """AI 决策主入口：

    1. 先跑确定性评分引擎（engine_best_move）—— 保证不依赖 LLM 也能下好棋
    2. 若引擎没有绝对把握（无连五/活四/冲四级威胁），尝试 LLM 增强决策
    3. LLM 返回的点如果合法且评分不差于引擎候选，采纳 LLM；否则用引擎
    """
    me = turn
    enemy = 3 - turn
    empty = game_logic.get_empty_positions(board)
    if not empty:
        return None, None, "棋盘已满"

    # 1) 确定性引擎
    engine = engine_best_move(board, turn, forbidden=forbidden)
    if engine is None:
        r, c = random.choice(empty)
        return r, c, "引擎无合法点，随机"
    e_row, e_col, e_total, e_attack, e_defend, e_reason = engine

    # 2) 引擎有绝对把握就直接返回（不问 LLM，快且稳）
    decisive = (
        e_attack >= SCORE_WIN            # 己方连五
        or e_defend >= SCORE_WIN         # 对方连五必须堵
        or e_attack >= SCORE_LIVE_FOUR   # 己方活四
        or e_defend >= SCORE_LIVE_FOUR   # 对方活四必须堵
    )
    if decisive:
        return e_row, e_col, f"引擎: {e_reason}"

    # 3) 常规局面 → LLM 增强（可选，失败不影响；失败原因透传进 reason 便于日志诊断）
    llm_note = ""
    if model_config and model_config.get("base_url"):
        try:
            prompt = build_prompt(board, turn, history, player_name)
            row, col, llm_reason = call_llm(
                model_config["base_url"], model_config["api_key"],
                model_config["model_id"], prompt, TEMPERATURE,
                reasoning_effort=model_config.get("reasoning_effort", "")
            )
            if row is None:
                llm_note = f"（LLM 调用失败: {llm_reason or '无返回'}）"
            elif not is_valid_move(board, row, col):
                llm_note = f"（LLM 返回非法坐标 ({row},{col})）"
            elif forbidden and is_forbidden_move(board, row, col, turn):
                llm_note = f"（LLM 落点为禁手 ({row},{col})）"
            else:
                # 校验 LLM 落点评分：不低于引擎候选的 60% 才采纳
                # 60% 阈值理由：允许 LLM 在非关键局面（无连五/活四/冲四）进行风格化决策，
                # 同时防止 LLM 选择明显劣着（如完全放弃防守、孤立落子）。
                # 引擎评分基于棋型权重（连五=1000万、活四=100万等），60% 足以过滤掉
                # 纯随机或明显错误的落子，同时保留 LLM 的策略多样性。
                llm_attack, llm_defend = score_point(board, row, col, me, enemy)
                llm_total = llm_attack + int(llm_defend * DEFEND_WEIGHT)
                if llm_total >= e_total * 0.6:
                    return row, col, f"LLM: {llm_reason or '策略落子'}"
                llm_note = f"（LLM 评分不足: {llm_total} < 引擎 {int(e_total * 0.6)}）"
        except Exception as e:
            llm_note = f"（LLM 异常: {type(e).__name__}: {e}）"

    # 4) 引擎兜底（附加 LLM 诊断信息，截断过长错误）
    if len(llm_note) > 120:
        llm_note = llm_note[:117] + "…"
    return e_row, e_col, f"引擎: {e_reason}{llm_note}"
