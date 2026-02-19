@echo off
title Scolecite - Server
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] No virtual environment found.
    echo Run:  python -m venv .venv
    echo Then: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo  =============================================
echo   SCOLECITE  -  AI Quant Trading Server
echo  =============================================
echo.

python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
pause
