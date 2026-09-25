@echo off
rem Runs the FastAPI receiver (Phase 2) using its own venv.
rem Listens on 127.0.0.1:8000 (loopback) by default.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\uvicorn.exe" (
    echo ERROR: venv not found at .venv. Create it first:
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port 8081
if errorlevel 1 pause