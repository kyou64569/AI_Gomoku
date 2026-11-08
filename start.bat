@echo off
setlocal EnableExtensions EnableDelayedExpansion
title AI Gomoku - Launcher

rem NOTE: keep this file pure ASCII. cmd.exe on a Chinese Windows console
rem decodes batch files with the OEM code page, and multi-byte CJK bytes in
rem comments can split a line or turn into command separators, producing
rem errors like "'...' is not recognized as an internal or external command".

rem --- constants ---
rem Prefer project venv (backend\.venv); then PYTHON_PATH env var; then managed Python 3.13.12; then py -3.10.
if exist "%~dp0backend\.venv\Scripts\python.exe" (
    set "PY=%~dp0backend\.venv\Scripts\python.exe"
) else if defined PYTHON_PATH (
    set "PY=%PYTHON_PATH%"
) else (
    set "PY=C:\Users\asus\.workbuddy\binaries\python\versions\3.13.12\python.exe"
)
set "BACKEND=%~dp0backend"
set "HOST=0.0.0.0"
set "PREFERRED_PORT=8000"
set "PORT_MIN=8000"
set "PORT_MAX=8099"
rem FastAPI auto-generates this; used to confirm the port really serves THIS app.
set "HEALTH=/openapi.json"

echo =============================================================
echo   AI Gomoku Backend Service Launcher
echo =============================================================
echo.

rem --- HTTP probe: prefer curl.exe (fast), fall back to PowerShell ---
set "USE_CURL=0"
where curl.exe >nul 2>&1
if not errorlevel 1 set "USE_CURL=1"

rem --- enter backend directory ---
pushd "%BACKEND%" 2>nul
if errorlevel 1 (
    echo [ERROR] Cannot enter backend directory: %BACKEND%
    echo.
    pause
    exit /b 1
)
echo [INFO] Working dir: %CD%

rem --- python sanity check (absolute path is known-good per user memory) ---
"%PY%" --version >nul 2>&1
set "PY_RC=!errorlevel!"
if not "!PY_RC!"=="0" (
    echo [WARN] Default Python failed, falling back to: py -3.10
    py -3.10 --version >nul 2>&1
    set "PY_RC=!errorlevel!"
    if not "!PY_RC!"=="0" (
        echo [ERROR] No working Python interpreter found.
        echo.
        popd
        pause
        exit /b 1
    )
    set "PY=py"
    set "PY_VERSION=3.10"
) else (
    set "PY_VERSION=managed-3.13.12"
)
echo [INFO] Python: %PY% (%PY_VERSION%)
if "%USE_CURL%"=="1" (echo [INFO] HTTP probe: curl.exe) else (echo [INFO] HTTP probe: PowerShell)

rem --- snapshot listening ports once (file-based; never pipe into findstr) ---
set "TMPNET=%TEMP%\gomoku_netstat_%RANDOM%.tmp"
netstat -ano | findstr " LISTENING " > "%TMPNET%" 2>nul

rem ============================================================
rem  1) REUSE an instance that is already running.
rem     The old launcher silently fell back to another port when 8000
rem     was busy, then closed this window before the URL could be read -
rem     so there was no way to know which port to open. Now we scan first:
rem     if our app is already up, just open its page and start nothing.
rem
rem     We only HTTP-probe ports that netstat reports as LISTENING. Probing
rem     all 100 ports spawns ~100 curl processes, which can exhaust the
rem     Windows desktop heap (curl then dies with 0xC0000142).
rem ============================================================
echo.
echo [SCAN] Looking for an already-running AI Gomoku instance...
set "EXISTING_PORT="
set "SCAN_PORT=%PORT_MIN%"

