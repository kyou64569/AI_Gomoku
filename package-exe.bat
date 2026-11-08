@echo off
REM ============================================================
REM  One-click desktop EXE packager for AI_WuZiQi.
REM  Pipeline: ensure venv -> install desktop deps -> PyInstaller (spec)
REM  Output:   dist\AI-Gomoku-Desktop\AI-Gomoku-Desktop.exe
REM
REM  Double-click to build. The web version (start.bat) is NOT touched:
REM  desktop_main.py reuses the same FastAPI app with a frozen-path branch.
REM ============================================================
REM NOTE: keep this file pure ASCII (cmd.exe decodes batch with the OEM code page).
chcp 65001 >nul
cd /d "%~dp0"

set "PY=backend\.venv\Scripts\python.exe"

echo [1/3] Ensure backend venv exists ...
if not exist "%PY%" (
  echo   venv missing, creating backend\.venv with py -3.10 ...
  py -3.10 -m venv backend\.venv || goto :fail
  "%PY%" -m pip install -q -r backend\requirements.txt || goto :fail
)

echo [2/3] Install desktop deps (pywebview + pyinstaller) ...
"%PY%" -m pip install -q -r requirements-desktop.txt || goto :fail

echo [3/3] Build EXE via PyInstaller spec ...
"%PY%" -m PyInstaller AI-Gomoku-Desktop.spec --noconfirm --clean || goto :fail

echo.
echo ============================================================
echo  DONE. Artifact: dist\AI-Gomoku-Desktop\AI-Gomoku-Desktop.exe
echo  Smoke test    : dist\AI-Gomoku-Desktop\AI-Gomoku-Desktop.exe --selftest
echo  Ship          : zip the whole dist\AI-Gomoku-Desktop folder
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [package] BUILD FAILED - see messages above.
pause
exit /b 1
