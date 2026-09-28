@echo off
setlocal
set "NO_PAUSE="
if /I "%~1"=="-NoPause" set "NO_PAUSE=1"
if defined CODEX_HOME (set "SKILL_ROOT=%CODEX_HOME%\skills\video-montage\") else (set "SKILL_ROOT=%USERPROFILE%\.codex\skills\video-montage\")
if /I "%~dp0"=="%SKILL_ROOT%" (
    echo This copy is inside the installation directory.
    if not defined NO_PAUSE (
        echo Press any key to start uninstall. It will finish after this window closes.
        pause >nul
    )
    start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall.ps1" -Detached
    if errorlevel 1 (
        echo Failed to start uninstall.
        if not defined NO_PAUSE pause
        exit /b 1
    )
    echo Uninstall started. Completion is pending; exit code 3 is expected.
    exit /b 3
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\uninstall.ps1"
set "EXIT_CODE=%errorlevel%"
if "%EXIT_CODE%"=="0" (
    echo Uninstall completed.
) else (
    echo Uninstall failed with exit code %EXIT_CODE%. See the error above.
)
if not defined NO_PAUSE (
    echo Press any key to close this window.
    pause >nul
)
exit /b %EXIT_CODE%
