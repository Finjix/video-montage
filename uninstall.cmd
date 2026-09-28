@echo off
start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall.ps1" -Detached
exit /b %errorlevel%
