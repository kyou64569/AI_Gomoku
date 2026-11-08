"""Gemini / Anthropic 原生协议分支（不走 OpenAI 兼容方言注册表）。

参考 AI_Draughts `server/src/services/modelProvider.js`（真机实测过的形状），要点：

- Gemini：`{裸origin}/v1beta/models/{model}:generateContent`（路径硬编码 /v1beta，
  base_url 不要带版本段）；认证走 `x-goog-api-key` 头（不是 ?key= 查询参数）。
  思考 = `generationConfig.thinkingConfig.thinkingLevel`（off→'minimal' 等效关闭、
  low/medium/high 直传）+ `includeThoughts:true` 必带（否则响应不含 thought 块）。
  ⚠️ 与 AI_Draughts 不同：未设置（''）→ 完全省略 thinkingConfig（跟随模型默认，
  本项目「未设置=不发思考字段」契约）；AI_Draughts 强制 default→medium 是其
  「保证能思考」的产品选择，想要思考的用户显式选档即可。
- Anthropic：`{base}/v1/messages`（版本段在路径里，base_url 不要带 /v1）；
  头 `x-api-key` + `anthropic-version: 2023-06-01`。`max_tokens` 官方必填。
  思考 = `thinking: {type:'enabled', budget_tokens:N}`（指南 §4 🟢）；关闭/未设置
  就是「不传 thinking」（该方言没有禁用字段）。AI_Draughts 刻意未实装思考块
  （只抬 max_tokens），本项目按指南实装：budget 取思考档位表（4096/8192/24576），
  `max_tokens = budget + 答题空间`（budget ≥1024 且 < max_tokens 由构造保证）。
  ⚠️ 指南未写的一条官方约束：thinking 开启时 temperature 必须 1（0.2 会被 400），
  故开思考时 temperature 切 1，关闭时维持 0.2。

均为非流式单轮（system + user），请求体由本模块纯函数构建，便于直接单测。
"""

from urllib.parse import quote

from . import thinking_profiles

# 支持的协议（ModelConfig.provider）。未知/缺省 → openai（存量配置零行为变化）。
LLM_PROVIDERS = ("openai", "anthropic", "gemini")
PROVIDER_DEFAULT = "openai"

ANTHROPIC_VERSION = "2023-06-01"
# Anthropic 的 max_tokens 要同时装下思考与答案：budget 之外再留答题空间
# （指南 §4 示例：high → budget_tokens 24576 + max_tokens 28672）。
ANTHROPIC_ANSWER_SPACE = 4096

# Gemini 3 思考等级映射（off 无真正关闭开关，'minimal' 等效关闭；'' 未设置 → 不写）
GEMINI_LEVEL_BY_LEVEL = {"off": "minimal", "low": "low", "medium": "medium", "high": "high"}
# Gemini 思考 token 计入 maxOutputTokens，按档位抬高防走法 JSON 被挤掉
GEMINI_BUDGET_BY_LEVEL = {"off": 1024, "low": 2048, "medium": 8192, "high": 24576}

GEMINI_BASE_BUDGET = 1024  # 无思考档的基础输出预算


def normalize_provider(provider):
    """provider 白名单归一：未知/缺省 → openai（与 AI_Draughts 一致，路由层另行严格校验）。"""
    return provider if provider in LLM_PROVIDERS else PROVIDER_DEFAULT


def trim_version_suffix(base: str) -> str:
    """剥掉 base_url 末尾的版本段（/v1beta、/v1alpha、/v1）：原生协议的版本段由代码
    拼接，用户习惯性带上时会拼出 /v1beta/v1beta 双版本而 404（厂商适配指南 §9 教训）。
    拉取模型与调用两条路径都必须走这里，否则会出现「列表能拉、调用 404」的劈叉。
    """
    base = base.rstrip("/")
    low = base.lower()
    for suf in ("/v1beta", "/v1alpha", "/v1"):
        if low.endswith(suf):
            return base[:-len(suf)]
    return base


