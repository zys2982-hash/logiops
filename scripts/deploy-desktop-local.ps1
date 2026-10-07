# 把桌面客户端「就地安装」到用户目录：不需要管理员权限、不写注册表、不经过 NSIS 安装程序。
#
# 为什么需要它：
#   构建产物默认在仓库里（`desktop/dist/win-unpacked`）。如果仓库位于被沙箱/安全软件
#   加固过 ACL 的目录（例如 DSH 的开发工作区：多出 CodexSandboxUsers 与 Deny 规则），
#   Chromium 的渲染进程会启动失败（render-process-gone: launch-failed），表现为
#   "双击了打不开"。复制到用户目录（ACL 干净）后一切正常 —— 2026-10-07 真机实测。
#
# 用法（在仓库根目录）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-desktop-local.ps1
#       默认装到 %LOCALAPPDATA%\Programs\LogiOps
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy-desktop-local.ps1 -Build
#       先重新打包再安装
#   ... -Target D:\LogiOps
#       装到指定目录（该目录必须对当前用户可写；D:\LogiOps 首次需要管理员建/授权，
#       见 scripts\install-to-d-logiops.cmd）
param(
  [switch]$Build,
  [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\LogiOps')
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root 'desktop\dist\win-unpacked'
$dest = $Target

if ($Build) {
  # 用当前 PowerShell 宿主执行构建脚本（有些机器没有 pwsh，只有 powershell）
  & (Join-Path $PSScriptRoot 'build-desktop.ps1')
}

if (-not (Test-Path (Join-Path $src 'LogiOps.exe'))) {
  throw "找不到构建产物：$src\LogiOps.exe —— 先跑 scripts/build-desktop.ps1（或加 -Build）"
}

Write-Host "== 复制到用户目录 ==" -ForegroundColor Cyan
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path $src '*') -Destination $dest -Recurse -Force
$files = (Get-ChildItem $dest -Recurse -File).Count
Write-Host "   $dest（$files 个文件）"

Write-Host "== 创建快捷方式 ==" -ForegroundColor Cyan
$shell = New-Object -ComObject WScript.Shell
$shortcuts = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'LogiOps.lnk'),
  (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\LogiOps.lnk')
)
foreach ($lnk in $shortcuts) {
  $shortcut = $shell.CreateShortcut($lnk)
  $shortcut.TargetPath = Join-Path $dest 'LogiOps.exe'
  $shortcut.WorkingDirectory = $dest
  $shortcut.IconLocation = Join-Path $dest 'LogiOps.exe'
  $shortcut.Description = 'LogiOps 物流异常协同平台'
  $shortcut.Save()
  Write-Host "   $lnk"
}

Write-Host ""
Write-Host "完成 ✓ 现在可以从桌面快捷方式打开（不要再从仓库目录直接双击构建产物）" -ForegroundColor Green
Write-Host "配置与日志：%APPDATA%\LogiOps\（config.json / boot.log / startup.log / window-*.png）"
