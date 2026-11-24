@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\navi-windows.ps1" window
if errorlevel 1 (
  echo.
  echo Navi window failed to start. Review the message above and data\gateway.log.
  pause
)
endlocal
