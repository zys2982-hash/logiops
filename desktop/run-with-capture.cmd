@echo off
rem Start LogiOps and print where the boot log / screenshot land.
setlocal
set "DIR=%APPDATA%\LogiOps"
echo Starting LogiOps...
echo   boot log   : %DIR%\boot.log
echo   screenshot : %DIR%\window-*.png
start "" "%~dp0dist\win-unpacked\LogiOps.exe"
echo Waiting 12 seconds for first paint...
timeout /t 12 /nobreak >nul
echo.
echo --- boot log tail ---
powershell -NoProfile -Command "Get-Content \"$env:APPDATA\LogiOps\boot.log\" -Tail 20 -ErrorAction SilentlyContinue"
echo.
pause
