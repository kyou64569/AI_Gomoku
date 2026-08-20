from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from .routers import configs_router, players_router, rooms_router, game_router, history_router
from .models import Base
from .database import engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 加载 backend/.env（API Key 等敏感配置不入库、不进 git）
load_dotenv()

Base.metadata.create_all(bind=engine)

# ============ 可选 API Key 认证（P0：默认放行，设置 AUTH_TOKEN 后启用） ============
# 单机/局域网默认不校验，保持开箱即用；需要防护时在 backend/.env 设置 AUTH_TOKEN，
# 前端将请求头 X-Api-Key 带上同一 token 即可。未设置时完全放行，不影响现有调用方。
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "").strip()

# 排除认证的路径前缀：静态前端、OpenAPI 文档（浏览器直连需要）
AUTH_EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/favicon.ico", "/assets", "/static")


def _migrate_schema():
    """轻量列迁移：为已存在的表补充新增列 / 移除废弃列（create_all 不会改已有表）。"""
    with engine.connect() as conn:
        try:
            # AIPlayer 增加思考等级列（按玩家配置，模型列表不一定都支持）
            # SECURITY NOTE: Table names are hardcoded here to prevent SQL injection
            pcols = [row[1] for row in conn.execute(text("PRAGMA table_info(ai_players)"))]
            if "reasoning_effort" not in pcols:
                conn.execute(text("ALTER TABLE ai_players ADD COLUMN reasoning_effort VARCHAR(20) DEFAULT ''"))
                conn.commit()
                logger.info("[migrate] ai_players.reasoning_effort 列已添加")
            # ModelConfig 移除废弃的思考等级列（已迁移到 AI 玩家维度）
            # SECURITY NOTE: Table names are hardcoded here to prevent SQL injection
            mcols = [row[1] for row in conn.execute(text("PRAGMA table_info(model_configs)"))]
            if "reasoning_effort" in mcols:
                conn.execute(text("ALTER TABLE model_configs DROP COLUMN reasoning_effort"))
                conn.commit()
                logger.info("[migrate] model_configs.reasoning_effort 列已移除（迁移至 ai_players）")
            # games 增加 created_at（历史页对局时间）
            # SECURITY NOTE: Table names are hardcoded here to prevent SQL injection
            gcols = [row[1] for row in conn.execute(text("PRAGMA table_info(games)"))]
            if "created_at" not in gcols:
                conn.execute(text("ALTER TABLE games ADD COLUMN created_at DATETIME"))
                # 使用 Python 生成 UTC 时间戳，避免 SQLite 特定函数依赖
                now_utc = datetime.now(timezone.utc).isoformat()
                conn.execute(text("UPDATE games SET created_at = :now WHERE created_at IS NULL"), {"now": now_utc})
                conn.commit()
                logger.info("[migrate] games.created_at 列已添加（现有记录已更新）")
        except Exception as e:
            logger.error(f"[migrate] 列迁移失败: {e}", exc_info=True)
            # For critical migration failures, consider raising the exception
            # to prevent running with incompatible schema
            # logger.warning("应用将以可能不兼容的数据库架构继续运行")


_migrate_schema()

app = FastAPI(title="AI Gomoku", version="1.0.0")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not AUTH_TOKEN:
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api"):
        if not any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
            provided = request.headers.get("X-Api-Key", "")
            if provided != AUTH_TOKEN:
                return JSONResponse(status_code=401, content={"detail": "未授权：X-Api-Key 无效或缺失"})
    return await call_next(request)


# CORS：允许来源支持环境变量覆盖（CORS_ORIGINS，逗号分隔），默认兼容本地开发端口
_cors_origins = [o.strip() for o in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000",
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Api-Key"],
)

app.include_router(configs_router)
app.include_router(players_router)
app.include_router(rooms_router)
app.include_router(game_router)
app.include_router(history_router)

# 前端静态目录：基于 __file__ 的绝对路径，不依赖启动 CWD
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
