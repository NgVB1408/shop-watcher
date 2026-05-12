@echo off
setlocal
cd /d "%~dp0"

echo === Tao venv ===
python -m venv .venv || exit /b 1

echo === Cap nhat pip ===
.venv\Scripts\python.exe -m pip install --upgrade pip || exit /b 1

echo === Cai dat dependencies ===
.venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1

echo === Cai Chromium cho Playwright (cho tool lay cookie Shopee) ===
.venv\Scripts\python.exe -m playwright install chromium || echo [!] Playwright install Chromium fail, ban co the bo qua neu khong dung cookie tool.

if not exist .env (
    echo === Tao .env tu .env.example ===
    copy .env.example .env >nul
    echo [!] Mo .env de dien TELEGRAM_BOT_TOKEN.
)

echo.
echo [OK] Setup xong.
echo   1. Mo .env va dien TELEGRAM_BOT_TOKEN
echo   2. Chay: run.bat
echo.
endlocal
