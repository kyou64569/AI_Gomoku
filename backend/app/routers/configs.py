from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..services import llm_service
from ..services import get_model_config, create_model_config, list_model_configs, delete_model_config
from ..services.config_helper import resolve_api_key
from ..models import ModelConfig, AIPlayer
import httpx
import json
from urllib.parse import urlparse

router = APIRouter(prefix="/api/configs", tags=["configs"])


def validate_url(url: str) -> bool:
    """验证 URL 格式是否正确"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and result.scheme in ('http', 'https')
    except Exception:
        return False


@router.post("/")
def create_config(name: str = Body(...), base_url: str = Body(...), api_key: str = Body(""), db: Session = Depends(get_db)):
    """创建模型配置。api_key 可留空：留空时运行期从环境变量 LLM_API_KEY_{id} 读取。"""
    if not validate_url(base_url):
        raise HTTPException(status_code=400, detail="base_url 格式无效，必须是有效的 HTTP/HTTPS URL")
    return create_model_config(db, name, base_url, api_key)


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
        return {"status": "error", "detail": str(e)}


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    delete_model_config(db, config_id)
    return {"detail": "已删除"}
