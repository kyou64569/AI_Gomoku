from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import logging
from .routers import configs_router, players_router, rooms_router, game_router
from .models import Base
from .database import engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 加载 backend/.env（API Key 等敏感配置不入库、不进 git）
load_dotenv()

Base.metadata.create_all(bind=engine)


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
        except Exception as e:
            logger.error(f"[migrate] 列迁移失败: {e}", exc_info=True)
            # For critical migration failures, consider raising the exception
            # to prevent running with incompatible schema
            # logger.warning("应用将以可能不兼容的数据库架构继续运行")


_migrate_schema()

app = FastAPI(title="AI Gomoku", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(configs_router)
app.include_router(players_router)
app.include_router(rooms_router)
app.include_router(game_router)

app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
