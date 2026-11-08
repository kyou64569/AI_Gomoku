from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..services import llm_service
from ..services import get_model_config, create_model_config, update_model_config, list_model_configs, delete_model_config
from ..services.config_helper import resolve_api_key
from ..services.thinking_profiles import (THINKING_PROFILE_KEYS, THINKING_PROFILE_DEFAULT,
                                          THINKING_CAPABILITIES, THINKING_CAPABILITY_DEFAULT)
from ..services.native_protocols import ANTHROPIC_VERSION, LLM_PROVIDERS, trim_version_suffix
from ..models import ModelConfig, AIPlayer
import httpx
import ipaddress
import logging
import json
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/configs", tags=["configs"])

# 禁止访问的内网/保留地址段（SSRF 防护）：云元数据、内网探测、回环等一律拒绝
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # 回环
    ipaddress.ip_network("10.0.0.0/8"),        # 私网
    ipaddress.ip_network("172.16.0.0/12"),     # 私网
    ipaddress.ip_network("192.168.0.0/16"),    # 私网
    ipaddress.ip_network("169.254.0.0/16"),    # 链路本地（含云元数据 169.254.169.254）
    ipaddress.ip_network("0.0.0.0/8"),         # 保留
    ipaddress.ip_network("100.64.0.0/10"),     # CGNAT
    ipaddress.ip_network("::1/128"),           # IPv6 回环
    ipaddress.ip_network("fc00::/7"),          # IPv6 私网
    ipaddress.ip_network("fe80::/10"),         # IPv6 链路本地
]


def validate_url(url: str) -> bool:
    """验证 URL 格式是否正确，并拦截内网/保留地址（SSRF 防护）。

    同时解析 host 的 IP：若直接是内网 IP 或解析结果落在内网段（防 DNS rebinding），拒绝。
    """
    try:
        result = urlparse(url)
        if not all([result.scheme, result.netloc]) or result.scheme not in ('http', 'https'):
            return False
        hostname = result.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "::1", "[::1]", "127.0.0.1", "0.0.0.0"):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            # 域名：解析后逐个 IP 校验，任一命中内网段即拒绝（防 DNS rebinding）
            try:
                infos = socket.getaddrinfo(hostname, None)
            except OSError:
                # 解析失败（离线/测试环境/域名不存在）：请求会自然失败，
                # 不构成 SSRF 风险，放行让上层处理（避免误伤合法配置）
                return True
            for info in infos:
                try:
                    ip = ipaddress.ip_address(info[4][0])
                except ValueError:
                    continue
                if any(ip in net for net in _PRIVATE_NETWORKS):
                    return False
            return True
        return not any(ip in net for net in _PRIVATE_NETWORKS)
    except Exception:
        return False


def _safe_error(detail: str) -> str:
    """把异常详情转为不泄漏敏感信息的通用错误消息（防止 API Key / URL 被带回给客户端）。"""
    logger.warning("configs 请求失败: %s", detail)
    return "连接失败，请检查配置（base_url / api_key / 网络）"


@router.post("/")
def create_config(name: str = Body(...), base_url: str = Body(...), api_key: str = Body(""),
                  thinking_profile: str = Body(THINKING_PROFILE_DEFAULT),
                  thinking_capability: str = Body(THINKING_CAPABILITY_DEFAULT),
                  provider: str = Body("openai"),
                  db: Session = Depends(get_db)):
    """创建模型配置。api_key 可留空：留空时运行期从环境变量 LLM_API_KEY_{id} 读取。

    provider：协议类型（openai / gemini / anthropic，决定调用通道与思考参数形状）；
    thinking_profile：思考参数厂商方言（仅 openai 协议使用）；
    thinking_capability：模型思考能力声明（graded / binary / none）。
    注意：传入的 api_key 会明文存库（兼容旧流程）；更安全的做法是留空 api_key，
    改在 backend/.env 设置 LLM_API_KEY_{id}。返回响应不包含 api_key 字段。
    """
    if not validate_url(base_url):
        raise HTTPException(status_code=400, detail="base_url 格式无效，必须是有效的 HTTP/HTTPS URL")
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="配置名称不能为空")
    if provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider 必须是 {' | '.join(LLM_PROVIDERS)} 之一")
    if thinking_profile not in THINKING_PROFILE_KEYS:
        raise HTTPException(status_code=400,
                            detail=f"thinking_profile 必须是 {' | '.join(THINKING_PROFILE_KEYS)} 之一")
    if thinking_capability not in THINKING_CAPABILITIES:
        raise HTTPException(status_code=400,
                            detail=f"thinking_capability 必须是 {' | '.join(THINKING_CAPABILITIES)} 之一")
    if api_key:
        logger.warning("create_config: 收到明文 api_key（配置 %s）。建议留空改存环境变量。", name)
    config = create_model_config(db, name, base_url, api_key, thinking_profile, thinking_capability, provider)
    return {"id": config.id, "name": config.name, "base_url": config.base_url,
            "provider": config.provider,
            "thinking_profile": config.thinking_profile,
            "thinking_capability": config.thinking_capability, "models": []}


