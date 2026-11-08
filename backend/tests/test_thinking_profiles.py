"""思考方言注册表（thinking_profiles）单元测试。

验证三件事：
1. resolve_thinking_spec 的档位×能力归一化矩阵（binary 满血 / none 全不发 / 未设置全不发）；
2. 7 种方言在 关闭 / 开启 / 未设置 下的精确请求体形状（对齐厂商适配指南 §3.1-3.7），
   特别是「未设置 → 什么都不发」的存量请求体逐字节不变保证；
3. build_thinking_params 的预算下限（思考开启抬预算，off/未设置不动）。
"""
import pytest

from app.services import thinking_profiles as tp


# ---------- resolve_thinking_spec：档位 × 能力 归一化矩阵 ----------

def test_spec_default_sends_nothing():
    for cap in ("graded", "binary", "none"):
        spec = tp.resolve_thinking_spec("", cap)
        assert spec["level"] is None and spec["disable_thinking"] is False and spec["effort"] is None


def test_spec_off_disables():
    spec = tp.resolve_thinking_spec("off", "graded")
    assert spec == {"level": "off", "disable_thinking": True, "effort": None}
    # binary/none 的 off：none 连关闭字段都不发（无思考能力），binary 正常发禁用字段
    assert tp.resolve_thinking_spec("off", "binary")["disable_thinking"] is True
    assert tp.resolve_thinking_spec("off", "none")["level"] is None


def test_spec_graded_levels_passthrough():
    for lv in ("low", "medium", "high"):
        spec = tp.resolve_thinking_spec(lv, "graded")
        assert spec == {"level": lv, "disable_thinking": False, "effort": lv}


def test_spec_binary_normalizes_to_high():
    for lv in ("low", "medium", "high"):
        spec = tp.resolve_thinking_spec(lv, "binary")
        assert spec == {"level": "high", "disable_thinking": False, "effort": "high"}


def test_spec_none_capability_suppresses_all():
    for lv in ("off", "low", "medium", "high"):
        spec = tp.resolve_thinking_spec(lv, "none")
        assert spec["level"] is None and spec["effort"] is None and spec["disable_thinking"] is False


def test_spec_invalid_inputs_fall_back_to_safe_defaults():
    spec = tp.resolve_thinking_spec("extreme", "turbo")
    assert spec["level"] is None  # 未知档位按未设置处理（不发字段）；未知能力按 graded


# ---------- 7 种方言的精确请求体形状 ----------

def _apply(profile, level, capability="graded"):
    body = {}
    occupied = tp.apply_thinking_profile(profile, body, tp.resolve_thinking_spec(level, capability))
    return body, occupied


def test_profile_openai_native():
    body, occ = _apply("openai-native", "off")
    assert body == {"enable_thinking": False} and occ == ["enable_thinking"]
    body, occ = _apply("openai-native", "high")
    assert body == {"enable_thinking": True, "reasoning_effort": "high"}
    assert occ == ["enable_thinking", "reasoning_effort"]


def test_profile_chat_template_nested_position():
    body, occ = _apply("chat-template", "off")
    assert body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert occ == ["chat_template_kwargs"]
    body, occ = _apply("chat-template", "medium")
    assert body == {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": "medium"}


def test_profile_thinking_flag_string_shape():
    body, _ = _apply("thinking-flag", "off")
    assert body == {"thinking": "disabled"}
    body, _ = _apply("thinking-flag", "low")
    assert body == {"thinking": "enabled", "reasoning_effort": "low"}


def test_profile_thinking_type_object_shape():
    # 与 thinking-flag 同名字段、不同形状：混元要 {"type": ...}，MiniMax 要字符串
    body, _ = _apply("thinking-type", "off")
    assert body == {"thinking": {"type": "disabled"}}
    body, _ = _apply("thinking-type", "high")
    assert body == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}


def test_profile_effort_only():
    body, _ = _apply("effort-only", "off")
    assert body == {"reasoning_effort": "none"}
    body, _ = _apply("effort-only", "medium")
    assert body == {"reasoning_effort": "medium"}


def test_profile_thinking_mode_boolean_no_effort():
    # 布尔（传字符串会被 Intern-S1 判 400）且没有强度档：不发 reasoning_effort
    body, occ = _apply("thinking-mode", "off")
    assert body == {"thinking_mode": False} and occ == ["thinking_mode"]
    body, occ = _apply("thinking-mode", "high")
    assert body == {"thinking_mode": True} and occ == ["thinking_mode"]


