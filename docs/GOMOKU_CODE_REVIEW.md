# AI 五子棋 — 规则代码 & AI 决策审查报告

**审查日期**：2026-08-11
**审查范围**：
- `backend/app/services/game_logic.py`（规则层）
- `backend/app/services/llm_service.py`（AI 决策层）
- `backend/app/services/room_service.py`（落子服务层）

---

## 一、规则层审查（game_logic.py）—— 基本正确 ✅

### 1.1 胜负判定 `check_win` — 正确

```python
def check_win(board, row, col, player):
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        count = 1
        for d in [1, -1]:
            r, c = row + dr * d, col + dc * d
            while 0 <= r < 15 and 0 <= c < 15 and board[r][c] == player:
                count += 1
                r += dr * d
                c += dc * d
        if count >= 5:
            return True
    return False
```

**结论：逻辑正确。** 从落子点沿 4 个方向双向延伸计数，`count >= 5` 判胜；边界检查在数组访问之前，无越界风险。这是标准的增量式五子棋胜负判定，没有缺陷。

### 1.2 落子合法性 `is_valid_move` — 正确

```python
def is_valid_move(board, row, col):
    return 0 <= row < 15 and 0 <= col < 15 and board[row][col] == 0
```

**结论：正确。** 边界 + 空格双重校验，无问题。

### 1.3 `place_stone` — 正确（防御式拷贝）

```python
def place_stone(board, row, col, player):
    new_board = [row[:] for row in board]
    new_board[row][col] = player
    return new_board
```

**结论：正确。** 返回深拷贝，不污染传入 board（对 `handle_move` 传入的 `json.loads(game.board)` 来说是安全的）。

### 1.4 ⚠️ 缺陷：**禁手规则未实现（规则完整性缺口）**

当前是**自由五子棋（无禁手）**：
- ❌ 无长连禁手：`count >= 5` 让六连、七连也判胜（专业规则下黑棋长连算输）
- ❌ 无三三禁手 / 四四禁手（黑棋一手同时形成两个活三 / 两个冲四应判负）
- ❌ 无"黑棋第一手天元"限制（开局规则，通常可忽略）

PRD P0-07 写的是"禁手规则（可选）"，所以这是**设计取舍而非 bug**，但如果目标是"专业五子棋"，这部分需要补。

### 1.5 ⚠️ 小问题：`handle_move` 无落子者身份校验

```python
def handle_move(db, game_id, player, row, col, player_name="Player"):
    ...
    if game.turn != player:
        return None
```

`handle_move` 自身校验了 `turn == player`，但**不校验 `player` 是否真的是该座的 AI/人类**（依赖上层 `is_human_turn` 已校验）。API 层 `make_move` 只允许人类回合落子、AI 走 `ai_turn`，当前调用路径安全，但函数边界不够严谨（若未来有其他入口直接调 `handle_move` 可越权落子）。

---

## 二、AI 决策层审查（llm_service.py）—— 三个致命问题 ❌

### 2.1 🔴【实锤】`call_llm` 实测 100% 失败 → AI 永远走随机兜底

**这是"AI 很笨"的直接原因。**

用你数据库里的两个真实配置各调用了一次：

| AI 玩家 | 端点 | 返回 | 分析 |
|---|---|---|---|
| 阶跃 (`step-3.7-flash`) | api.stepfun.com | `row=None col=None reason='Expecting value: line 1 column 1 (char 0)'` | **请求超时 10.2s**（≈timeout=10.0），返回空内容 → `json.loads('')` 抛 `Expecting value` |
| 商汤 (`sensenova-6.8-flash-lite`) | token.sensenova.cn | `row=None col=None reason="'NoneType' object has no attribute 'strip'"` | **`message.content` 是 `None`** → `.strip()` AttributeError |

```python
def call_llm(base_url, api_key, model_id, prompt, temperature):
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=10.0)
        response = client.chat.completions.create(
            model=model_id,
            messages=[...],
            temperature=temperature,
            max_tokens=100,
            response_format={"type": "json_object"},   # ← 可疑点
        )
        content = response.choices[0].message.content.strip()  # ← 崩点
        data = json.loads(content)                              # ← 崩点
        ...
    except Exception as e:
        return None, None, str(e)
```

