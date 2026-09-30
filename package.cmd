@echo off
setlocal
set "ROOT=%~dp0"
set "NO_PAUSE="
if /I "%~1"=="-NoPause" set "NO_PAUSE=1"
if /I "%~2"=="-NoPause" set "NO_PAUSE=1"
if not exist "%ROOT%assets\dependencies\python\python.exe" (
  echo Bundled Python is missing: "%ROOT%assets\dependencies\python\python.exe"
  if not defined NO_PAUSE (
    echo Press any key to close this window.
    pause >nul
  )
  exit /b 2
)
echo Packaging started. Large model files may take several minutes.
"%ROOT%assets\dependencies\python\python.exe" -X utf8 -B "%ROOT%tools\package_release.py" %*
set "EXIT_CODE=%errorlevel%"
if "%EXIT_CODE%"=="0" (
  echo Packaging completed successfully.
) else (
  echo Packaging failed with exit code %EXIT_CODE%. See the error above.
)
if not defined NO_PAUSE (
  echo Press any key to close this window.
  pause >nul
)
exit /b %EXIT_CODE%
