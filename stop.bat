@echo off
setlocal EnableExtensions
title AI Gomoku - Stop Service

echo ===============================================
echo   AI Gomoku Service Stopper
echo ===============================================
echo.

set "KILLED=0"

rem 1) kill whatever process currently LISTENs on ports 8000-8099
for /L %%P in (8000,1,8099) do (
    for /f "tokens=5" %%K in ('netstat -ano ^| findstr " LISTENING " ^| findstr ":%%P "') do (
        echo [INFO] Killing PID %%K (LISTEN on :%%P)
        taskkill /F /PID %%K >nul 2>&1
        if not errorlevel 1 set "KILLED=1"
    )
)

rem 2) belt-and-suspenders: kill any lingering python uvicorn from this project
for /f "tokens=2" %%K in ('tasklist /FI "IMAGENAME eq python.exe" /NH 2^>nul ^| findstr /R "python\.exe"') do (
    echo [INFO] Killing leftover python.exe PID %%K
    taskkill /F /PID %%K >nul 2>&1
    if not errorlevel 1 set "KILLED=1"
)

if "%KILLED%"=="1" (
    echo [DONE] Stopped.
) else (
    echo [INFO] No running uvicorn found. Ports 8000-8099 are clean.
)
echo.
echo Press any key to close . . .
pause >nul
endlocal
