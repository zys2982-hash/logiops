@echo off
rem Double-clickable wrapper: install the built desktop client into %LOCALAPPDATA%\Programs\LogiOps
rem and create Desktop / Start-menu shortcuts. No admin rights, no registry changes.
rem
rem ExecutionPolicy is bypassed for THIS process only (some machines block unsigned .ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-desktop-local.ps1" %*
echo.
pause
