@echo off
setlocal EnableExtensions EnableDelayedExpansion
title AI Gomoku Backend Service

rem --- safe code page switch (fail-quiet so it never kills the script) ---
chcp 65001 >nul 2>&1

rem --- constants ---
set "PY=C:\Users\asus\.workbuddy\binaries\python\versions\3.13.12\python.exe"
set "BACKEND=%~dp0backend"
set "HOST=0.0.0.0"
set "PREFERRED_PORT=8000"

echo ===============================================
echo   AI Gomoku Backend Service Launcher
echo ===============================================
echo.

rem --- enter backend directory ---
pushd "%BACKEND%" 2>nul
if errorlevel 1 (
    echo [ERROR] Cannot enter backend directory: %BACKEND%
    echo Press any key to exit . . .
    pause >nul
    exit /b 1
)
echo [INFO] Working dir: %CD%

rem --- python sanity check (the absolute path is known-good per user memory) ---
"%PY%" --version >nul 2>&1
set "PY_ARGS=--version"
if errorlevel 1 (
    echo [WARN] Default Python failed, falling back to: py -3.10
    py -3.10 --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] No working Python interpreter found.
        popd
        pause >nul
        exit /b 1
    )
    set "PY=py"
    set "PY_VERSION=3.10"
) else (
    set "PY_VERSION=managed-3.13.12"
)
echo [INFO] Python: !PY! (%PY_VERSION%)

rem --- port detection: probe 8000..8099, pick the first LISTEN-free port ---
set "FINAL_PORT="
set "IS_PREFERRED=0"
for /L %%P in (8000,1,8099) do (
    if not defined FINAL_PORT (
        netstat -ano | findstr " LISTENING " | findstr ":%%P " >nul 2>&1
        if errorlevel 1 (
            set "FINAL_PORT=%%P"
            if "%%P"=="8000" set "IS_PREFERRED=1"
        )
    )
)
if not defined FINAL_PORT (
    echo [ERROR] No free port in range 8000-8099. Try stop.bat first.
    popd
    pause >nul
    exit /b 1
)
if "%IS_PREFERRED%"=="0" (
    echo [INFO] Port 8000 busy, auto-selected %FINAL_PORT% instead.
) else (
    echo [INFO] Using port %FINAL_PORT%.
)

rem --- write port to .port for frontend tooling / readme ---
echo %FINAL_PORT% > "%~dp0.port"

echo.
echo [START] uvicorn app.main:app --reload --host %HOST% --port %FINAL_PORT%
echo [URL]   http://localhost:%FINAL_PORT%/
echo [DOCS]  http://localhost:%FINAL_PORT%/docs
echo [STOP]  Press Ctrl+C, or run stop.bat
echo.

rem --- launch ---
if "!PY!"=="py" (
    py -3.10 -m uvicorn app.main:app --reload --host %HOST% --port %FINAL_PORT%
) else (
    "!PY!" -m uvicorn app.main:app --reload --host %HOST% --port %FINAL_PORT%
)
set "UVICORN_EXITCODE=%errorlevel%"

popd
echo.
if %UVICORN_EXITCODE% neq 0 (
    echo [WARN] uvicorn exited with code %UVICORN_EXITCODE%.
    echo        Common cause: port %FINAL_PORT% just got occupied. Run stop.bat then retry.
) else (
    echo [DONE] Service exited cleanly.
)
echo Press any key to close . . .
pause >nul
endlocal
