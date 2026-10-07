@echo off
rem ===========================================================================
rem  把最新版装到 D:\LogiOps（替换旧版本），并让该目录以后可被普通用户更新。
rem
rem  背景：D:\LogiOps 是上次"提权安装"创建的目录（属主 Administrators），
rem        普通权限无法删除/写入 —— 所以这一步需要管理员权限（脚本会自动请求 UAC）。
rem        装完脚本会给你的用户账号加"完全控制"，**以后更新就不需要管理员了**。
rem
rem  用法：双击本文件 → UAC 弹窗点「是」
rem ===========================================================================
setlocal
set "SRC=%LOCALAPPDATA%\Programs\LogiOps"
set "DST=D:\LogiOps"

net session >nul 2>&1
if errorlevel 1 (
  echo 正在请求管理员权限（请在弹窗中点「是」）...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo [1/6] 检查最新版构建产物...
if not exist "%SRC%\LogiOps.exe" (
  echo   [错误] 找不到 %SRC%\LogiOps.exe
  echo   请先运行 scripts\deploy-desktop-local.cmd（或 build-desktop.ps1）
  echo.
  pause
  exit /b 1
)
echo    OK: %SRC%\LogiOps.exe

echo [2/6] 结束正在运行的 LogiOps...
taskkill /F /IM LogiOps.exe >nul 2>&1

echo [3/6] 删除旧版本 %DST% ...
if exist "%DST%" rmdir /S /Q "%DST%"

echo [4/6] 复制最新版到 %DST% ...
mkdir "%DST%" 2>nul
xcopy "%SRC%\*" "%DST%\" /E /I /Y /Q >nul
if not exist "%DST%\LogiOps.exe" (
  echo   [错误] 复制失败
  pause
  exit /b 1
)

echo [5/6] 给你的账号加完全控制（以后更新无需管理员）...
icacls "%DST%" /grant "%USERNAME%:(OI)(CI)F" /T >nul

echo [6/6] 重建桌面/开始菜单快捷方式（指向 %DST%）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; foreach($p in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'LogiOps.lnk'),(Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\LogiOps.lnk'))){$s=$ws.CreateShortcut($p);$s.TargetPath='D:\LogiOps\LogiOps.exe';$s.WorkingDirectory='D:\LogiOps';$s.IconLocation='D:\LogiOps\LogiOps.exe';$s.Description='LogiOps 物流异常协同平台';$s.Save()}"

echo.
echo 完成！已安装到 %DST%
echo   桌面/开始菜单快捷方式已指向它
echo   以后更新：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-desktop-local.ps1 -Target D:\LogiOps
echo.
pause