def build_gemini_request(base_url, api_key, model, system_text, user_text,
                         level, capability="graded", max_tokens=None, include_thinking=True):
    """构建 Gemini generateContent 请求。

    返回 (url, headers, body, floor, thinking_active)：floor 为思考激活时的
    maxOutputTokens 预算下限（未激活 None）；max_tokens 传入时取 max（截断升级用，
    不会把预算反降）。
    """
    spec = thinking_profiles.resolve_thinking_spec(level, capability)
    gemini_level = GEMINI_LEVEL_BY_LEVEL.get(spec["level"]) if spec["level"] else None
    thinking_active = bool(include_thinking and gemini_level)

    floor = GEMINI_BUDGET_BY_LEVEL.get(spec["level"]) if thinking_active else None
    max_output = max(max_tokens or 0, floor or 0) or GEMINI_BASE_BUDGET

    generation_config = {"temperature": 0.2, "maxOutputTokens": max_output}
    if thinking_active:
        # includeThoughts 必带：否则响应不返回 thought 块（前端思考展示无内容）
        generation_config["thinkingConfig"] = {"thinkingLevel": gemini_level, "includeThoughts": True}

    body = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": generation_config,
    }
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    url = f"{trim_version_suffix(base_url)}/v1beta/models/{quote(model, safe='')}:generateContent"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-goog-api-key"] = api_key
    return url, headers, body, floor, thinking_active


def build_anthropic_request(base_url, api_key, model, system_text, user_text,
                            level, capability="graded", max_tokens=None, include_thinking=True):
    """构建 Anthropic messages 请求。

    返回 (url, headers, body, floor, thinking_active)：thinking 激活时 floor =
    budget + 答题空间（即本请求的 max_tokens 总额）；关闭/未激活 floor=None。
    """
    spec = thinking_profiles.resolve_thinking_spec(level, capability)
    budget = None
    if include_thinking and spec["level"] not in (None, "off"):
        # 档位预算表（low 4096 / medium 8192 / high 24576），≥1024 下限由表保证
        budget = thinking_profiles.THINKING_BUDGET_BY_LEVEL[spec["level"]]

    if budget:
        max_tokens_total = max(max_tokens or 0, budget + ANTHROPIC_ANSWER_SPACE)
        floor = max_tokens_total
    else:
        max_tokens_total = max_tokens or GEMINI_BASE_BUDGET
        floor = None

    body = {
        "model": model,
        "max_tokens": max_tokens_total,
        # thinking 开启时 temperature 必须 1（官方约束，0.2 会被 400）
        "temperature": 1 if budget else 0.2,
        "messages": [{"role": "user", "content": user_text}],
    }
    if budget:
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if system_text:
        body["system"] = system_text

    url = f"{trim_version_suffix(base_url)}/v1/messages"
    headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
    if api_key:
        headers["x-api-key"] = api_key
    return url, headers, body, floor, bool(budget)


def parse_gemini_response(data):
    """解析 generateContent 响应 → (text, truncated)。

    thought 块（part.thought===true）与答案块分离；答案为空时回退拼接 thought 块
    （截断场景可能只有思考没有答案）。finishReason==='MAX_TOKENS' 视为截断。
    直接按 part 自身字段过滤（不做 parts/texts 交叉 zip——text 缺失/非字符串的
    part 会让 zip 错位配对，thought 标志张冠李戴）。
    """
    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    answer = "".join(p.get("text", "") for p in parts
                     if isinstance(p.get("text"), str) and p.get("thought") is not True)
    if not answer.strip():
        answer = "".join(p.get("text", "") for p in parts
                         if isinstance(p.get("text"), str) and p.get("thought") is True)
    finish = candidate.get("finishReason") or ""
    return answer, finish == "MAX_TOKENS"


def parse_anthropic_response(data):
    """解析 messages 响应 → (text, truncated)。

    取 type==='text' 块；为空时回退 type==='thinking' 块。stop_reason==='max_tokens'
    视为截断。
    """
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    if not text.strip():
        text = "".join(b.get("thinking", "") for b in blocks if isinstance(b, dict) and b.get("type") == "thinking")
    stop = data.get("stop_reason") or ""
    return text, stop == "max_tokens"
