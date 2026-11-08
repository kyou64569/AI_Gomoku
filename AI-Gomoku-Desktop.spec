# -*- mode: python ; coding: utf-8 -*-
"""AI Gomoku 桌面版 PyInstaller 打包配置（onedir，console 关闭）。

构建：双击 package-exe.bat，或
  backend\\.venv\\Scripts\\python.exe -m PyInstaller AI-Gomoku-Desktop.spec --noconfirm
产物：dist/AI-Gomoku-Desktop/AI-Gomoku-Desktop.exe（双击即用；数据/密钥在同目录 data/ 与 .env）
"""
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['backend\\desktop_main.py'],
    pathex=['backend'],
    binaries=[],
    datas=[
        ('frontend\\index.html', 'frontend'),  # 前端单文件（避免把 src-archived 等带入产物）
        ('backend\\.env.example', '.'),
    ],
    hiddenimports=[
        # uvicorn 按名字懒加载的模块（PyInstaller 静态分析不到）
        'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'sqlalchemy.dialects.sqlite',
    ] + collect_submodules('webview.platforms'),   # pywebview 各平台后端（Windows: winforms/edgechromium）
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AI-Gomoku-Desktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 无黑窗；日志见 data/desktop.log
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AI-Gomoku-Desktop',
)