:SCAN_LOOP
if !SCAN_PORT! GTR %PORT_MAX% goto SCAN_DONE
findstr /R /C:":!SCAN_PORT! " "%TMPNET%" >nul 2>nul
set "FIND_RC=!errorlevel!"
if "!FIND_RC!"=="0" (
    call :PROBE_OURS "http://localhost:!SCAN_PORT!%HEALTH%"
    if "!PROBE_OK!"=="1" (
        set "EXISTING_PORT=!SCAN_PORT!"
        goto SCAN_DONE
    )
)
set /a SCAN_PORT+=1
goto SCAN_LOOP

:SCAN_DONE
if defined EXISTING_PORT (
    del "%TMPNET%" 2>nul
    echo [INFO] Found a running instance on port !EXISTING_PORT!. Reusing it.
    echo.
    echo =============================================================
    echo   ALREADY RUNNING - opening the page now
    echo.
    echo   Frontend : http://localhost:!EXISTING_PORT!/
    echo   API docs : http://localhost:!EXISTING_PORT!/docs
    echo.
    echo   To move it back to port %PREFERRED_PORT%:
    echo   run stop.bat first, then start.bat again.
    echo =============================================================
    echo.
    echo %EXISTING_PORT%> "%~dp0.port"
    echo [OPEN] Launching browser...
    start "" "http://localhost:!EXISTING_PORT!/"
    echo.
    echo This launcher window can be closed. The service keeps running.
    pause
    popd
    endlocal
    exit /b 0
)
echo [SCAN] None running. Starting a new instance.

rem ============================================================
rem  2) Pick a free port (only reached when nothing is running yet)
rem ============================================================
set "FINAL_PORT="
set "TEST_PORT=%PORT_MIN%"

:PICK_LOOP
if !TEST_PORT! GTR %PORT_MAX% goto PICK_DONE
findstr /R /C:":!TEST_PORT! " "%TMPNET%" >nul 2>nul
set "FIND_RC=!errorlevel!"
if "!FIND_RC!"=="0" (
    set /a TEST_PORT+=1
    goto PICK_LOOP
)
set "FINAL_PORT=!TEST_PORT!"
goto PICK_DONE

:PICK_DONE
del "%TMPNET%" 2>nul
if not defined FINAL_PORT (
    echo [ERROR] No free port in range %PORT_MIN%-%PORT_MAX%. Run stop.bat first.
    echo.
    popd
    pause
    exit /b 1
)
if not "%FINAL_PORT%"=="%PREFERRED_PORT%" (
    echo.
    echo [WARN] Port %PREFERRED_PORT% is taken by another program.
    echo [WARN] This instance will use port %FINAL_PORT% instead - use the URL below.
    echo.
) else (
    echo [INFO] Using port %FINAL_PORT%.
)

rem --- write port to .port (no trailing space) for frontend tooling / readme ---
echo %FINAL_PORT%> "%~dp0.port"

echo.
echo [START] uvicorn app.main:app --reload --host %HOST% --port %FINAL_PORT%
echo.

rem --- launch server in its own window (logs stay visible, independent of us) ---
rem Pass /D "%BACKEND%" explicitly so uvicorn finds the "app" package
rem regardless of the host cmd's cwd.
rem Keep the window title prefix "AI Gomoku Backend" - stop.bat matches it.
if "%PY%"=="py" (
    start "AI Gomoku Backend (:!FINAL_PORT!)" /D "%BACKEND%" py -m uvicorn app.main:app --reload --host %HOST% --port !FINAL_PORT!
) else (
    start "AI Gomoku Backend (:!FINAL_PORT!)" /D "%BACKEND%" "%PY%" -m uvicorn app.main:app --reload --host %HOST% --port !FINAL_PORT!
)

rem ============================================================
rem  3) Wait until the server answers, then open the browser (~90s max)
rem ============================================================
echo [WAIT] Waiting for the server to become ready...
set "TRIES=0"
set "READY=0"