@router.get("/")
def list_configs(db: Session = Depends(get_db)):
    configs = list_model_configs(db)
    return [{"id": c.id, "name": c.name, "base_url": c.base_url, "models": json.loads(c.models),
             "provider": c.provider, "thinking_profile": c.thinking_profile,
             "thinking_capability": c.thinking_capability}
            for c in configs]


@router.put("/{config_id}")
def update_config(config_id: int, name: str = Body(None), base_url: str = Body(None),
                  api_key: str = Body(None), thinking_profile: str = Body(None),
                  thinking_capability: str = Body(None), provider: str = Body(None),
                  db: Session = Depends(get_db)):
    """部分更新模型配置（None = 不改）。

    配置被 AI 玩家引用时无法删除重建（DELETE 返回 409），方言/能力/协议选错在这里修正。
    修改 provider 会清空已拉取的模型列表（模型列表是端点特定的）。
    """
    config = get_model_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    if name is not None and not name.strip():
        raise HTTPException(status_code=400, detail="配置名称不能为空")
    if provider is not None and provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider 必须是 {' | '.join(LLM_PROVIDERS)} 之一")
    if thinking_profile is not None and thinking_profile not in THINKING_PROFILE_KEYS:
        raise HTTPException(status_code=400,
                            detail=f"thinking_profile 必须是 {' | '.join(THINKING_PROFILE_KEYS)} 之一")
    if thinking_capability is not None and thinking_capability not in THINKING_CAPABILITIES:
        raise HTTPException(status_code=400,
                            detail=f"thinking_capability 必须是 {' | '.join(THINKING_CAPABILITIES)} 之一")
    old_base_url = config.base_url
    models_cleared = bool(provider is not None and provider != config.provider)
    url_or_key_changed = ((base_url is not None and base_url != old_base_url)
                          or (api_key is not None and api_key != config.api_key))
    if base_url is not None and base_url != old_base_url and not validate_url(base_url):
        raise HTTPException(status_code=400, detail="base_url 格式无效，必须是有效的 HTTP/HTTPS URL")
    update_model_config(db, config, name=name, base_url=base_url, api_key=api_key,
                        thinking_profile=thinking_profile, thinking_capability=thinking_capability,
                        provider=provider, models="[]" if models_cleared else None)
    if url_or_key_changed:
        # base_url / api_key 变化后，旧客户端缓存（含连接池与旧密钥）不再适用
        llm_service.clear_client_cache(base_url=old_base_url)
    return {"id": config.id, "name": config.name, "base_url": config.base_url,
            "provider": config.provider, "thinking_profile": config.thinking_profile,
            "thinking_capability": config.thinking_capability, "models_cleared": models_cleared}


