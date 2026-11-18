@echo off
chcp 65001 >nul
title Discord Music Bot - Wine
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Khong tim thay moi truong ao .venv!
    pause
    exit /b 1
)

echo Dang khoi dong Discord Music Bot...
".venv\Scripts\python.exe" bot.py
pause
