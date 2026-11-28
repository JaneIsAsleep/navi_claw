@echo off
setlocal
chcp 65001 >nul
set "PYTHONPATH=%~dp0backend"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%~dp0.venv-win\Scripts\python.exe" (
  echo Navi is not set up for native Windows yet. Run start-windows.cmd first.
  exit /b 1
)

"%~dp0.venv-win\Scripts\python.exe" -m navi.cli %*
exit /b %ERRORLEVEL%
