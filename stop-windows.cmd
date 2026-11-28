@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\navi-windows.ps1" stop
if errorlevel 1 (
  echo.
  echo Navi failed to stop. Review the message above and data\gateway.log.
  pause
)
endlocal
