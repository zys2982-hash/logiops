@echo off
rem ---------------------------------------------------------------------------
rem LogiOps desktop - diagnostic launcher
rem
rem Starts the app with screenshot capture enabled, so that even if the window
rem is blank / invisible, we still get a PNG of what it renders plus a boot log.
rem
rem Output:
rem   %USERPROFILE%\Desktop\LogiOps-screenshot\   <- PNG of the first paint
rem   %TEMP%\logiops-boot.log                     <- boot trace
rem ---------------------------------------------------------------------------
setlocal
set "CAPDIR=%USERPROFILE%\Desktop\LogiOps-screenshot"
set "LOGIOPS_CAPTURE_DIR=%CAPDIR%"
set "LOGIOPS_BOOT_LOG=%TEMP%\logiops-boot.log"

echo [1/3] Starting LogiOps (capture mode)...
echo       screenshot dir : %CAPDIR%
echo       boot log       : %LOGIOPS_BOOT_LOG%
start "" "%~dp0dist\win-unpacked\LogiOps.exe"

echo [2/3] Waiting 10 seconds for the first paint...
timeout /t 10 /nobreak >nul

echo [3/3] Result:
if exist "%CAPDIR%" (
  dir /b "%CAPDIR%"
) else (
  echo       no screenshot was written - the window never painted
)
echo.
echo Boot log tail:
powershell -NoProfile -Command "if (Test-Path $env:LOGIOPS_BOOT_LOG) { Get-Content $env:LOGIOPS_BOOT_LOG -Tail 15 } else { 'no boot log' }"
echo.
pause
