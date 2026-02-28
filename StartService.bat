@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

start "" py -3 "%~dp0main.py" --daemon
start "" "D:\WeGameApps\rail_apps\无畏契约(2001715)\ACLOS\aclos-launcher.exe"