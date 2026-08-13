from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List
from ..database import get_db
from ..services import get_ai_player, create_ai_player, list_ai_players, delete_ai_player, get_model_config
from ..services.config_helper import resolve_api_key
from ..services.llm_service import call_llm, build_prompt, TEMPERATURE
from ..services.game_logic import create_board
from ..models import AIPlayer

router = APIRouter(prefix="/api/players", tags=["players"])


@router.post("/")
def create_player(name: str = Body(...), model_config_id: int = Body(...), model_id: str = Body(...),
                  temperature: int = Body(70), reasoning_effort: str = Body(""), db: Session = Depends(get_db)):
    # Validate temperature: OpenAI expects 0.0-2.0, but we store as integer (0-200) for UI convenience
    # Convert to float range 0.0-2.0 by dividing by 100
    if not (0 <= temperature <= 200):
        raise HTTPException(status_code=400, detail="temperature 必须在 0-200 范围内（对应 OpenAI 的 0.0-2.0）")
    return create_ai_player(db, name, model_config_id, model_id, temperature, reasoning_effort)


@router.get("/")
def list_players(db: Session = Depends(get_db)):
    players = list_ai_players(db)
    return [{"id": p.id, "name": p.name, "model_config_id": p.model_config_id, "model_id": p.model_id,
             "temperature": p.temperature, "reasoning_effort": p.reasoning_effort} for p in players]


@router.post("/test_llm")
def test_llm(model_config_id: int = Body(...), model_id: str = Body(...),
             reasoning_effort: str = Body(""), db: Session = Depends(get_db)):
    """测试"模型配置 + 模型 + 指定思考等级"能否真实调用 LLM 返回合法落子（无需玩家存在）。"""
    config = get_model_config(db, model_config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    api_key = resolve_api_key(config)
    try:
        prompt = build_prompt(create_board(), 1, [], "AI")
        row, col, reason = call_llm(config.base_url, api_key, model_id, prompt,
                                    TEMPERATURE, reasoning_effort=reasoning_effort)
        if row is not None:
            return {"status": "ok", "detail": f"连通成功，模型建议落子 ({row},{col})"}
        return {"status": "error", "detail": f"调用失败：{reason}"}
    except Exception as e:
        return {"status": "error", "detail": f"调用异常：{type(e).__name__}: {e}"}


@router.put("/{player_id}")
def update_player(player_id: int, name: str = Body(None), model_config_id: int = Body(None),
                  model_id: str = Body(None), temperature: int = Body(None),
                  reasoning_effort: str = Body(None), db: Session = Depends(get_db)):
    player = get_ai_player(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="玩家不存在")
    if name is not None:
        player.name = name
    if model_config_id is not None:
        player.model_config_id = model_config_id
    if model_id is not None:
        player.model_id = model_id
    if temperature is not None:
        # Validate temperature: OpenAI expects 0.0-2.0, but we store as integer (0-200) for UI convenience
        if not (0 <= temperature <= 200):
            raise HTTPException(status_code=400, detail="temperature 必须在 0-200 范围内（对应 OpenAI 的 0.0-2.0）")
        player.temperature = temperature
    if reasoning_effort is not None:
        player.reasoning_effort = reasoning_effort
    db.commit()
    db.refresh(player)
    return {"id": player.id, "name": player.name, "model_config_id": player.model_config_id,
            "model_id": player.model_id, "temperature": player.temperature,
            "reasoning_effort": player.reasoning_effort}


@router.post("/{player_id}/test_llm")
def test_player_llm(player_id: int, reasoning_effort: str = Body(""), db: Session = Depends(get_db)):
    """测试该玩家（模型配置 + 模型 + 指定思考等级）能否真实调用 LLM 返回合法落子。"""
    player = get_ai_player(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="玩家不存在")
    config = get_model_config(db, player.model_config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    api_key = resolve_api_key(config)
    try:
        prompt = build_prompt(create_board(), 1, [], player.name)
        row, col, reason = call_llm(config.base_url, api_key, player.model_id, prompt,
                                    TEMPERATURE, reasoning_effort=reasoning_effort)
        if row is not None:
            return {"status": "ok", "detail": f"连通成功，模型建议落子 ({row},{col})"}
        return {"status": "error", "detail": f"调用失败：{reason}"}
    except Exception as e:
        return {"status": "error", "detail": f"调用异常：{type(e).__name__}: {e}"}


@router.delete("/{player_id}")
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = delete_ai_player(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="玩家不存在")
    return {"detail": "已删除"}
