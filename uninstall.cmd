@echo off
if /I "%~dp0"=="%USERPROFILE%\video-montage\" (
    start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall.ps1" -Detached
    if errorlevel 1 exit /b 1
    echo Uninstall started. Exit code 3 means completion is pending.
    exit /b 3
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall.ps1"
exit /b %errorlevel%
