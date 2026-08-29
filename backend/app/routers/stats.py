"""系统状态（可观测性）：按模型维度暴露 LLM 调用的可靠性指标。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AIPlayer, ModelConfig
from ..services import metrics

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/")
def get_stats(db: Session = Depends(get_db)):
    """返回每个 (base_url, model_id) 的调用统计。

    指标口径：
    - calls   = 落子决策次数（每次决策最多发 1 个真实请求，靠参数组合缓存保证）
    - ok      = LLM 返回了可解析坐标的次数
    - timeout / parse_fail = 失败分类
    - rejected  = LLM 给了坐标但不合法（越界/已占/禁手）
    - fallback  = 最终没采纳 LLM、走了引擎兜底
    - degraded  = 因端点不兼容而降级的参数组合次数（response_format / reasoning_effort）
    """
    rows = metrics.snapshot()

    # 把配置名补进去，方便前端显示"DeepSeek / gpt-4o"而不是裸 URL
    configs = db.query(ModelConfig).all()
    cfg_by_url = {c.base_url: c.name for c in configs}
    players = db.query(AIPlayer).all()
    player_by_model = {}
    for p in players:
        player_by_model.setdefault(p.model_id, []).append(p.name)

    for r in rows:
        r["config_name"] = cfg_by_url.get(r["base_url"], "")
        r["players"] = player_by_model.get(r["model_id"], [])

    return {
        "models": rows,
        "totals": {
            "calls": sum(r["calls"] for r in rows),
            "ok": sum(r["ok"] for r in rows),
            "timeout": sum(r["timeout"] for r in rows),
            "parse_fail": sum(r["parse_fail"] for r in rows),
            "rejected": sum(r["rejected"] for r in rows),
            "fallback": sum(r["fallback"] for r in rows),
            "degraded": sum(r["degraded"] for r in rows),
        },
        "model_count": len(rows),
    }


@router.post("/reset")
def reset_stats():
    """清空进程内指标（进程重启也会自动清空）。"""
    metrics.reset()
    return {"status": "ok"}