**问题拆解**：
1. **`response_format={"type": "json_object"}`**：两个国内模型端点（StepFun / 商汤）很可能**不支持该参数**，或支持但返回结构与 OpenAI 官方不同（如 content 为空/为 None）。
2. **超时 10s 对慢模型太紧**：StepFun 实测 10.2s 才回，被 `timeout=10.0` 掐断 → 空内容。
3. **`content` 可能为 `None`**：`OpenAI` SDK 对某些兼容端点的响应解析不出来时给 None，`.strip()` 直接炸。
4. **异常被吞**：`except Exception as e: return None, None, str(e)` —— 错误只被放进 reason 字符串，`ai_turn` 根本不会看到，玩家也看不到。**AI 失败是静默的**。

**结果**：两个 AI 玩家从第一手开始就走 `ai_move` 的兜底分支。

### 2.2 🔴 兜底策略是纯随机，毫无棋理

```python
# ai_move 兜底：在已有棋子附近 2 格随机
nearby = []
if stones:
    for sr, sc in stones:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                ...
if nearby:
    row, col = random.choice(nearby)   # ← 纯随机
    reason = "兜底：在已有棋子附近落子"
```

- **不堵对方冲四**、**不抢己方活三**、**不判断连五机会**
- 只保证"落在有子附近"，落点无任何威胁评估
- 这就是你看到的"AI 不会下五子棋"：**它根本没有在下棋，只是在一个大概的范围内掷骰子**

### 2.3 🟡 棋型分析引擎有缺陷（即使 LLM 成功也用不好）

`analyze_line` / `analyze_board` 是喂给 LLM 的"局面情报"，存在几个逻辑缺陷：

**缺陷 A：只识别连续段，跳型棋形完全漏判**
```python
def analyze_line(line):
    # 只找"最长连续同色段"
    # XX_XX（双活二）、X_XX（跳三）、XXX_X（眠四变体）都无法正确归类
```
例如 `X_XX`（跳三）在实战中也是强威胁，但 `analyze_line` 只看连续段，`X_XX` 会被识别成"长度 2 的活二"，**威胁等级被低估**，LLM 拿到的情报是错的。

**缺陷 B：`must_block` 只堵"直接连五/活四/冲四"，不堵活三**
```python
must_block = enemy_analysis.get("five", [])[:1]
if not must_block:
    must_block = enemy_analysis.get("live_four", [])[:1]
if not must_block:
    must_block = enemy_analysis.get("dead_four", [])[:1]
```
敌方**活三**（下一步可成冲四/活四）完全不在"必须防守"列表里 —— 连初学者都知道要堵活三，但这里的 AI 决策情报里没有这一级。

**缺陷 C：无双威胁检测（双三/双四）**
没有识别"一个点同时形成两个活三"（双三必胜）或"冲四+活三"组合的杀着点。专业五子棋最关键的就是双威胁点，完全缺失。

**缺陷 D：analyze_board 模拟落子逻辑在边界有偏差（次要）**
`cells` 用 9 格窗口 + 边界 `-1` 填充，`center_idx=4` 恒等于中心 —— 边界处理基本正确；但 `analyze_line` 把 `-1` 当"非空非己方"，`open_ends` 计数不受影响，可接受。此条仅记录，非主要问题。

### 2.4 🟡 Prompt 构造问题（即使调用成功也会坑）

```python
【输出要求】
只返回JSON，不要其他内容：
{"row": 行号, "col": 列号, "reason": "简短理由"}
```

- "行号" "列号" 是**中文占位符**，不是合法 JSON 值 —— 如果 LLM 原样照抄（很多小模型会），`json.loads` 直接失败
- 应该给出**示例值**：`{"row": 7, "col": 7, "reason": "形成活四"}`，并说明"输出为 0-14 的整数"
- `max_tokens=100` 偏紧：reason 写中文时容易截断 JSON

---

## 三、改进建议（按优先级排序）

### P0-1：给 AI 加"确定性评分引擎"作为第一决策层（治本）

**核心思路：不依赖 LLM 也能下好棋。** 加一个纯规则评分函数，LLM 只是"策略增强器"而非"唯一大脑"：

