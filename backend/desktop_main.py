"""AI Gomoku 桌面入口：双击 exe（或 `python desktop_main.py`）即开即用。

与网页版（start.bat → uvicorn app.main:app --reload）**共用同一个 FastAPI 应用**，
仅启动方式不同，网页版零改动：

- 桌面版：内嵌 uvicorn（127.0.0.1，自动探测端口，非 --reload）+ pywebview 原生窗口
  （Windows 走系统自带 WebView2）；窗口关闭 → 进程退出。pywebview 不可用（未安装 /
  WebView2 缺失 / 创建失败）时自动回退「打开默认浏览器」模式（start.bat 的兄弟路径）。
- 冻结（PyInstaller）运行时：前端从解包目录（sys._MEIPASS）读取、数据库放 exe 同级
  data/ 目录、.env 从 exe 同级加载（见 app/main.py 与 app/database.py 的 frozen 分支）。
- 单实例：启动前探测 8000-8099 上是否已有本应用实例（与 start.bat 的复用逻辑同口径），
  有则直接在它上面开窗口，不再起第二个服务。

用法：
  python desktop_main.py                 # 源码模式开发桌面版（共用 backend/gomoku.db）
  AI-Gomoku-Desktop.exe                  # 打包后
  ... --browser                          # 强制浏览器模式（跳过原生窗口）
  ... --selftest                         # 起服务 → 探测 /openapi.json 与 / → 退出（自动化验证用）
"""

import argparse
import base64
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
EXE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BACKEND_DIR
WINDOW_TITLE = "AI Gomoku · 弈境"
PORT_RANGE = range(8000, 8100)  # 与 start.bat 同一口径

logger = logging.getLogger("desktop")
_stdio_redirected = False  # 冻结模式下 stdio 被重定向到 desktop.log 时为 True（用于日志去重）


def _ensure_stdio() -> None:
    """冻结（console=False）运行时 sys.stdout/stderr 是 None：uvicorn 配置日志的
    formatter 会调 sys.stdout.isatty() 直接崩（dictConfig → ValueError: Unable to
    configure formatter 'default'，双击启动必现、从终端启动因继承控制台而掩盖）。
    这里把它们重定向到 desktop.log（行缓冲）：既修复崩溃，也让 uvicorn 日志落文件。
    """
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_dir = EXE_DIR / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        target = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        target = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = target
    if sys.stderr is None:
        sys.stderr = target
    global _stdio_redirected
    _stdio_redirected = True


def _setup_logging() -> None:
    """控制台 + 文件双写（冻结模式下控制台不可见，留 data/desktop.log 便于排查）。

    stdio 被重定向到 desktop.log 时跳过 StreamHandler：否则 desktop 自己的日志
    会经「重定向的 stderr」与「FileHandler」在文件里各写一份。
    """
    handlers = []
    if not _stdio_redirected:
        handlers.append(logging.StreamHandler())
    try:
        log_dir = EXE_DIR / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "desktop.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=handlers)


