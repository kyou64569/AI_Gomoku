"""思考（Reasoning）参数「厂商方言」注册表。

把「用户意图（未设置 / off / low / medium / high）→ 厂商请求体字段」的翻译收敛成
数据驱动的注册表：主流程只表达统一意图，由模型配置的 thinking_profile 决定请求体里
具体用哪个字段、什么形状。新增厂商 = 在 THINKING_PROFILES 加一个函数，主流程零改动。

三段式：归一化（resolve_thinking_spec）→ 预算（THINKING_BUDGET_BY_LEVEL）→
适配（apply_thinking_profile）。关键语义：

- 「未设置」≠「关闭」：未设置一个思考字段都不发（跟随模型默认，存量配置请求体
  逐字节不变）；只有显式 off 才发禁用字段。
- 关闭思考的字段「位置/形状/类型」是头号坑：顶层 enable_thinking、嵌套
  chat_template_kwargs、字符串/对象/布尔 thinking、布尔 thinking_mode 同名不同形，
  给错的那一方会照收返回 200 然后继续思考（或像 NVIDIA 那样直接 400）。
- 未知 profile 报错而不是静默回退：回退会把开关放到网关不读的位置，得到
  「配了关闭仍在思考」的无信号静默失效。
- thinking-mode（Intern-S1）与 nvidia（NVIDIA NIM）没有强度档：不发 reasoning_effort，
  且应声明 thinking_capability='binary'，否则 UI 会假装能分级。
"""

# 玩家思考档位：'' = 未设置（跟随模型默认，不发任何思考字段）；off = 显式关闭
THINKING_LEVELS = ("", "off", "low", "medium", "high")
# 模型「思考能力声明」（挂在 ModelConfig 上，缺省 graded）：
#   graded=真分级；binary=只支持开/关（各档一律满血）；none=不发任何思考参数
THINKING_CAPABILITIES = ("graded", "binary", "none")
THINKING_CAPABILITY_DEFAULT = "graded"
THINKING_PROFILE_DEFAULT = "openai-native"
THINKING_PROFILE_KEYS = (
    "openai-native",   # 顶层 enable_thinking（Qwen3 / vLLM / 多数端点事实标准）
    "chat-template",   # chat_template_kwargs.enable_thinking（小红书 Dots / Agnes：顶层被忽略）
    "thinking-flag",   # thinking: "enabled" | "disabled"（MiniMax-M3 系，字符串）
    "thinking-type",   # thinking: {"type": ...}（腾讯混元 hy3/hy4 系，对象；与上一条同名字段不同形状）
    "effort-only",     # 无独立开关，仅 reasoning_effort（关闭发 "none"）
    "thinking-mode",   # thinking_mode: true | false（Intern-S1，布尔，无强度档）
    "nvidia",          # thinking: true | false（NVIDIA NIM，布尔，无强度档；字符串会 400）
)

# 思考开启时的 max_tokens 预算下限（思考 token 计入输出上限，低预算会把走法 JSON
# 截断）。off / 未设置一律不动预算——这是存量配置请求体零变化的保证。
THINKING_BUDGET_BY_LEVEL = {"low": 4096, "medium": 8192, "high": 24576}

_EFFORT_BY_LEVEL = {"low": "low", "medium": "medium", "high": "high"}


def resolve_thinking_spec(level, capability="graded"):
    """第 1 段：归一化（与厂商无关）。只回答「开/关/几档」，不关心字段名。

    返回 {'level': str|None, 'disable_thinking': bool, 'effort': str|None}，
    level=None 表示「不发任何思考字段」（未设置 / capability=none）。
    """
    if capability not in THINKING_CAPABILITIES:
        capability = "graded"
    if level not in THINKING_LEVELS:
        level = ""
    if capability == "none" or level == "":
        return {"level": None, "disable_thinking": False, "effort": None}
    if level == "off":
        return {"level": "off", "disable_thinking": True, "effort": None}
    if capability == "binary":
        # binary 不分档：low/medium/high 一律满血开（映射 high，同时按 high 取预算防截断）
        return {"level": "high", "disable_thinking": False, "effort": "high"}
    return {"level": level, "disable_thinking": False, "effort": _EFFORT_BY_LEVEL[level]}


def _profile_openai_native(body, spec):
    """顶层 enable_thinking（Qwen3 / vLLM / 多数推理端点事实标准）。"""
    if spec["level"] is None:
        return []
    if spec["disable_thinking"]:
        body["enable_thinking"] = False
        return ["enable_thinking"]
    body["enable_thinking"] = True
    occupied = ["enable_thinking"]
    if spec["effort"]:
        body["reasoning_effort"] = spec["effort"]
        occupied.append("reasoning_effort")
    return occupied