def test_profile_nvidia_boolean_no_effort():
    # 布尔（NVIDIA NIM 对字符串 thinking 直接 400）且没有强度档：不发 reasoning_effort
    body, occ = _apply("nvidia", "off")
    assert body == {"thinking": False} and occ == ["thinking"]
    body, occ = _apply("nvidia", "high")
    assert body == {"thinking": True} and occ == ["thinking"]


def test_profile_unset_writes_nothing_for_all_dialects():
    # 存量兼容保证：未设置档（level=''）时任何方言都不写任何思考字段
    for profile in tp.THINKING_PROFILE_KEYS:
        body, occ = _apply(profile, "")
        assert body == {}, f"{profile} 在未设置档不应发任何字段"
        assert occ == []


def test_unknown_profile_raises_instead_of_silent_fallback():
    # 静默回退会把开关放到网关不读的位置（配了关闭仍在思考且无任何错误信号），必须报错
    with pytest.raises(ValueError, match="unknown-dialect"):
        tp.apply_thinking_profile("unknown-dialect", {}, tp.resolve_thinking_spec("off"))


# ---------- build_thinking_params：预算下限 ----------

def test_build_params_unset_returns_empty_no_budget():
    assert tp.build_thinking_params("openai-native", "", "graded") == ({}, None)


def test_build_params_none_capability_returns_empty():
    assert tp.build_thinking_params("openai-native", "high", "none") == ({}, None)


def test_build_params_budget_by_level():
    kw, floor = tp.build_thinking_params("openai-native", "low", "graded")
    assert floor == 4096 and kw["reasoning_effort"] == "low"
    _, floor = tp.build_thinking_params("openai-native", "high", "graded")
    assert floor == 24576


def test_build_params_off_does_not_touch_budget():
    kw, floor = tp.build_thinking_params("nvidia", "off", "binary")
    assert floor is None and kw == {"thinking": False}


def test_build_params_binary_high_budget():
    kw, floor = tp.build_thinking_params("thinking-mode", "medium", "binary")
    assert floor == 24576 and kw == {"thinking_mode": True}  # binary 满血 + high 预算


# ---------- API 层：方言/能力白名单校验（configs）与档位白名单（players） ----------

def test_config_create_and_update_thinking_fields(client):
    # 创建时带上方言/能力
    resp = client.post("/api/configs/", json={"name": "nv", "base_url": "https://api.example.com/v1",
                                              "api_key": "", "thinking_profile": "nvidia",
                                              "thinking_capability": "binary"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thinking_profile"] == "nvidia" and body["thinking_capability"] == "binary"
    cid = body["id"]

    # 卡片内嵌下拉的保存路径：PUT 部分更新
    resp = client.put(f"/api/configs/{cid}", json={"thinking_capability": "none"})
    assert resp.status_code == 200 and resp.json()["thinking_capability"] == "none"
    resp = client.get("/api/configs/")
    cfg = next(c for c in resp.json() if c["id"] == cid)
    assert cfg["thinking_profile"] == "nvidia"  # 未传的字段不被覆盖

    # 非法方言/能力 → 400 并列出合法值
    resp = client.put(f"/api/configs/{cid}", json={"thinking_profile": "bogus"})
    assert resp.status_code == 400 and "nvidia" in resp.json()["detail"]
    resp = client.put(f"/api/configs/{cid}", json={"thinking_capability": "bogus"})
    assert resp.status_code == 400


def test_config_create_rejects_unknown_profile(client):
    resp = client.post("/api/configs/", json={"name": "x", "base_url": "https://api.example.com/v1",
                                              "api_key": "", "thinking_profile": "unknown-dialect"})
    assert resp.status_code == 400


def test_player_effort_whitelist_includes_off(client):
    resp = client.post("/api/configs/", json={"name": "c", "base_url": "https://api.example.com/v1", "api_key": ""})
    cid = resp.json()["id"]
    # off（显式关闭）是合法档位
    resp = client.post("/api/players/", json={"name": "p1", "model_config_id": cid, "model_id": "m",
                                              "reasoning_effort": "off"})
    assert resp.status_code == 200, resp.text
    pid = resp.json()["id"]
    assert resp.json()["reasoning_effort"] == "off"
    # PUT 部分更新同样接受 off，非法值被拒
    assert client.put(f"/api/players/{pid}", json={"reasoning_effort": ""}).status_code == 200
    resp = client.put(f"/api/players/{pid}", json={"reasoning_effort": "extreme"})
    assert resp.status_code == 400
    resp = client.post("/api/players/", json={"name": "p2", "model_config_id": cid, "model_id": "m",
                                              "reasoning_effort": "extreme"})
    assert resp.status_code == 400
