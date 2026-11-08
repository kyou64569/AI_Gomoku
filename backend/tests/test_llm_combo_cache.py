"""Fix A：验证 call_llm 对同一模型的后续落子只发 1 个成功组合（不再逐组合试错浪费配额）。

模拟商汤网关行为：带 response_format 的组合必抛 4xx，不带的成功。
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openai import BadRequestError

import app.services.llm_service as llm_service


@pytest.fixture(autouse=True)
def _clear_cache():
    llm_service._combo_cache.clear()
    yield
    llm_service._combo_cache.clear()


class _FakeCompletions:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        # 模拟严格网关（如商汤）：非标准参数 response_format / reasoning_effort 都会被拒
        if kwargs.get("response_format") or kwargs.get("reasoning_effort"):
            _resp = SimpleNamespace(
                status_code=400,
                headers={},
                request=SimpleNamespace(method="POST", url="http://fake"),
            )
            raise BadRequestError("param rejected", response=_resp, body=None)
        # 不带 response_format 的组合成功
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"row": 7, "col": 7, "reason": "ok"}'),
                )
            ]
        )


class _FakeClient:
    def __init__(self, calls, *a, **k):
        self.chat = SimpleNamespace(completions=_FakeCompletions(calls))


def _client_factory(calls):
    def _factory(*a, **k):
        return _FakeClient(calls, *a, **k)

    return _factory


def test_combo_cache_reduces_requests_rf_rejection():
    calls = []
    with patch.object(llm_service, "OpenAI", _client_factory(calls)):
        # 首战：带 rf 的 combo 被拒，落到无 rf 的 combo → 2 次请求
        r, c, _ = llm_service.call_llm("http://x", "k", "m", "p", 0.2, reasoning_effort="")
        assert (r, c) == (7, 7)
        # 行为契约：首战需经历"尝试-被拒-成功"，至少 2 次、最多组合总数；不硬编码具体数量
        assert len(calls) >= 2, f"首战应至少发 2 个组合(1拒+1成)，实际 {len(calls)}"
        assert "response_format" in calls[0]
        assert "response_format" not in calls[-1]

        # 次战：命中缓存的 good 组合 → 仅 1 次请求
        before = len(calls)
        r2, c2, _ = llm_service.call_llm("http://x", "k", "m", "p2", 0.2, reasoning_effort="")
        assert (r2, c2) == (7, 7)
        assert len(calls) - before == 1, "次战应只发 1 个请求(good 组合)"


def test_combo_cache_with_reasoning_effort():
    calls = []
    with patch.object(llm_service, "OpenAI", _client_factory(calls)):
        # reasoning_effort 时组合更多：rf+effort / effort / rf / bare，前 3 个带 rf 被拒
        r, c, _ = llm_service.call_llm("http://y", "k", "m2", "p", 0.2, reasoning_effort="low")
        assert (r, c) == (7, 7)
        # 行为契约：至少 2 次（1拒+1成），最终成功组合不带 rf/effort
        assert len(calls) >= 2, f"首战带 effort 应至少发 2 个组合，实际 {len(calls)}"
        assert "response_format" not in calls[-1]
        assert "reasoning_effort" not in calls[-1]

        # 次战：命中 good(bare) → 1 次
        before = len(calls)
        llm_service.call_llm("http://y", "k", "m2", "p2", 0.2, reasoning_effort="low")
        assert len(calls) - before == 1, "次战应只发 1 个请求"


def test_ai_lock_ttl_exceeds_deadline():
    """Fix B 不变量：锁 TTL 必须大于单次 AI 落子最坏耗时，避免慢思考途中过期触发重复线程。"""
    ai_lock_ttl = pytest.importorskip("app.routers.game", reason="game 路由模块缺失").AI_LOCK_TTL
    assert ai_lock_ttl > llm_service.LLM_CALL_DEADLINE


# ============ 思考方言（thinking_profile）接入后的组合行为 ============

def test_openai_native_high_sends_dialect_shape_and_budget():
    """openai-native 高档：首个组合应带 enable_thinking:true + reasoning_effort + 档位预算 24576。"""
    calls = []
    with patch.object(llm_service, "OpenAI", _client_factory(calls)):
        # base_url/api_key 需与本文件其他用例错开：_clients 客户端缓存跨用例共享（只清 _combo_cache），
        # 撞键会拿到上一个用例的 fake 客户端 → 本用例的 calls 列表为空
        r, c, _ = llm_service.call_llm("http://eff", "k-eff", "m-eff", "p", 0.2,
                                       reasoning_effort="high", thinking_profile="openai-native")
        assert (r, c) == (7, 7)
        first = calls[0]
        assert first["enable_thinking"] is True
        assert first["reasoning_effort"] == "high"
        assert first["max_tokens"] == 24576  # 思考档预算下限生效（原 1024）


def test_nvidia_off_degrades_when_thinking_field_rejected():
    """nvidia 布尔方言 off：思考字段被端点拒绝（400）时逐级降级，最终不带思考字段成功。"""
    calls = []

    class _StrictCompletions(_FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(dict(kwargs))
            # 模拟严格网关：response_format / reasoning_effort / thinking 字段都会被拒
            if kwargs.get("response_format") or kwargs.get("reasoning_effort") or "thinking" in kwargs:
                _resp = SimpleNamespace(
                    status_code=400, headers={},
                    request=SimpleNamespace(method="POST", url="http://fake"),
                )
                raise BadRequestError("param rejected", response=_resp, body=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop",
                                         message=SimpleNamespace(content='{"row": 7, "col": 7, "reason": "ok"}'))]
            )

    class _StrictClient:
        def __init__(self, *a, **k):
            self.chat = SimpleNamespace(completions=_StrictCompletions(calls))

    with patch.object(llm_service, "OpenAI", lambda *a, **k: _StrictClient()):
        r, c, _ = llm_service.call_llm("http://nv", "k", "m-nv", "p", 0.2,
                                       reasoning_effort="off", thinking_profile="nvidia")
        assert (r, c) == (7, 7)
        # 首个组合按 nvidia 方言发布尔禁用字段；被拒后降级，最终成功组合不含任何思考字段
        assert calls[0].get("thinking") is False
        assert "thinking" not in calls[-1]
        assert len(calls) >= 2


def test_combo_cache_key_distinguishes_profiles():
    """不同 thinking_profile 的组合缓存互不串用：nvidia(off) 的 good 组合不能被 openai-native(off) 复用。"""
    calls = []

    class _ProfileStrictCompletions(_FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(dict(kwargs))
            if kwargs.get("response_format") or kwargs.get("reasoning_effort") or "thinking" in kwargs:
                _resp = SimpleNamespace(
                    status_code=400, headers={},
                    request=SimpleNamespace(method="POST", url="http://fake"),
                )
                raise BadRequestError("param rejected", response=_resp, body=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop",
                                         message=SimpleNamespace(content='{"row": 7, "col": 7, "reason": "ok"}'))]
            )

    class _ProfileStrictClient:
        def __init__(self, *a, **k):
            self.chat = SimpleNamespace(completions=_ProfileStrictCompletions(calls))

    with patch.object(llm_service, "OpenAI", lambda *a, **k: _ProfileStrictClient()):
        # 第一战：nvidia off → 探索后 bare 成功，记入 nvidia 的缓存条目
        llm_service.call_llm("http://p", "k", "m-prof", "p", 0.2,
                             reasoning_effort="off", thinking_profile="nvidia")
        # 第二战：同 base_url/model/档位，但方言是 openai-native（off 发 enable_thinking:false，
        # 该网关接受它）。若缓存 key 未含 profile，会直接命中 nvidia 的 good(bare) 只发 1 次；
        # 正确行为是独立探索：combo1(rf+enable_thinking) 被拒、combo2(enable_thinking) 成功 → 2 次
        before = len(calls)
        llm_service.call_llm("http://p", "k", "m-prof", "p2", 0.2,
                             reasoning_effort="off", thinking_profile="openai-native")
        assert len(calls) - before == 2, "openai-native(off) 应独立探索组合，而不是命中 nvidia 的缓存"
        # 且成功请求按 openai-native 方言发的是 enable_thinking（而非 nvidia 的 thinking）
        assert calls[-1].get("enable_thinking") is False
        assert "thinking" not in calls[-1]
