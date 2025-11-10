@echo off
echo ===============================
echo   Cobot Logger Setup Utility
echo ===============================

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b
)

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
) else (
    echo Virtual environment already exists.
)

echo Activating environment and installing dependencies...
call .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Starting Cobot Logger...
python main.py

echo.
echo Setup complete! Press any key to exit.
pause >nul