```python
# 评分表（权重可调）
WIN = 1_000_000      # 连五
LIVE_FOUR = 100_000  # 活四（必应）
DEAD_FOUR = 50_000   # 冲四
LIVE_THREE = 10_000  # 活三
DEAD_THREE = 1_000   # 眠三
LIVE_TWO = 500       # 活二
```

对每个空位：
1. 若己方落子即五连 → 直接下（评分 +1e6）
2. 若对方落子即五连 → 必须堵（该点 +1e6 防守分）
3. 否则对每个空位分别算「攻分（己方棋型）+ 防分（封堵对方棋型）」，取总分最高点
4. 只有分数接近并列（比如差 < 100）时才问 LLM 做风格化决策，LLM 失败就取确定性最高分

这一层哪怕 LLM 全挂，AI 也能达到"会下棋"的水平：会进攻（活三/冲四）、会防守（堵活三、堵冲四、堵连五点）。

### P0-2：修复 `call_llm`（配合上面，LLM 作为增强器可用）

```python
def call_llm(base_url, api_key, model_id, prompt, temperature):
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=30.0)  # 10→30
        kwargs = {
            "model": model_id,
            "messages": [...],
            "temperature": temperature,
            "max_tokens": 200,
        }
        # response_format 不是所有兼容端点都支持，先尝试带，失败重试不带
        try:
            resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        content = (resp.choices[0].message.content or "").strip()  # None 防御
        if not content:
            return None, None, "LLM 返回空内容"
        # 尝试从内容中提取 JSON 子串（LLM 可能夹杂说明文字）
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end+1]
        data = json.loads(content)
        row, col = int(data["row"]), int(data["col"])
        return row, col, data.get("reason", "")
    except Exception as e:
        return None, None, str(e)
```

关键点：`content or ""` 防 None、JSON 子串提取、`response_format` 失败重试、超时放宽到 30s。

### P0-3：修复棋型分析引擎（让情报准确）

- 支持跳型识别（在 `analyze_line` 里对 `X_XX`、`XX_XX`、`XXX_X` 等跳形也统计威胁）
- `must_block` 增加**活三**一级：敌方活三时，把堵点（活三两端）加入高优先防守
- 增加**双威胁检测**：对每个空位计算"落子后同时形成两个活三/冲四" → 标记为杀着点（攻防分 ×2）

### P1-1：修正 prompt 示例

```python
【输出要求】
只返回 JSON 对象，不要输出任何其他文字。
格式（row/col 必须是 0-14 的整数，且必须是棋盘上的空位）：
{"row": 7, "col": 7, "reason": "形成活四"}
```

### P1-2：补禁手规则（如目标是专业规则）

在 `game_logic.py` 增加：
- `is_forbidden_move(board, row, col, player)`：黑棋长连、双三、双四判负
- `check_win` 保持自由规则；在 `handle_move` 里根据房间配置决定是否启用禁手
- 禁手生效时白棋胜利条件改为"黑棋禁手 + 白棋连五"任一

### P2-1：`handle_move` 加身份校验参数

给 `handle_move` 增加 `seat_role` / `expected_player_id` 参数，杜绝未来入口越权。

---

## 四、结论

| 层 | 状态 | 关键问题 |
|---|---|---|
| 规则层 `game_logic.py` | ✅ 基本正确 | 无禁手（设计取舍）；`handle_move` 缺身份校验 |
| AI 调用 `call_llm` | 🔴 100% 失败（实测） | 超时 10s 太紧 / `response_format` 兼容性 / `content=None` 未防御 / 异常静默 |
| AI 兜底 `ai_move` | 🔴 纯随机 | 不攻不守，无任何棋理 |
| 棋型分析 `analyze_board` | 🟡 有缺陷 | 跳型漏判 / 不堵活三 / 无双威胁检测 |
| Prompt | 🟡 有坑 | 中文占位符非合法 JSON 示例 |

**一句话总结：AI 表现得笨不是因为它"不会用大模型"，而是 `call_llm` 从第一手起就 100% 失败（超时 + 解析崩），落到纯随机兜底上。** 修复路径：确定性评分引擎（保底会下棋）→ 修 `call_llm`（LLM 可用作增强）→ 修棋型分析（情报准确）→ 补禁手（规则完整）。
