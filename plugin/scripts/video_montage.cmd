@echo off
setlocal
if defined VIDEO_MONTAGE_CACHE_DIR (set "VM_CACHE=%VIDEO_MONTAGE_CACHE_DIR%") else (set "VM_CACHE=%~dp0..\runtime-cache")
set "VIDEO_MONTAGE_CACHE_DIR=%VM_CACHE%"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0video_montage.ps1" __bootstrap
if errorlevel 1 exit /b %errorlevel%
"%VM_CACHE%\environment\Scripts\python.exe" -B "%~dp0video_montage.py" %*
exit /b %errorlevel%
