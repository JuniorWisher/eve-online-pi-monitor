@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   EVE Planetary Industry Monitor
echo ============================================================

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" goto setup
echo Environment ready, starting...
goto run

:setup
echo [1/3] Creating virtual environment...
where python >nul 2>nul
if errorlevel 1 goto nopython
python -m venv .venv
if errorlevel 1 goto venvfail
echo [2/3] Installing dependencies...
"%PY%" -m pip install --upgrade pip -q
"%PY%" -m pip install -r requirements.txt -q
if errorlevel 1 goto depfail
echo [3/3] Starting...
goto run

:run
"%PY%" app.py %*
echo.
echo Program exited.
pause
exit /b 0

:nopython
echo.
echo [ERROR] Python not found.
echo Install Python 3.10 or newer, tick "Add python.exe to PATH" during setup.
echo Download: https://www.python.org/downloads/
echo.
pause
exit /b 1

:venvfail
echo.
echo [ERROR] Failed to create virtual environment in .venv
pause
exit /b 1

:depfail
echo.
echo [ERROR] Failed to install dependencies. Check your network and run again.
pause
exit /b 1
