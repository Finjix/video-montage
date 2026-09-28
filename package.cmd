@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%dependencies\python\python.exe" (
  echo Bundled Python is missing: "%ROOT%dependencies\python\python.exe"
  exit /b 2
)
"%ROOT%dependencies\python\python.exe" -B "%ROOT%tools\package_release.py" %*
exit /b %errorlevel%
