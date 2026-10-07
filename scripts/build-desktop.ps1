# 一键构建 Windows 桌面安装包（Electron）
#
# 用法（在仓库根目录或任意位置）：
#     pwsh -File scripts/build-desktop.ps1
#
# 它做四件事：
#   1. 构建前端（frontend/dist）
#   2. 把 dist 拷进 desktop/web（打包进安装包）
#   3. 安装 desktop 的依赖（electron / electron-builder）
#   4. 打出 NSIS 安装包 → desktop/dist/LogiOps-Setup-<版本>.exe
#
# 国内网络：Electron 与 electron-builder 的二进制走 npmmirror 镜像（desktop/.npmrc 里也配了）。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"

Write-Host "== 1/4 构建前端 ==" -ForegroundColor Cyan
& corepack pnpm --dir frontend run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }

Write-Host "== 2/4 拷贝前端产物到 desktop/web ==" -ForegroundColor Cyan
if (Test-Path "desktop\web") { Remove-Item -Recurse -Force "desktop\web" }
Copy-Item -Recurse "frontend\dist" "desktop\web"
Write-Host ("   web 文件数：" + (Get-ChildItem "desktop\web" -Recurse -File).Count)

Write-Host "== 3/4 安装桌面端依赖 ==" -ForegroundColor Cyan
& corepack pnpm --dir desktop install
if ($LASTEXITCODE -ne 0) { throw "桌面端依赖安装失败" }

# electron 的运行时（约 100MB）由它的 postinstall 下载；若之前被 pnpm 跳过，这里补一次
if (-not (Test-Path "desktop\node_modules\electron\dist\electron.exe")) {
    Write-Host "   Electron 运行时缺失，补下载…" -ForegroundColor Yellow
    Push-Location "desktop\node_modules\electron"
    & node install.js
    Pop-Location
}

# electron-builder 内部会**直接调用 `pnpm`** 收集依赖树；而本机如果只用 corepack 提供 pnpm
# （pnpm 不在 PATH 上），就会报 `No JSON content found in output` → 造一个 shim 顶上。
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    $shimDir = Join-Path $root "artifacts\pnpm-shim"
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    Set-Content -Path (Join-Path $shimDir "pnpm.cmd") -Encoding ascii -Value "@echo off`r`ncorepack pnpm %*"
    $env:PATH = "$shimDir;$env:PATH"
    Write-Host "   已为 electron-builder 准备 pnpm shim：$shimDir" -ForegroundColor Yellow
}

Write-Host "== 4/4 打包 NSIS 安装包 ==" -ForegroundColor Cyan
& corepack pnpm --dir desktop run dist
if ($LASTEXITCODE -ne 0) { throw "electron-builder 打包失败" }

Write-Host ""
Write-Host "完成 ✓ 安装包位于：" -ForegroundColor Green
Get-ChildItem "desktop\dist\*.exe" | ForEach-Object { Write-Host ("   " + $_.FullName + "  (" + [math]::Round($_.Length / 1MB, 1) + " MB)") }
Write-Host ""
Write-Host "双击安装后：桌面会出现 LogiOps 快捷方式；首次打开默认连 http://101.200.139.115"
Write-Host "要改服务器地址：软件菜单 → 服务器 → 设置服务器地址…"
