@echo off
chcp 65001 >nul
title Douyin Monitor Client
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" pythonw "douyin_client.py"
exit
