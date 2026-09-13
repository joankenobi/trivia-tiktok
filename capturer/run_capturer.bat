@echo off
rem Runs the TikTok live capturer (Phase 1) using its own venv.
rem Reads config from .env in this folder; dry-run unless API_ENDPOINT+DRY_RUN=false.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: venv not found at .venv. Create it first:
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m client
if errorlevel 1 pause