"""API Key 解析工具：优先数据库值，为空时回退环境变量，避免密钥明文落库。"""
import os


def resolve_api_key(config) -> str:
    """取模型配置的 API Key：
    1. 数据库已存（旧数据兼容）→ 直接用；
    2. 数据库为空 → 环境变量 LLM_API_KEY_{id}，再兜底 LLM_API_KEY。
    """
    if config is None:
        return ""
    # Use getattr to safely handle missing api_key attribute
    api_key = getattr(config, "api_key", "")
    if api_key:
        return api_key
    env_key = f"LLM_API_KEY_{config.id}"
    if env_key in os.environ:
        return os.environ[env_key]
    return os.environ.get("LLM_API_KEY", "")
