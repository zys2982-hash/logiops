@echo off
rem ===========================================================================
rem  ?????? D:\LogiOps????????????????????????
rem
rem  ???????D:\LogiOps ???"????"?????? Administrators??
rem  ??????????????????? UAC??????????????
rem  ?????????????
rem
rem  ???????? -> UAC ??? [?]
rem ===========================================================================
setlocal
set "SRC=%~dp0..\desktop\dist\win-unpacked"
if not exist "%SRC%\LogiOps.exe" set "SRC=%LOCALAPPDATA%\Programs\LogiOps"
set "DST=D:\LogiOps"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights... please click YES in the UAC dialog.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo [1/6] Latest build source: %SRC%
if not exist "%SRC%\LogiOps.exe" (
  echo   [ERROR] not found: %SRC%\LogiOps.exe
  echo   Run scripts\build-desktop.cmd first.
  echo.
  pause
  exit /b 1
)
echo [2/6] Stopping running LogiOps...
taskkill /F /IM LogiOps.exe >nul 2>&1
echo [3/6] Removing old %DST% ...
if exist "%DST%" rmdir /S /Q "%DST%"
echo [4/6] Copying to %DST% ...
mkdir "%DST%" 2>nul
xcopy "%SRC%\*" "%DST%\" /E /I /Y /Q >nul
if not exist "%DST%\LogiOps.exe" (
  echo   [ERROR] copy failed
  pause
  exit /b 1
)
echo [5/6] Granting %USERNAME% full control (future updates need no admin)...
icacls "%DST%" /grant "%USERNAME%:(OI)(CI)F" /T >nul
echo [6/6] Recreating shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; foreach($p in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'LogiOps.lnk'),(Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\LogiOps.lnk'))){$s=$ws.CreateShortcut($p);$s.TargetPath='D:\LogiOps\LogiOps.exe';$s.WorkingDirectory='D:\LogiOps';$s.IconLocation='D:\LogiOps\LogiOps.exe';$s.Description='LogiOps';$s.Save()}"
echo.
echo Done. Installed to %DST%
echo   Update later: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-desktop-local.ps1 -Target D:\LogiOps
echo.
pause
