@echo off
setlocal EnableExtensions
title AI Gomoku - Stop Service

echo ===============================================
echo   AI Gomoku Service Stopper
echo ===============================================
echo.

set "KILLED=0"

rem 1) kill whatever process currently LISTENs on ports 8000-8099.
rem    Lessons learned:
rem    - DO NOT run `netstat | findstr | findstr` inside `for /f` - inner
rem      findstr can hang forever on stdin, leaving cmd windows stuck
rem      with title "findstr ..." that re-spawn on each loop iteration.
rem    - DO NOT call `for /f` against `findstr` that produces zero matches.
rem      Some Windows builds never see EOF on the empty pipe and hang.
rem    - Solution: dump netstat to a temp file once, then for each port
rem      do a presence check (findstr >nul) FIRST; only run the for /f
rem      extraction when the presence check sets errorlevel=0.
set "TMPNET=%TEMP%\gomoku_netstat_%RANDOM%.tmp"
netstat -ano | findstr " LISTENING " > "%TMPNET%" 2>nul
for /L %%P in (8000,1,8099) do (
    findstr /R /C:":%%P " "%TMPNET%" >nul 2>nul
    if not errorlevel 1 (
        for /f "tokens=5" %%K in ('findstr /R /C:":%%P " "%TMPNET%" 2^>nul') do (
            echo [INFO] Killing PID %%K (LISTEN on :%%P)
            taskkill /F /PID %%K >nul 2>&1
            if not errorlevel 1 set "KILLED=1"
        )
    )
)
del "%TMPNET%" 2>nul

rem 2) belt-and-suspenders: kill any lingering python uvicorn from this project.
rem    ONLY match the "AI Gomoku Backend" window title (set by start.bat),
rem    NEVER kill all python.exe - that would clobber the user's Jupyter or
rem    other project Python processes.
for /f "tokens=2" %%K in ('tasklist /FI "WINDOWTITLE eq AI Gomoku Backend*" /NH 2^>nul ^| findstr /R "python\.exe"') do (
    echo [INFO] Killing leftover uvicorn PID %%K (title: AI Gomoku Backend)
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