:POLL_LOOP
call :PROBE_REACHABLE "http://localhost:!FINAL_PORT!%HEALTH%"
if "!PROBE_OK!"=="1" (
    set "READY=1"
    goto POLL_DONE
)
set /a TRIES+=1
if !TRIES! GEQ 45 goto POLL_DONE
<nul set /p "=."
rem --reload spawns a child process, so the first probe is slow: 1s x15, then 2s
if !TRIES! LSS 15 (
    timeout /t 1 >nul 2>&1
) else (
    timeout /t 2 >nul 2>&1
)
goto POLL_LOOP

:POLL_DONE
echo.
echo.
echo =============================================================
if "!READY!"=="1" (
    echo   SERVER READY
    echo.
    echo   Frontend : http://localhost:!FINAL_PORT!/
    echo   API docs : http://localhost:!FINAL_PORT!/docs
    echo   To stop  : close the "AI Gomoku Backend" window, or run stop.bat
    echo.
    echo [OPEN] Launching browser...
    start "" "http://localhost:!FINAL_PORT!/"
) else (
    echo   SERVER DID NOT BECOME READY IN TIME
    echo.
    echo   Check the "AI Gomoku Backend" window for the error message.
    echo   If it reports a port conflict, run stop.bat and try again.
    echo.
    echo   Once it is up, open: http://localhost:!FINAL_PORT!/
)
echo =============================================================
echo.
echo This launcher window can be closed. The service keeps running.
pause
popd
endlocal
exit /b 0

rem ============================================================
rem  Subroutine: does this URL return HTTP 200?
rem  Arg %~1 = URL      Out: PROBE_OK = 1 / 0
rem
rem  Exit codes are compared against the exact string "0" on purpose.
rem  The classic `if not errorlevel 1` form means "errorlevel < 1", which is
rem  ALSO true for negative codes - and Windows reports crashes as negative
rem  values (e.g. 0xC0000142 = -1073741502). Using it made a crashed curl
rem  look like a successful probe and the launcher latched onto a random port.
rem  --noproxy '*' keeps localhost probes direct when a proxy env var is set.
rem ============================================================
:PROBE_REACHABLE
set "PROBE_OK=0"
if "%USE_CURL%"=="1" (
    curl.exe -s -f --noproxy '*' -o NUL --max-time 2 "%~1" >nul 2>&1
    set "PROBE_RC=!errorlevel!"
    if "!PROBE_RC!"=="0" set "PROBE_OK=1"
) else (
    powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%~1' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
    set "PROBE_RC=!errorlevel!"
    if "!PROBE_RC!"=="0" set "PROBE_OK=1"
)
exit /b 0

rem ============================================================
rem  Subroutine: is the app on this port really ours?
rem  (returns 200 AND its OpenAPI title contains "AI Gomoku")
rem  Arg %~1 = URL      Out: PROBE_OK = 1 / 0
rem
rem  Download to a temp file and grep the FILE. Do NOT pipe curl into
rem  findstr - findstr can block forever on a pipe that yields no data,
rem  hanging the launcher (same hazard documented in stop.bat).
rem ============================================================
:PROBE_OURS
set "PROBE_OK=0"
if "%USE_CURL%"=="1" (
    set "PROBE_FILE=%TEMP%\gomoku_probe_%RANDOM%.json"
    curl.exe -s -f --noproxy '*' -o "!PROBE_FILE!" --max-time 2 "%~1" >nul 2>&1
    set "PROBE_RC=!errorlevel!"
    if "!PROBE_RC!"=="0" (
        findstr /C:"AI Gomoku" "!PROBE_FILE!" >nul 2>&1
        set "PROBE_RC=!errorlevel!"
        if "!PROBE_RC!"=="0" set "PROBE_OK=1"
    )
    del "!PROBE_FILE!" 2>nul
) else (
    powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%~1' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200 -and $r.Content -like '*AI Gomoku*') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
    set "PROBE_RC=!errorlevel!"
    if "!PROBE_RC!"=="0" set "PROBE_OK=1"
)
exit /b 0
