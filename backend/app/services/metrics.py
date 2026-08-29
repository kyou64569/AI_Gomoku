"""LLM 调用可观测性：轻量进程内计数器（P5）。

按 (base_url, model_id) 维度累计：
- calls:     发起的落子决策次数
- ok:        LLM 返回了可解析坐标
- timeout:   超时 / 连接失败 / 被总时限截断
- parse_fail:返回内容不是合法 JSON 或缺 row/col
- rejected:  LLM 给了坐标但不合法（越界/已占/禁手）
- fallback:  最终没采纳 LLM、走引擎或随机兜底的次数
- latency_ms_sum / latency_ms_max: 单次决策耗时（含降级重试）

用途：多模型对比时能一眼看出哪个模型"慢 / 爱超时 / 输出格式不稳"，
前端「系统状态」页直接消费 /api/stats。
"""
import threading
import time

_stats = {}
_stats_lock = threading.Lock()

# 单模型最多保留的近期错误样本数
_MAX_SAMPLES = 5

_METRIC_KEYS = (
    "calls", "ok", "timeout", "parse_fail", "rejected", "fallback",
    "degraded", "latency_ms_sum", "latency_ms_max", "last_error", "updated_at",
)

# 超时类错误的判定关键词（用于把 err 文本归类到 timeout）
_TIMEOUT_HINTS = (
    "timeout", "timed out", "timedout", "connection", "connect", "总时长上限",
    "apitimeouterror", "apiconnectionerror",
)


def _blank():
    return {k: 0 for k in _METRIC_KEYS} | {"last_error": "", "updated_at": 0.0}


def _entry(base_url: str, model_id: str) -> dict:
    key = f"{base_url or ''}|{model_id or ''}"
    with _stats_lock:
        item = _stats.get(key)
        if item is None:
            item = _blank()
            item["base_url"] = base_url or ""
            item["model_id"] = model_id or ""
            _stats[key] = item
        return item


def classify_error(err: str) -> str:
    """把 LLM 错误信息归类为 timeout / parse_fail / other。"""
    low = (err or "").lower()
    if any(h in low for h in _TIMEOUT_HINTS):
        return "timeout"
    if "json" in low or "row/col" in low or "format" in low or "格式" in low:
        return "parse_fail"
    return "other"


def record_call(base_url: str, model_id: str, outcome: str, latency_ms: int, error: str = ""):
    """记录一次 LLM 决策结果。

    outcome: ok | timeout | parse_fail | rejected | fallback
    """
    item = _entry(base_url, model_id)
    with _stats_lock:
        item["calls"] += 1
        if outcome in ("ok", "timeout", "parse_fail", "rejected", "fallback"):
            item[outcome] += 1
        item["latency_ms_sum"] += latency_ms
        if latency_ms > item["latency_ms_max"]:
            item["latency_ms_max"] = latency_ms
        if error:
            item["last_error"] = str(error)[:300]
        item["updated_at"] = time.time()


def record_outcome(base_url: str, model_id: str, outcome: str):
    """只累计结果计数，不增加 calls（用于 ai_move 在已有一次 LLM 调用之上补充判定）。

    例：LLM 给了坐标但不合法 → 在 call_llm 已记 ok 的基础上再记一次 rejected。
    """
    if not base_url and not model_id:
        return
    item = _entry(base_url, model_id)
    with _stats_lock:
        if outcome in ("ok", "timeout", "parse_fail", "rejected", "fallback"):
            item[outcome] += 1
        item["updated_at"] = time.time()


def record_degraded(base_url: str, model_id: str):
    """记录一次参数组合降级（端点不兼容 response_format / reasoning_effort）。"""
    item = _entry(base_url, model_id)
    with _stats_lock:
        item["degraded"] += 1


def snapshot() -> list:
    """导出全部指标（供 /api/stats 返回）。"""
    with _stats_lock:
        rows = []
        for item in _stats.values():
            calls = item["calls"]
            rows.append({
                "base_url": item.get("base_url", ""),
                "model_id": item.get("model_id", ""),
                "calls": calls,
                "ok": item["ok"],
                "timeout": item["timeout"],
                "parse_fail": item["parse_fail"],
                "rejected": item["rejected"],
                "fallback": item["fallback"],
                "degraded": item["degraded"],
                "success_rate": round(item["ok"] / calls * 100, 1) if calls else 0.0,
                "avg_latency_ms": int(item["latency_ms_sum"] / calls) if calls else 0,
                "max_latency_ms": item["latency_ms_max"],
                "last_error": item["last_error"],
                "updated_at": item["updated_at"],
            })
    rows.sort(key=lambda r: (-r["calls"], r["model_id"]))
    return rows


def reset():
    """清空全部指标（测试 / 运维手动重置用）。"""
    with _stats_lock:
        _stats.clear()
