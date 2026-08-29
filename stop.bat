@echo off
setlocal EnableExtensions EnableDelayedExpansion
title AI Gomoku - Stop Service

rem NOTE: keep this file pure ASCII - cmd.exe decodes batch files with the OEM
rem code page and multi-byte CJK bytes can be misread, splitting a line or
rem turning into command separators.

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
rem    - /T also takes down the --reload worker this process spawned.
set "TMPNET=%TEMP%\gomoku_netstat_%RANDOM%.tmp"
netstat -ano | findstr " LISTENING " > "%TMPNET%" 2>nul
for /L %%P in (8000,1,8099) do (
    findstr /R /C:":%%P " "%TMPNET%" >nul 2>nul
    if not errorlevel 1 (
        for /f "tokens=5" %%K in ('findstr /R /C:":%%P " "%TMPNET%" 2^>nul') do (
            rem NOTE: parens in this message MUST be ^-escaped. cmd's block parser
            rem is naive: an unquoted "(" ... ")" inside a ( ... ) body ends the
            rem block early, so the body then runs UNCONDITIONALLY with %%K
            rem unsubstituted - printing a bogus "Stopping PID %K" and always
            rem flipping KILLED to 1, which made the script claim "Stopped"
            rem even when nothing was running.
            echo [INFO] Stopping PID %%K and its children ^(LISTEN on :%%P^)
            taskkill /F /T /PID %%K >nul 2>&1
            set "KILLED=1"
        )
    )
)
del "%TMPNET%" 2>nul

rem 2) belt-and-suspenders: kill the uvicorn launched by start.bat, by title.
rem    ONLY match the "AI Gomoku Backend" window title - NEVER kill all
rem    python.exe, that would clobber the user's Jupyter or other projects.
for /f "tokens=2" %%K in ('tasklist /FI "WINDOWTITLE eq AI Gomoku Backend*" /NH 2^>nul ^| findstr /R "python\.exe"') do (
    echo [INFO] Stopping uvicorn PID %%K and its children ^(title: AI Gomoku Backend^)
    taskkill /F /T /PID %%K >nul 2>&1
    set "KILLED=1"
)

rem 3) ORPHANED --reload workers (the case the two passes above cannot see).
rem    uvicorn --reload spawns a worker via multiprocessing.spawn; the socket
rem    belongs to that worker but netstat reports the ORIGINAL parent pid. When
rem    the parent dies first, the worker is orphaned: the port stays busy, every
rem    port-based lookup points at a dead pid, taskkill does nothing, and this
rem    script used to wrongly print "ports are clean". The next start.bat then
rem    silently picked another port.
rem    stop_workers.ps1 finds them by command line (spawn_main whose parent is
rem    gone), so it never touches other projects. It prints one "GOMOKU-KILL <pid>"
rem    line per kill; we count those lines rather than trusting the exit code,
rem    because how "exit N" propagates back to cmd.exe is not reliable here.
if exist "%~dp0stop_workers.ps1" (
    set "SWEEP_OUT=%TEMP%\gomoku_sweep_%RANDOM%.txt"
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_workers.ps1" > "!SWEEP_OUT!" 2>nul
    findstr /C:"GOMOKU-KILL" "!SWEEP_OUT!" >nul 2>nul
    set "FIND_RC=!errorlevel!"
    if "!FIND_RC!"=="0" (
        set "KILLED=1"
        for /f "tokens=2" %%K in ('findstr /C:"GOMOKU-KILL" "!SWEEP_OUT!" 2^>nul') do (
            echo [INFO] Stopped orphaned worker PID %%K
        )
    )
    del "!SWEEP_OUT!" 2>nul
) else (
    echo [WARN] stop_workers.ps1 not found - orphaned workers may survive.
)

rem 4) Verify for real instead of assuming: re-scan and report what is still up.
set "STILL_BUSY="
set "TMPNET2=%TEMP%\gomoku_netstat2_%RANDOM%.tmp"
netstat -ano | findstr " LISTENING " > "%TMPNET2%" 2>nul
for /L %%P in (8000,1,8099) do (
    findstr /R /C:":%%P " "%TMPNET2%" >nul 2>nul
    if not errorlevel 1 set "STILL_BUSY=!STILL_BUSY! %%P"
)
del "%TMPNET2%" 2>nul

echo.
if "%KILLED%"=="1" (
    if defined STILL_BUSY (
        echo [WARN] Stopped what I could, but ports%STILL_BUSY% are STILL in use.
        echo [WARN] Close the "AI Gomoku Backend" window by hand, then re-run stop.bat.
    ) else (
        echo [DONE] Stopped. Ports 8000-8099 are clean.
    )
) else (
    if defined STILL_BUSY (
        echo [INFO] No AI Gomoku process found, but ports%STILL_BUSY% are in use
        echo [INFO] by other programs. start.bat will pick a different port.
    ) else (
        echo [INFO] No running uvicorn found. Ports 8000-8099 are clean.
    )
)
echo.
echo Press any key to close . . .
pause >nul
endlocal
