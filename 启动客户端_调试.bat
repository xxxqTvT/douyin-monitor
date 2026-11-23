@echo off
chcp 65001 >nul
title Douyin Monitor Client (debug)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python "douyin_client.py"
echo.
echo Program exited. Press any key to close.
pause >nul