def _profile_chat_template(body, spec):
    """嵌套在 chat_template_kwargs（小红书 Dots / Agnes：顶层 enable_thinking 会被忽略）。"""
    if spec["level"] is None:
        return []
    body.setdefault("chat_template_kwargs", {})["enable_thinking"] = not spec["disable_thinking"]
    occupied = ["chat_template_kwargs"]
    if not spec["disable_thinking"] and spec["effort"]:
        body["reasoning_effort"] = spec["effort"]
        occupied.append("reasoning_effort")
    return occupied


def _profile_thinking_flag(body, spec):
    """字符串型 thinking（MiniMax-M3 系）。取值集合是厂商自定义的（可能含 adaptive）。"""
    if spec["level"] is None:
        return []
    body["thinking"] = "disabled" if spec["disable_thinking"] else "enabled"
    occupied = ["thinking"]
    if not spec["disable_thinking"] and spec["effort"]:
        body["reasoning_effort"] = spec["effort"]
        occupied.append("reasoning_effort")
    return occupied


def _profile_thinking_type(body, spec):
    """对象型 thinking（腾讯混元 hy3 / hy4-preview）：与 thinking-flag 同名字段、不同形状。"""
    if spec["level"] is None:
        return []
    body["thinking"] = {"type": "disabled" if spec["disable_thinking"] else "enabled"}
    occupied = ["thinking"]
    if not spec["disable_thinking"] and spec["effort"]:
        body["reasoning_effort"] = spec["effort"]
        occupied.append("reasoning_effort")
    return occupied


def _profile_effort_only(body, spec):
    """无独立开关，仅 reasoning_effort：关闭发 "none"（⚠️ 该写法未经真网关证实，接前先探测）。"""
    if spec["level"] is None:
        return []
    body["reasoning_effort"] = "none" if spec["disable_thinking"] else spec["effort"]
    return ["reasoning_effort"]


def _profile_thinking_mode(body, spec):
    """布尔 thinking_mode（Intern-S1 / chat.intern-ai.org.cn）。字符串会被判 400；无强度档。"""
    if spec["level"] is None:
        return []
    body["thinking_mode"] = not spec["disable_thinking"]
    return ["thinking_mode"]


def _profile_nvidia(body, spec):
    """布尔 thinking（NVIDIA NIM / Nemotron）。字符串写法直接 400；无强度档，不发 reasoning_effort。"""
    if spec["level"] is None:
        return []
    body["thinking"] = not spec["disable_thinking"]
    return ["thinking"]


# 第 3 段：适配注册表。每个 profile 是纯函数 (body, spec) -> list[str]：
# 把统一意图 spec 翻译成该厂商的请求体字段，返回自己实际写入的字段名
# （供降级链被端点 4xx 拒绝时干净移除）。level=None 时一律不写、不占用。
THINKING_PROFILES = {
    "openai-native": _profile_openai_native,
    "chat-template": _profile_chat_template,
    "thinking-flag": _profile_thinking_flag,
    "thinking-type": _profile_thinking_type,
    "effort-only": _profile_effort_only,
    "thinking-mode": _profile_thinking_mode,
    "nvidia": _profile_nvidia,
}


def apply_thinking_profile(profile_key, body, spec):
    """按方言把统一意图套用到请求体（原地修改），返回占用的字段名列表。"""
    fn = THINKING_PROFILES.get(profile_key)
    if fn is None:
        raise ValueError(
            f"未知的思考方言 thinking_profile='{profile_key}'，"
            f"合法值：{' | '.join(THINKING_PROFILE_KEYS)}"
        )
    return fn(body, spec)


def build_thinking_params(profile_key, level, capability="graded"):
    """便捷入口：据「方言 + 玩家档位 + 模型能力」产出思考参数与预算下限。

    返回 (thinking_kwargs, max_tokens_floor)。未设置（level=''）或 capability=none
    时返回 ({}, None)——调用方请求体保持与旧版逐字节一致；思考开启时 floor 取
    档位预算（off 显式关闭不动预算）。
    """
    spec = resolve_thinking_spec(level, capability)
    body = {}
    occupied = apply_thinking_profile(profile_key, body, spec)
    if not occupied:
        return {}, None
    floor = THINKING_BUDGET_BY_LEVEL.get(spec["level"]) if spec["level"] else None
    return body, floor
