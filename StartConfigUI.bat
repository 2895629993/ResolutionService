@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

py -3 "%~dp0main.py" --config-ui
