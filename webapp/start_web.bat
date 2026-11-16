@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0.."
echo DripLink: http://127.0.0.1:5001
echo Keep this window open while using the dashboard.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B webapp\run.py
) else if exist ".venv-wmeter\Scripts\python.exe" (
  ".venv-wmeter\Scripts\python.exe" -B webapp\run.py
) else (
  py -3.11 -B webapp\run.py
)
pause
