"""运行期路径解析：源码模式 vs PyInstaller 桌面版（frozen）。

桌面版数据与密钥的默认策略：**优先与网页版共享同一份**。

- exe 在仓库内构建运行（dist/<name>/ 位于仓库根下两层）→ 自动探测到仓库的
  backend/，数据库与 .env 都用网页版同一份（backend/gomoku.db、backend/.env），
  桌面端与网页端看到完全相同的模型配置与对局数据。
- 探测不到仓库布局（dist 文件夹被拷走单独分发）→ 退回 exe 同级 data/ 的
  独立数据库与 .env，保证可移植。
- 源码模式（非 frozen）不做任何处理，调用方走原逻辑。
"""

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def meipass() -> Path:
    """PyInstaller 解包目录（onedir 下为 _internal），存放 --add-data 打入的文件。"""
    return Path(getattr(sys, "_MEIPASS", exe_dir()))


def repo_backend_dir() -> Path | None:
    """仓库布局探测：exe 位于 <repo>/dist/<name>/ 且 <repo>/backend 存在 → 返回它。

    命中时桌面版与网页版共享 backend/gomoku.db 与 backend/.env（用户预期：
    两端看到同一份配置与数据）；拷出仓库单独分发时返回 None。
    """
    if not is_frozen():
        return None
    candidate = exe_dir().parent.parent / "backend"
    if (candidate / "app" / "main.py").exists():
        return candidate
    return None


def env_file() -> Path | None:
    """应加载的 .env 路径；源码模式返回 None（调用方走 load_dotenv() 按 CWD 搜索）。

    冻结模式：exe 同级 .env 优先（显式覆盖），其次共享仓库 backend/.env。
    返回不存在的路径也无妨——load_dotenv 对缺失文件是 no-op。
    """
    if not is_frozen():
        return None
    local = exe_dir() / ".env"
    if local.exists():
        return local
    repo = repo_backend_dir()
    if repo and (repo / ".env").exists():
        return repo / ".env"
    return local


def database_file(name: str = "gomoku.db") -> Path:
    """冻结模式应使用的 sqlite 文件路径（仓库共享优先，否则 exe 同级 data/）。"""
    repo = repo_backend_dir()
    if repo is not None:
        return repo / name
    data_dir = exe_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / name
