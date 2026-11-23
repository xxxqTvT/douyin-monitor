@echo off
chcp 65001 >nul
title Douyin Monitor
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python douyin_monitor.py
echo.
echo ============================================
echo Program exited. Press any key to close.
echo ============================================
pause >nul
