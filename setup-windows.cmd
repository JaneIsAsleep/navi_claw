@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\navi-windows.ps1" setup
if errorlevel 1 (
  echo.
  echo Navi Windows setup failed. Review the message above.
)
echo.
pause
endlocal
