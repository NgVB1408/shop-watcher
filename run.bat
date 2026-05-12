@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [!] Chua co venv. Chay setup.bat truoc.
    exit /b 1
)
.venv\Scripts\python.exe run.py
endlocal
