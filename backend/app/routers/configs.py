from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..services import llm_service
from ..services import get_model_config, create_model_config, list_model_configs, delete_model_config
from ..services.config_helper import resolve_api_key
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
def create_config(name: str = Body(...), base_url: str = Body(...), api_key: str = Body(""), db: Session = Depends(get_db)):
    """创建模型配置。api_key 可留空：留空时运行期从环境变量 LLM_API_KEY_{id} 读取。

    注意：传入的 api_key 会明文存库（兼容旧流程）；更安全的做法是留空 api_key，
    改在 backend/.env 设置 LLM_API_KEY_{id}。返回响应不包含 api_key 字段。
    """
    if not validate_url(base_url):
        raise HTTPException(status_code=400, detail="base_url 格式无效，必须是有效的 HTTP/HTTPS URL")
    if api_key:
        logger.warning("create_config: 收到明文 api_key（配置 %s）。建议留空改存环境变量。", name)
    config = create_model_config(db, name, base_url, api_key)
    return {"id": config.id, "name": config.name, "base_url": config.base_url, "models": []}


@router.get("/")
def list_configs(db: Session = Depends(get_db)):
    configs = list_model_configs(db)
    return [{"id": c.id, "name": c.name, "base_url": c.base_url, "models": json.loads(c.models)} for c in configs]


@router.post("/{config_id}/test")
def test_config(config_id: int, db: Session = Depends(get_db)):
    config = get_model_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    try:
        api_key = resolve_api_key(config)
        resp = httpx.get(f"{config.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
        if resp.status_code == 200:
            return {"status": "ok", "detail": "连通成功"}
        else:
            return {"status": "error", "detail": f"HTTP {resp.status_code}"}
    except Exception as e:
        # 不把原始异常 str(e) 返回给客户端（可能含 URL/header/token）
        return {"status": "error", "detail": _safe_error(str(e))}


@router.post("/{config_id}/models")
def fetch_models(config_id: int, db: Session = Depends(get_db)):
    config = get_model_config(db, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    try:
        api_key = resolve_api_key(config)
        resp = httpx.get(f"{config.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id", "") for m in data.get("data", [])]
            config.models = json.dumps(models)
            db.commit()
            return {"models": models}
        else:
            raise HTTPException(status_code=resp.status_code, detail="拉取失败")
    except HTTPException:
        raise
    except Exception as e:
        # 不把原始异常返回（可能泄漏 URL/token）；仅记录到服务端日志
        logger.warning("fetch_models 失败 config_id=%s: %s", config_id, e)
        raise HTTPException(status_code=500, detail="拉取模型列表失败，请检查配置")


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
