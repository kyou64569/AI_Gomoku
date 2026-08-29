"""AI 擂台赛路由：让两个 AI 玩家自动对弈 N 局，产出模型棋力排行。"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import arena_service

router = APIRouter(prefix="/api/arena", tags=["arena"])


@router.post("/")
def create_run(
    player_a_id: int = Body(..., embed=True),
    player_b_id: int = Body(..., embed=True),
    rounds: int = Body(4, embed=True),
    size: int = Body(15, embed=True),
    forbidden: int = Body(0, embed=True),
    difficulty: str = Body("normal", embed=True),
    max_moves: int = Body(80, embed=True),
    db: Session = Depends(get_db),
):
    """启动一个擂台赛（后台线程串行对弈，立即可用 GET /api/arena/ 查进度）。"""
    result = arena_service.start_run(
        db, player_a_id, player_b_id,
        rounds=rounds, size=size, forbidden=forbidden,
        difficulty=difficulty, max_moves=max_moves,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("detail", "启动失败"))
    return result


@router.get("/")
def list_runs(limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    """擂台赛列表 + 当前正在跑的 run_id（前端据此轮询进度）。"""
    return arena_service.list_runs(db, limit=limit)


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    """跨所有擂台赛聚合的模型棋力榜 + 交手记录。"""
    return arena_service.leaderboard(db)


@router.get("/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = arena_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="擂台赛不存在")
    return run


@router.post("/{run_id}/abort")
def abort(run_id: int, db: Session = Depends(get_db)):
    """请求中止（后台线程会在下一手开始前停下，已完成的对局保留）。"""
    if not arena_service.abort_run(run_id):
        raise HTTPException(status_code=400, detail="该擂台赛未在运行中")
    return {"status": "ok", "detail": "已请求中止，将在当前手结束后停止"}


@router.delete("/{run_id}")
def delete(run_id: int, drop_games: bool = Query(True), db: Session = Depends(get_db)):
    """删除擂台赛记录。drop_games=true 时同时删除它产生的对局与房间。"""
    if not arena_service.delete_run(db, run_id, drop_games=drop_games):
        raise HTTPException(status_code=400, detail="擂台赛不存在，或仍在运行中（请先中止）")
    return {"status": "deleted"}