def _probe_ours(port: int, timeout: float = 0.5) -> bool:
    """探测该端口是否是「本应用」的运行实例（/openapi.json 200 且含 /api/ 路径）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=timeout) as resp:
            if resp.status != 200:
                return False
            spec = json.loads(resp.read().decode("utf-8"))
            return spec.get("openapi", "").startswith("3.") and any(
                p.startswith("/api/") for p in spec.get("paths", {}))
    except Exception:
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _find_existing_port() -> int | None:
    for port in PORT_RANGE:
        if _probe_ours(port):
            return port
    return None


def _pick_free_port() -> int:
    for port in PORT_RANGE:
        if _port_free(port):
            return port
    return 0  # 让系统随机分配（极端情况：8000-8099 全被占）


def _start_server(port: int):
    """内嵌启动 uvicorn（非 --reload，主进程退出随 daemon 线程消亡）。返回 Server。"""
    import uvicorn

    from app.main import app  # noqa: E402  触发建表/列迁移（frozen 路径分支见 app 内）

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
    for _ in range(100):  # 最多等 10s 到 started（含建表/列迁移时间）
        if server.started:
            return server
        if server.should_exit:  # 启动阶段失败（如端口被抢占）
            break
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("内嵌服务 10s 内未完成启动")
    return server


class _DesktopApi:
    """暴露给前端（window.pywebview.api）的桥。

    导出文件走原生「另存为」对话框选位置，保存后把完整路径返回给前端 toast——
    浏览器模式拿不到下载文件的完整路径（沙箱限制），桌面端可以，这正是两端
    导出体验的差异点。
    """

    def save_file(self, filename: str, content_b64: str) -> str:
        import webview  # 延迟导入：与 _open_window 同口径（浏览器回退模式不装/不触发 pywebview）

        data = base64.b64decode(content_b64)
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return ""
        # 原生另存为对话框（默认文件名 = 前端传来的导出名）；用户取消 → 返回空串
        target = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename)
        if not target:
            return ""
        path = target if isinstance(target, str) else target[0]
        Path(path).write_bytes(data)  # 写失败会抛 → 前端 Promise reject → 导出失败 toast
        return str(path)


def _open_window(url: str) -> None:
    """原生窗口；pywebview 不可用时抛异常，由调用方回退浏览器。"""
    import webview  # 延迟导入：浏览器回退模式不需要 pywebview

    # text_select：pywebview 默认 False 会整页禁用鼠标选择文字（用户无法选中复制
    # 思考日志/导出预览等）——必须显式开启；按钮/棋盘的禁选由前端 CSS 自己负责。
    webview.create_window(WINDOW_TITLE, url, width=1280, height=860, min_size=(980, 640),
                          text_select=True, js_api=_DesktopApi())
    webview.start()


def _serve_forever(url: str, server) -> None:
    """浏览器回退模式：服务随本进程存活，Ctrl+C / 窗口关闭才退出。

    server 可能为 None（复用已运行实例时服务不归本进程管，只挂住窗口用进程）。
    """
    print(f"[desktop] 服务已就绪：{url}（浏览器模式；关闭此进程即退出服务）", flush=True)
    try:
        while True:
            if server is not None and server.should_exit:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.should_exit = True


def _selftest(server, port: int) -> int:
    """自动化验证：/openapi.json、首页 /、/api/configs/ 均可访问 → PASS。

    configs=N 会打进行日志（N 应与网页版配置数一致 → 验证共享同一数据库）。
    供打包后冒烟；仓库内运行预期 configs=5。
    """
    ok_openapi = _probe_ours(port)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            ok_index = resp.status == 200 and b"<" in resp.read(512)
    except Exception:
        ok_index = False
    n_configs = -1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/configs/", timeout=5) as resp:
            n_configs = len(json.loads(resp.read().decode("utf-8")))
    except Exception:
        pass
    ok = ok_openapi and ok_index and n_configs >= 0
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} "
          f"(openapi={ok_openapi}, index={ok_index}, configs={n_configs}, port={port})", flush=True)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=WINDOW_TITLE)
    parser.add_argument("--browser", action="store_true", help="强制浏览器模式（不开原生窗口）")
    parser.add_argument("--selftest", action="store_true", help="起服务自检后退出（自动化验证用）")
    args = parser.parse_args()

    _ensure_stdio()  # 必须最先：uvicorn.Config 构造即配置日志，而它需要可用的 stdout
    _setup_logging()

    # 已有本应用实例 → 直接复用（单实例，与 start.bat 行为同口径）
    existing = _find_existing_port()
    if existing:
        port = existing
        server = None
        logger.info("发现已运行实例（端口 %s），直接复用", port)
    else:
        port = _pick_free_port()
        if not port:
            print("[desktop] 8000-8099 全被占用且无本应用实例，退出", file=sys.stderr)
            return 1
        server = _start_server(port)
        logger.info("内嵌服务已启动：http://127.0.0.1:%s", port)

    url = f"http://127.0.0.1:{port}/"
    if args.selftest:
        if server is None:  # 复用模式下无法保证冷启动行为，自检必须独立起服务
            print("SELFTEST SKIP（已有运行实例，请先停止后自检）", flush=True)
            return 2
        code = _selftest(server, port)
        server.should_exit = True
        return code

    if args.browser:
        import webbrowser
        webbrowser.open(url)
        _serve_forever(url, server)
        return 0

    try:
        _open_window(url)
        logger.info("窗口已关闭，退出")
    except Exception as e:  # pywebview 未装 / WebView2 缺失 / 创建失败 → 浏览器兜底
        logger.warning("原生窗口不可用（%s: %s），回退浏览器模式", type(e).__name__, e)
        import webbrowser
        webbrowser.open(url)
        _serve_forever(url, server)
    finally:
        if server is not None:
            server.should_exit = True
    return 0


if __name__ == "__main__":
    # 源码模式下把 CWD 切到 backend/：load_dotenv()（无参）按 CWD 找 backend/.env，
    # 与 start.bat 行为一致；冻结模式走 app/main.py 的 exe 同级 .env 分支。
    if not getattr(sys, "frozen", False):
        os.chdir(BACKEND_DIR)
    try:
        sys.exit(main())
    except Exception:
        # 冻结模式下未捕获异常只有 PyInstaller 弹窗（信息少、不可复制）；
        # 落完整堆栈到 desktop.log，再弹一个带日志路径的提示框。
        logging.getLogger("desktop").exception("桌面版启动失败")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                f"启动失败：{sys.exc_info()[1]}\n\n详细日志：{EXE_DIR / 'data' / 'desktop.log'}",
                WINDOW_TITLE, 0x10)
        except Exception:
            pass
        sys.exit(1)
