@echo off
echo ===============================
echo   Build Cobot Logger Executable
echo ===============================

if not exist .venv (
    echo [ERROR] Virtual environment not found. Run setup_env.bat first.
    pause
    exit /b
)

call .venv\Scripts\activate

if not exist requirements.txt (
    echo [ERROR] requirements.txt missing.
    pause
    exit /b
)

pip install pyinstaller

echo Building executable...
pyinstaller --onefile --noconsole --name "CobotLogger" main.py

echo.
echo Build complete! Check the 'dist' folder.
pause >nul
