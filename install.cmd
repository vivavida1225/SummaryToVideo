@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -InstallOnly -NoBrowser -NoDialog
set "install_exit=%errorlevel%"
if not "%install_exit%"=="0" echo Installation failed. See .runtime\launcher.log for details.
if /i not "%~1"=="--no-pause" pause
exit /b %install_exit%
