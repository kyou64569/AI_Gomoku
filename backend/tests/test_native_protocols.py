"""Gemini / Anthropic 原生协议分支（native_protocols + llm_service 分发）测试。

覆盖：
1. 请求构建：URL / 认证头 / 请求体形状；未设置省略思考字段；off→minimal；binary 满血；
   Anthropic 的 budget_tokens 约束（≥1024 且 < max_tokens）与 temperature 切换。
2. 响应解析：thought 块分离、MAX_TOKENS/max_tokens 截断、thinking 块回退。
3. call_llm 分发与降级：思考字段被 4xx 拒绝 → 自动去掉思考重试成功；组合缓存含 provider。
4. chat_json 原生分支（复盘点评路径）。
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.llm_service as llm_service
from app.services import native_protocols as np


@pytest.fixture(autouse=True)
def _clear_cache():
    llm_service._combo_cache.clear()
    yield
    llm_service._combo_cache.clear()


# ---------- provider 归一 ----------

def test_normalize_provider():
    assert np.normalize_provider("gemini") == "gemini"
    assert np.normalize_provider("anthropic") == "anthropic"
    assert np.normalize_provider("openai") == "openai"
    assert np.normalize_provider("bogus") == "openai"
    assert np.normalize_provider(None) == "openai"


# ---------- Gemini 请求构建 ----------

def _gemini(level, capability="graded", **kw):
    return np.build_gemini_request("https://gen.example.com", "gk", "gem-3",
                                   "系统提示", "用户提示", level, capability, **kw)


def test_gemini_unset_omits_thinking_config():
    url, headers, body, floor, active = _gemini("")
    assert url == "https://gen.example.com/v1beta/models/gem-3:generateContent"
    assert headers["x-goog-api-key"] == "gk"
    assert "thinkingConfig" not in body["generationConfig"]  # 未设置 → 跟随模型默认
    assert body["generationConfig"]["maxOutputTokens"] == 1024
    assert floor is None and active is False
    assert body["systemInstruction"] == {"parts": [{"text": "系统提示"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "用户提示"}]}]


def test_gemini_off_maps_to_minimal():
    _, _, body, floor, active = _gemini("off")
    tc = body["generationConfig"]["thinkingConfig"]
    assert tc == {"thinkingLevel": "minimal", "includeThoughts": True}  # includeThoughts 必带
    assert body["generationConfig"]["maxOutputTokens"] == 1024 and floor == 1024 and active


def test_gemini_high_budget_and_binary_normalization():
    _, _, body, floor, _ = _gemini("high")
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"
    assert body["generationConfig"]["maxOutputTokens"] == 24576 and floor == 24576
    # binary：low 也满血（thinkingLevel high + high 预算）
    _, _, body, floor, _ = _gemini("low", "binary")
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"
    assert body["generationConfig"]["maxOutputTokens"] == 24576 and floor == 24576


def test_gemini_escalation_never_shrinks_budget():
    # 截断升级传 max_tokens=8192：有 floor 时取 max（24576），无 floor 时用 8192
    _, _, body, _, _ = _gemini("high", max_tokens=8192)
    assert body["generationConfig"]["maxOutputTokens"] == 24576
    _, _, body, _, _ = _gemini("", max_tokens=8192)
    assert body["generationConfig"]["maxOutputTokens"] == 8192


def test_gemini_include_thinking_false_forces_no_thinking():
    _, _, body, floor, active = _gemini("high", include_thinking=False)
    assert "thinkingConfig" not in body["generationConfig"] and floor is None and not active


# ---------- Anthropic 请求构建 ----------

def _anthropic(level, capability="graded", **kw):
    return np.build_anthropic_request("https://api.example.com", "ak", "cla-x",
                                      "系统提示", "用户提示", level, capability, **kw)


def test_anthropic_unset_and_off_send_no_thinking():
    for level in ("", "off"):
        url, headers, body, floor, active = _anthropic(level)
        assert url == "https://api.example.com/v1/messages"
        assert headers["x-api-key"] == "ak" and headers["anthropic-version"] == np.ANTHROPIC_VERSION
        assert "thinking" not in body  # 该方言没有禁用字段，不传即关闭
        assert body["temperature"] == 0.2 and body["max_tokens"] == 1024  # max_tokens 官方必填
        assert body["system"] == "系统提示"
        assert floor is None and active is False


def test_anthropic_thinking_budget_rules():
    budgets = {"low": 4096, "medium": 8192, "high": 24576}
    for level, budget in budgets.items():
        _, _, body, floor, active = _anthropic(level)
        assert body["thinking"] == {"type": "enabled", "budget_tokens": budget}
        assert body["max_tokens"] == budget + np.ANTHROPIC_ANSWER_SPACE  # budget + 答题空间
        assert 1024 <= body["thinking"]["budget_tokens"] < body["max_tokens"]  # 官方约束
        assert body["temperature"] == 1  # thinking 开启时必须 1（0.2 会被 400）
        assert floor == body["max_tokens"] and active is True


def test_anthropic_escalation_and_thinking_toggle():
    _, _, body, _, _ = _anthropic("high", max_tokens=99_999)
    assert body["max_tokens"] == 99_999 and body["thinking"]["budget_tokens"] == 24576
    _, _, body, floor, active = _anthropic("high", include_thinking=False)
    assert "thinking" not in body and body["temperature"] == 0.2 and not active


# ---------- 响应解析 ----------

def test_parse_gemini_response():
    data = {"candidates": [{"content": {"parts": [
        {"text": "思考过程", "thought": True},
        {"text": '{"row": 1, "col": 2}'},
    ]}, "finishReason": "STOP"}]}
    text, truncated = np.parse_gemini_response(data)
    assert text == '{"row": 1, "col": 2}' and truncated is False  # thought 块与答案分离
    data["candidates"][0]["finishReason"] = "MAX_TOKENS"
    _, truncated = np.parse_gemini_response(data)
    assert truncated is True
    # 只有 thought 块（截断场景）→ 回退拼接思考文本
    only_thought = {"candidates": [{"content": {"parts": [{"text": "思考", "thought": True}]},
                                    "finishReason": "MAX_TOKENS"}]}
    text, _ = np.parse_gemini_response(only_thought)
    assert text == "思考"
    # text 缺失/为 None 的 part 不参与拼接，也不让 thought 标志错位（防 zip 对齐坑）
    mixed = {"candidates": [{"content": {"parts": [
        {"text": "A", "thought": True},
        {"text": None},
        {"text": "B", "thought": True},
        {"text": "C"},
    ]}}]}
    assert np.parse_gemini_response(mixed)[0] == "C"
    assert np.parse_gemini_response({}) == ("", False)


def test_parse_anthropic_response():
    data = {"content": [{"type": "thinking", "thinking": "思考"},
                        {"type": "text", "text": '{"row": 3}'}], "stop_reason": "end_turn"}
    text, truncated = np.parse_anthropic_response(data)
    assert text == '{"row": 3}' and truncated is False
    assert np.parse_anthropic_response({"content": [{"type": "text", "text": "x"}],
                                        "stop_reason": "max_tokens"})[1] is True
    # 只有 thinking 块 → 回退
    text, _ = np.parse_anthropic_response({"content": [{"type": "thinking", "thinking": "仅思考"}]})
    assert text == "仅思考"


# ---------- call_llm 原生分发与降级 ----------

class _FakeHTTP:
    def __init__(self, responder):
        self.calls = []
        self._responder = responder

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        return self._responder(len(self.calls), url, headers, json)


def _resp(status=200, data=None):
    data = data if data is not None else {}
    return SimpleNamespace(status_code=status, text=json.dumps(data), json=lambda: data)


_GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": '{"row": 7, "col": 7, "reason": "ok"}'}]},
                              "finishReason": "STOP"}]}


def test_native_move_degrades_when_thinking_rejected():
    """Gemini 高档：thinkingConfig 被 4xx 拒 → 自动去思考重试成功，并记入缓存。"""
    http = _FakeHTTP(lambda n, url, headers, body: (
        _resp(400, {"error": {"message": "thinkingLevel unsupported"}})
        if body.get("generationConfig", {}).get("thinkingConfig") else _resp(200, _GEMINI_OK)))
    with patch.object(llm_service, "_get_http_client", lambda: http):
        r, c, reason = llm_service.call_llm("http://gnv", "k", "m-nv", "p", 0.2,
                                            reasoning_effort="high", provider="gemini")
    assert (r, c) == (7, 7)
    assert "thinkingConfig" in http.calls[0]["body"]["generationConfig"]  # 首个组合带思考
    assert "thinkingConfig" not in http.calls[-1]["body"]["generationConfig"]  # 降级后不带
    assert len(http.calls) >= 2
    assert any(k[-1] == "gemini" for k in llm_service._combo_cache)  # 缓存 key 含 provider
    # 次战命中 good 组合 → 仅 1 次请求，且不带思考
    before = len(http.calls)
    with patch.object(llm_service, "_get_http_client", lambda: http):
        llm_service.call_llm("http://gnv", "k", "m-nv", "p2", 0.2,
                             reasoning_effort="high", provider="gemini")
    assert len(http.calls) - before == 1
    assert "thinkingConfig" not in http.calls[-1]["body"]["generationConfig"]


def test_native_unset_sends_single_stable_combo():
    """Gemini 未设置：单组合（无思考字段），失败也不降级试别的思考形状。"""
    http = _FakeHTTP(lambda n, url, headers, body: _resp(200, _GEMINI_OK))
    with patch.object(llm_service, "_get_http_client", lambda: http):
        r, c, _ = llm_service.call_llm("http://gu", "k", "m-gu", "p", 0.2,
                                       reasoning_effort="", provider="gemini")
    assert (r, c) == (7, 7)
    assert len(http.calls) == 1
    assert "thinkingConfig" not in http.calls[0]["body"]["generationConfig"]


def test_native_5xx_and_network_fail_fast():
    http = _FakeHTTP(lambda n, url, headers, body: _resp(500, {"error": "boom"}))
    with patch.object(llm_service, "_get_http_client", lambda: http):
        r, c, err = llm_service.call_llm("http://g5", "k", "m-5", "p", 0.2,
                                         reasoning_effort="high", provider="gemini")
    assert r is None and "500" in err


def test_anthropic_move_end_to_end():
    """Anthropic 高档：thinking 块 + temperature 1 + budget；文本块解析出落子。"""
    calls = []

    def responder(n, url, headers, body):
        calls.append(body)
        assert body["temperature"] == 1 and body["max_tokens"] > body["thinking"]["budget_tokens"]
        return _resp(200, {"content": [{"type": "text", "text": '{"row": 5, "col": 6}'}],
                           "stop_reason": "end_turn"})

    http = _FakeHTTP(responder)
    with patch.object(llm_service, "_get_http_client", lambda: http):
        r, c, _ = llm_service.call_llm("http://an", "k", "m-an", "p", 0.2,
                                       reasoning_effort="high", provider="anthropic")
    assert (r, c) == (5, 6)
    assert calls[0]["thinking"] == {"type": "enabled", "budget_tokens": 24576}
    assert calls[0]["max_tokens"] == 24576 + np.ANTHROPIC_ANSWER_SPACE


def test_chat_json_native_anthropic():
    """复盘点评走原生分支：system 透传、思考块降级、JSON 解析。"""
    http = _FakeHTTP(lambda n, url, headers, body: (
        _resp(200, {"content": [{"type": "text", "text": '{"summary": "好棋"}'}],
                    "stop_reason": "end_turn"})))
    with patch.object(llm_service, "_get_http_client", lambda: http):
        data = llm_service.chat_json("http://ac", "k", "m-ac", "点评系统", "棋谱",
                                     reasoning_effort="medium", provider="anthropic")
    assert data == {"summary": "好棋"}
    first = http.calls[0]["body"]
    assert first["system"] == "点评系统" and first["temperature"] == 1  # medium → thinking on
    assert first["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert first["max_tokens"] == 8192 + np.ANTHROPIC_ANSWER_SPACE


def test_chat_json_native_thinking_rejected_degrades():
    """Anthropic 思考被 4xx 拒 → 去思考重试（temperature 回 0.2）成功。"""
    http = _FakeHTTP(lambda n, url, headers, body: (
        _resp(400, {"error": "thinking unsupported"})
        if "thinking" in body else
        _resp(200, {"content": [{"type": "text", "text": '{"summary": "ok"}'}], "stop_reason": "end_turn"})))
    with patch.object(llm_service, "_get_http_client", lambda: http):
        data = llm_service.chat_json("http://ad", "k", "m-ad", "s", "u",
                                     reasoning_effort="high", provider="anthropic")
    assert data == {"summary": "ok"}
    assert "thinking" in http.calls[0]["body"] and "thinking" not in http.calls[-1]["body"]
    assert http.calls[-1]["body"]["temperature"] == 0.2


def test_chat_json_native_non_json_degrades_not_escapes():
    """200 + HTML 错误页（resp.json() 抛异常）→ 降级到无思考组合，最终返回 _error
    而不是异常逃逸（review_game 的契约：失败拿 {"_error": ...}）。"""
    http = _FakeHTTP(lambda n, url, headers, body: SimpleNamespace(
        status_code=200, text="<html>gateway error</html>",
        json=lambda: (_ for _ in ()).throw(ValueError("not json"))))
    with patch.object(llm_service, "_get_http_client", lambda: http):
        data = llm_service.chat_json("http://an-html", "k", "m-html", "s", "u",
                                     reasoning_effort="high", provider="anthropic")
    assert "_error" in data and "非 JSON" in data["_error"]
    assert len(http.calls) == 2  # 带思考 + 无思考两个组合都试过


def test_native_truncation_retry_escalates_budget():
    """高档思考截断（MAX_TOKENS）→ attempt 1 真正抬预算（floor×2），不是逐字节重发。"""
    calls = []

    def responder(n, url, headers, body):
        calls.append(body)
        if n == 1:
            return _resp(200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]})
        return _resp(200, _GEMINI_OK)

    http = _FakeHTTP(responder)
    with patch.object(llm_service, "_get_http_client", lambda: http):
        r, c, _ = llm_service.call_llm("http://gtr", "k", "m-gtr", "p", 0.2,
                                       reasoning_effort="high", provider="gemini")
    assert (r, c) == (7, 7)
    assert calls[0]["generationConfig"]["maxOutputTokens"] == 24576   # 首战 = floor
    assert calls[1]["generationConfig"]["maxOutputTokens"] == 49152   # 重试 = floor×2


def test_fetch_models_gemini_pagination(client, monkeypatch):
    """gemini 拉取跟随 nextPageToken 翻页合并结果。"""
    resp = client.post("/api/configs/", json={"name": "gp", "base_url": "https://gen.example.com",
                                              "api_key": "K", "provider": "gemini"})
    cid = resp.json()["id"]
    pages = [
        {"models": [{"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]}],
         "nextPageToken": "t1"},
        {"models": [{"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]}]},
    ]
    seen_params = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen_params.append(params)
        return SimpleNamespace(status_code=200, json=lambda: pages[len(seen_params) - 1])

    monkeypatch.setattr("app.routers.configs.httpx.get", fake_get)
    resp = client.post(f"/api/configs/{cid}/models")
    assert resp.json()["models"] == ["gemini-a", "gemini-b"]
    assert seen_params[1]["pageToken"] == "t1"


def test_config_name_required(client):
    """创建/更新时名称不允许空字符串。"""
    assert client.post("/api/configs/", json={"name": "  ", "base_url": "https://api.example.com/v1",
                                              "api_key": ""}).status_code == 400
    resp = client.post("/api/configs/", json={"name": "ok", "base_url": "https://api.example.com/v1",
                                              "api_key": ""})
    cid = resp.json()["id"]
    assert client.put(f"/api/configs/{cid}", json={"name": ""}).status_code == 400


# ---------- 模型列表拉取（configs.fetch_models 按协议分发） ----------

from app.routers import configs as configs_router  # noqa: E402


def test_trim_version_suffix():
    f = np.trim_version_suffix
    assert f("https://gen.example.com/v1beta/") == "https://gen.example.com"
    assert f("https://gen.example.com/v1beta") == "https://gen.example.com"
    assert f("https://api.example.com/v1") == "https://api.example.com"
    assert f("https://gen.example.com") == "https://gen.example.com"  # 裸域名不动


def test_request_urls_tolerate_versioned_base():
    """调用端 URL 与拉取模型共用版本段容错：用户带 /v1beta、/v1 也不双拼（否则调用 404）。"""
    for base in ("https://gen.example.com", "https://gen.example.com/v1beta", "https://gen.example.com/v1beta/"):
        url, _, _, _, _ = np.build_gemini_request(base, "K", "gemini-2.5-flash", "s", "u", "high")
        assert url == "https://gen.example.com/v1beta/models/gemini-2.5-flash:generateContent", url
    for base in ("https://api.example.com", "https://api.example.com/v1"):
        url, _, _, _, _ = np.build_anthropic_request(base, "K", "cla-x", "s", "u", "")
        assert url == "https://api.example.com/v1/messages", url


def test_parse_gemini_models_filters_non_generate():
    data = {"models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "tunedModels/x", "supportedGenerationMethods": ["generateContent"]},
        {"name": "bad-entry"},
    ]}
    assert configs_router._parse_gemini_models(data) == ["gemini-2.5-flash", "tunedModels/x"]


def test_fetch_models_gemini_provider(client, monkeypatch):
    """gemini 配置拉取：x-goog-api-key 头 + /v1beta/models + 剥版本段不双拼；embedding 被过滤。"""
    resp = client.post("/api/configs/", json={"name": "g", "base_url": "https://gen.example.com/v1beta",
                                              "api_key": "K", "provider": "gemini"})
    cid = resp.json()["id"]
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.update(url=url, headers=headers)
        return SimpleNamespace(status_code=200, json=lambda: {"models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]})

    monkeypatch.setattr("app.routers.configs.httpx.get", fake_get)
    resp = client.post(f"/api/configs/{cid}/models")
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"] == ["gemini-2.5-flash"]  # embedding 被过滤
    assert seen["url"] == "https://gen.example.com/v1beta/models"  # /v1beta 不双拼
    assert seen["headers"]["x-goog-api-key"] == "K"  # gemini 用专用头而非 Bearer


def test_fetch_models_error_detail_surfaced(client, monkeypatch):
    """上游 4xx：502 透传状态与 error.message 摘要（不再是含混的「拉取失败」）。"""
    resp = client.post("/api/configs/", json={"name": "a", "base_url": "https://api.example.com",
                                              "api_key": "K", "provider": "anthropic"})
    cid = resp.json()["id"]

    def fake_get(url, headers=None, params=None, timeout=None):
        return SimpleNamespace(status_code=401, json=lambda: {"error": {"message": "invalid x-api-key"}})

    monkeypatch.setattr("app.routers.configs.httpx.get", fake_get)
    resp = client.post(f"/api/configs/{cid}/models")
    assert resp.status_code == 502
    assert "HTTP 401" in resp.json()["detail"] and "invalid x-api-key" in resp.json()["detail"]


def test_fetch_models_openai_unchanged_shape(client, monkeypatch):
    """openai 协议保持原行为：Bearer 头 + data[].id。"""
    resp = client.post("/api/configs/", json={"name": "o", "base_url": "https://api.example.com/v1",
                                              "api_key": "K", "provider": "openai"})
    cid = resp.json()["id"]
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.update(url=url, headers=headers)
        return SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "m1"}, {"id": "m2"}]})

    monkeypatch.setattr("app.routers.configs.httpx.get", fake_get)
    resp = client.post(f"/api/configs/{cid}/models")
    assert resp.json()["models"] == ["m1", "m2"]
    assert seen["url"] == "https://api.example.com/v1/models"  # openai 不剥版本段
    assert seen["headers"]["Authorization"] == "Bearer K"