class _UpstreamError(Exception):
    """拉取/探测时的上游非 2xx：带 HTTP 状态与错误摘要（不含 URL/token）。"""

    def __init__(self, status: int, message: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def _extract_error_message(resp) -> str:
    """从上游错误响应提取 error.message（三种协议错误体同形），截 200 字。
    只取 JSON 字段而非原始 body，避免把 URL/token 等敏感内容带回客户端。"""
    try:
        err = (resp.json() or {}).get("error") or {}
        return str(err.get("message", ""))[:200] if isinstance(err, dict) else ""
    except Exception:
        return ""


def _parse_openai_models(data) -> list[str]:
    return [m.get("id", "") for m in (data.get("data") or [])
            if isinstance(m, dict) and m.get("id")]


def _parse_gemini_models(data) -> list[str]:
    """{"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": [...]}]}
    → 只剥 "models/" 前缀得到模型 ID（tunedModels/x 的前缀是 ID 的一部分，保留）；
    过滤只保留支持 generateContent 的（embedding 等无法用于落子/点评，不进列表）。"""
    out = set()
    for m in (data.get("models") or []):
        name = m.get("name") if isinstance(m, dict) else None
        if not isinstance(name, str) or "/" not in name:
            continue
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        out.add(name[len("models/"):] if name.lower().startswith("models/") else name)
    return sorted(out)


def _parse_anthropic_models(data) -> list[str]:
    return [m.get("id", "") for m in (data.get("data") or [])
            if isinstance(m, dict) and m.get("id")]


def _list_models_for(config) -> list[str]:
    """按 provider 分协议拉取模型列表。

    gemini:    GET {base}/v1beta/models（x-goog-api-key 头；Bearer 会被忽略 → 401）
    anthropic: GET {base}/v1/models（x-api-key + anthropic-version）
    openai:    GET {base}/models（Authorization: Bearer，事实标准）
    非 2xx 抛 _UpstreamError（带错误摘要）；网络异常原样抛出由调用方归类。
    """
    api_key = resolve_api_key(config)
    base = config.base_url.rstrip("/")
    if config.provider == "gemini":
        # 分页跟随：pageSize=1000 是单页上限，>1000 个模型时用 pageToken 翻页（封顶 5 页防御）
        url = f"{trim_version_suffix(base)}/v1beta/models"
        headers = {"x-goog-api-key": api_key} if api_key else {}
        raw, page_token = [], None
        for _ in range(5):
            params = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            resp = httpx.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code != 200:
                raise _UpstreamError(resp.status_code, _extract_error_message(resp))
            data = resp.json()
            raw.extend(data.get("models") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return _parse_gemini_models({"models": raw})
    if config.provider == "anthropic":
        url = f"{trim_version_suffix(base)}/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
        resp = httpx.get(url, headers=headers, params={"limit": 1000}, timeout=10)
        if resp.status_code != 200:
            raise _UpstreamError(resp.status_code, _extract_error_message(resp))
        return _parse_anthropic_models(resp.json())
    url = f"{base}/models"
    resp = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
    if resp.status_code != 200:
        raise _UpstreamError(resp.status_code, _extract_error_message(resp))
    return _parse_openai_models(resp.json())


@router.post("/{config_id}/test")
def test_config(config_id: int, db: Session = Depends(get_db)):
    """按协议探测连通性（与拉取模型同一条代码路径：测试通过即可拉取）。"""
    config = get_model_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    try:
        models = _list_models_for(config)
        return {"status": "ok", "detail": f"连通成功（{len(models)} 个模型）"}
    except _UpstreamError as e:
        return {"status": "error",
                "detail": f"上游返回 HTTP {e.status}" + (f"：{e.message}" if e.message else "")}
    except Exception as e:
        # 不把原始异常 str(e) 返回给客户端（可能含 URL/header/token）
        return {"status": "error", "detail": _safe_error(str(e))}


@router.post("/{config_id}/models")
def fetch_models(config_id: int, db: Session = Depends(get_db)):
    """拉取模型列表并入库（按 provider 分协议，见 _list_models_for）。"""
    config = get_model_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    try:
        models = _list_models_for(config)
    except _UpstreamError as e:
        detail = f"拉取失败：上游返回 HTTP {e.status}" + (f"：{e.message}" if e.message else "")
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        # 不把原始异常返回（可能泄漏 URL/token）；仅记录到服务端日志
        logger.warning("fetch_models 失败 config_id=%s: %s", config_id, e)
        raise HTTPException(status_code=500, detail="拉取模型列表失败，请检查 base_url / 网络")
    config.models = json.dumps(models)
    db.commit()
    return {"models": models}


@router.delete("/{config_id}")
def delete_config(config_id: int, db: Session = Depends(get_db)):
    config = get_model_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    # 防止产生孤儿 AIPlayer：删除被 AI 玩家引用的配置会让相关对局永久卡死
    # （即便启用 PRAGMA foreign_keys=ON 做级联删除，也会导致关联玩家/对局失效）。
    # 因此优先显式拦截，给出可执行的错误提示。
    ref = db.query(AIPlayer).filter(AIPlayer.model_config_id == config_id).first()
    if ref:
        raise HTTPException(
            status_code=409,
            detail="该配置正被 AI 玩家引用，无法删除。请先删除使用该配置的 AI 玩家，或更换其配置后再试。",
        )
    base_url = config.base_url
    delete_model_config(db, config_id)
    llm_service.clear_client_cache(base_url=base_url)
    return {"detail": "已删除"}
