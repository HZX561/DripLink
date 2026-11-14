@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0.."
echo DripLink: http://127.0.0.1:5001
echo Keep this window open. Original website port 5000 is not changed.
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -B webapp\run.py
) else (
  py -3.11 -B webapp\run.py
)
pause
