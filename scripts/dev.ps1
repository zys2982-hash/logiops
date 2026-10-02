<#
.SYNOPSIS
    LogiOps 一键开发启动（Windows PowerShell 7）。

.DESCRIPTION
    默认走「路径 A」：本机 MySQL 3306（基线 §5.1，本机实测方案，不需要 Docker）。
    加 -WithDocker 走「路径 B」：docker compose 的 MySQL 3307（本机未装 Docker，仅备用）。

    脚本做的四件事：
      1) 环境自检（uv / 前端目录 / 端口 / Docker 可选）
      2) 数据库：-Drop 时 init-schema --drop；否则先 alembic upgrade head，
         失败自动回退 uv run python -m app.cli init-schema
      3) -Seed 时调用 scripts/seed.ps1（seed --reset --demo，幂等）
      4) 后端 (uv run uvicorn --reload) 与前端 (corepack pnpm dev) 各起一个独立窗口；
         加 -Background 则改为后台 job，日志用 Receive-Job 查看

    两条路径只差 DATABASE_URL，业务代码零差异。

.PARAMETER Drop
    先删除全部表再重建（uv run python -m app.cli init-schema --drop）。会清空数据。

.PARAMETER Seed
    建表后执行 scripts/seed.ps1（等价 uv run python -m app.seed --reset --demo）。

.PARAMETER WithDocker
    路径 B：docker compose up -d mysql 并使用 127.0.0.1:3307。本机未安装 Docker。

.PARAMETER SkipBackend
    不启动后端（只建库/seed/起前端）。

.PARAMETER SkipFrontend
    不启动前端。

.PARAMETER Background
    用 PowerShell 后台 job 代替独立窗口（适合 agent/无桌面环境）。

.PARAMETER NoEnvFile
    不在缺少 .env 时从 .env.example 复制。

.PARAMETER BackendPort
    后端端口，默认 8000。

.PARAMETER FrontendPort
    前端端口，默认 5173。

.PARAMETER Help
    打印本帮助并退出（不执行任何环境检查）。

.EXAMPLE
    pwsh -File scripts/dev.ps1 -Help

.EXAMPLE
    # 路径 A：建表 + seed + 起前后端（独立窗口）
    pwsh -File scripts/dev.ps1 -Seed

.EXAMPLE
    # 清库重建 + seed + 后台 job 启动（CI/agent 环境）
    pwsh -File scripts/dev.ps1 -Drop -Seed -Background -SkipFrontend
#>
[CmdletBinding()]
param(
    [switch]$Help,
    [switch]$Drop,
    [switch]$Seed,
    [switch]$WithDocker,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$Background,
    [switch]$NoEnvFile,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Continue'

# 注意：Windows PowerShell 5.1 在 $ErrorActionPreference='Stop' 时会把原生程序（uv/pytest/alembic/
# docker）的 stderr 升级为终止性错误。这里用 'Continue'，成败一律通过 $LASTEXITCODE 与显式 exit 判定。

function Show-Help {
    Write-Host ''
    Write-Host 'LogiOps dev.ps1 —— 一键开发启动（默认路径 A：本机 MySQL 3306）' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '用法：'
    Write-Host '  pwsh -File scripts/dev.ps1 [-Drop] [-Seed] [-WithDocker] [-SkipBackend] [-SkipFrontend]'
    Write-Host '                              [-Background] [-NoEnvFile] [-BackendPort 8000] [-FrontendPort 5173]'
    Write-Host ''
    Write-Host '参数：'
    Write-Host '  -Drop           先删表再建表（会清空本机 logiops 库数据）'
    Write-Host '  -Seed           建表后执行 seed --reset --demo（调用 scripts/seed.ps1）'
    Write-Host '  -WithDocker     路径 B：docker compose 起 mysql:3307 并使用它（本机未装 Docker）'
    Write-Host '  -SkipBackend    不启动后端'
    Write-Host '  -SkipFrontend   不启动前端'
    Write-Host '  -Background     用后台 job 启动（默认开独立窗口）'
    Write-Host '  -NoEnvFile      缺少 .env 时不自动从 .env.example 复制'
    Write-Host '  -Help           显示本帮助'
    Write-Host ''
    Write-Host '启动后：'
    Write-Host '  后端 OpenAPI   http://127.0.0.1:8000/docs'
    Write-Host '  健康检查       http://127.0.0.1:8000/healthz'
    Write-Host '  前端           http://127.0.0.1:5173'
    Write-Host ''
    Write-Host '相关脚本：scripts/seed.ps1、scripts/acceptance.ps1（基线 §14.5 一键验收）'
    Write-Host ''
}

if ($Help) {
    Show-Help
    exit 0
}

# ---------------------------------------------------------------------------
# 路径解析：无论从哪个目录执行，都相对仓库根定位
# ---------------------------------------------------------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$EnvFile = Join-Path $RepoRoot '.env'
$EnvExample = Join-Path $RepoRoot '.env.example'
$SeedScript = Join-Path $PSScriptRoot 'seed.ps1'

Write-Host ''
Write-Host '=== LogiOps dev.ps1 ===' -ForegroundColor Cyan
Write-Host "仓库根目录：$RepoRoot"

# ---------------------------------------------------------------------------
# 1) 环境自检
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $BackendDir 'pyproject.toml'))) {
    Write-Host "[错误] 找不到 backend/pyproject.toml，仓库结构不完整：$BackendDir" -ForegroundColor Red
    exit 2
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '[错误] 未找到 uv。安装：winget install astral-sh.uv 或 https://docs.astral.sh/uv/' -ForegroundColor Red
    exit 2
}
if (-not (Get-Command corepack -ErrorAction SilentlyContinue)) {
    Write-Host '[提示] 未找到 corepack，前端启动会被跳过（Node 22+ 自带 corepack）。' -ForegroundColor Yellow
    $SkipFrontend = $true
}
if (-not (Test-Path (Join-Path $FrontendDir 'package.json'))) {
    Write-Host "[提示] 前端尚未就绪（缺 frontend/package.json），跳过前端启动。" -ForegroundColor Yellow
    $SkipFrontend = $true
}

$DatabaseUrl = 'mysql+pymysql://logiops:logiops@127.0.0.1:3306/logiops?charset=utf8mb4'
if ($WithDocker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host '[错误] 未找到 docker。本机实测未安装 Docker（基线附录 D），请改用默认路径 A（不加 -WithDocker）。' -ForegroundColor Red
        exit 2
    }
    $DatabaseUrl = 'mysql+pymysql://logiops:logiops@127.0.0.1:3307/logiops?charset=utf8mb4'
}
Write-Host "数据库模式：$(if ($WithDocker) { '路径 B（docker compose，3307）' } else { '路径 A（本机 MySQL，3306）' })"

function Test-PortListening([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return [bool]$conn
    } catch {
        return $false
    }
}
if (-not $SkipBackend -and (Test-PortListening $BackendPort)) {
    Write-Host "[错误] 端口 $BackendPort 已被占用（后端可能已在运行）。" -ForegroundColor Red
    exit 2
}
if (-not $SkipFrontend -and (Test-PortListening $FrontendPort)) {
    Write-Host "[错误] 端口 $FrontendPort 已被占用（前端可能已在运行）。" -ForegroundColor Red
    exit 2
}

# ---------------------------------------------------------------------------
# 2) .env（可选）：缺少时从 .env.example 复制，路径 A 下的默认值本来就能跑
# ---------------------------------------------------------------------------
if (-not $NoEnvFile -and -not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    try {
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -ErrorAction Stop
        Write-Host '[信息] 已从 .env.example 生成 .env（可按需修改数据库/LLM 配置）。' -ForegroundColor Yellow
    } catch {
        Write-Host "[提示] 无法生成 .env（$($_.Exception.Message)）；路径 A 用配置默认值也能跑。" -ForegroundColor Yellow
    }
}

# 数据库 URL 通过进程环境变量传给子进程（权限高于 .env 文件），避免改动用户的 .env
$env:DATABASE_URL = $DatabaseUrl
$env:CLOCK_MODE = 'replay'
$env:AI_MODE = 'replay'
if (-not $env:AI_REPLAY_DIR) { $env:AI_REPLAY_DIR = 'tests/fixtures/ai' }

# ---------------------------------------------------------------------------
# 3) 路径 B：拉起 MySQL 容器并等待 healthy
# ---------------------------------------------------------------------------
if ($WithDocker) {
    Write-Host '[1/4] 启动 MySQL 容器（docker compose up -d mysql）...'
    & docker compose -f (Join-Path $RepoRoot 'docker-compose.yml') up -d mysql
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] docker compose up -d mysql 失败。' -ForegroundColor Red
        exit 2
    }
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        & docker compose -f (Join-Path $RepoRoot 'docker-compose.yml') exec -T mysql mysqladmin ping -h 127.0.0.1 -uroot -proot --silent 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 3
    }
    Write-Host '      MySQL 容器就绪。'
} else {
    Write-Host '[1/4] 路径 A：使用本机 MySQL 127.0.0.1:3306（若连接失败请先执行 scripts/init_db.sql）。'
}

# ---------------------------------------------------------------------------
# 4) 建表（-Drop 则先删）+ 可选 seed
# ---------------------------------------------------------------------------
Push-Location $BackendDir
try {
    if ($Drop) {
        Write-Host '[2/4] init-schema --drop（先删全部表再建）...'
        & uv run python -m app.cli init-schema --drop
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[错误] init-schema --drop 失败：请检查 MySQL 服务与 DATABASE_URL（scripts/init_db.sql）。' -ForegroundColor Red
            exit 2
        }
    } else {
        Write-Host '[2/4] alembic upgrade head ...'
        & uv run alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[提示] alembic 迁移失败（可能暂无迁移脚本），回退到 init-schema（按 ORM 元数据建表）。' -ForegroundColor Yellow
        }
        & uv run python -m app.cli init-schema
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[错误] init-schema 失败：请检查 MySQL 服务与 DATABASE_URL（scripts/init_db.sql）。' -ForegroundColor Red
            exit 2
        }
    }

    if ($Seed) {
        Write-Host '[3/4] seed --reset --demo ...'
        & $SeedScript
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[错误] seed 失败（退出码 $LASTEXITCODE）：处理办法见上文提示。" -ForegroundColor Red
            exit $LASTEXITCODE
        }
    } else {
        Write-Host '[3/4] 跳过 seed（加 -Seed 可执行 seed --reset --demo）。'
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# 5) 启动后端 / 前端
# ---------------------------------------------------------------------------
# ⚠️ `--reload-dir app`：只监视 app/ 目录。
# 若监视整个 backend/，pytest 在执行期间反复创建/删除临时 SQLite 文件会触发 reload 风暴
# （服务一直在重启 → 接口 000、机器变卡）。测试产物现在由 conftest 统一放到系统临时目录。
$backendCmd = "Set-Location -LiteralPath '$BackendDir'; " +
    "`$env:DATABASE_URL='$DatabaseUrl'; " +
    "`$host.UI.RawUI.WindowTitle='LogiOps backend'; " +
    "uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port $BackendPort"
$frontendCmd = "Set-Location -LiteralPath '$FrontendDir'; " +
    "`$host.UI.RawUI.WindowTitle='LogiOps frontend'; " +
    "corepack pnpm dev --port $FrontendPort"

$started = @()
if (-not $SkipBackend) {
    if ($Background) {
        $job = Start-Job -Name 'logiops-backend' -ScriptBlock {
            param($dir, $cmd) Set-Location -LiteralPath $dir; Invoke-Expression $cmd
        } -ArgumentList $BackendDir, "uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port $BackendPort"
        $started += "后端 job: logiops-backend (id=$($job.Id))"
    } else {
        Start-Process -FilePath 'pwsh' -WorkingDirectory $RepoRoot `
            -ArgumentList "-NoExit -Command `"$backendCmd`"" | Out-Null
        $started += "后端新窗口: http://127.0.0.1:$BackendPort/docs"
    }
}
if (-not $SkipFrontend) {
    if ($Background) {
        $job = Start-Job -Name 'logiops-frontend' -ScriptBlock {
            param($dir, $cmd) Set-Location -LiteralPath $dir; Invoke-Expression $cmd
        } -ArgumentList $FrontendDir, "corepack pnpm dev --port $FrontendPort"
        $started += "前端 job: logiops-frontend (id=$($job.Id))"
    } else {
        Start-Process -FilePath 'pwsh' -WorkingDirectory $RepoRoot `
            -ArgumentList "-NoExit -Command `"$frontendCmd`"" | Out-Null
        $started += "前端新窗口: http://127.0.0.1:$FrontendPort"
    }
}

Write-Host '[4/4] 已启动：'
foreach ($item in $started) { Write-Host "      - $item" -ForegroundColor Green }
Write-Host ''
Write-Host '常用地址：'
Write-Host "  后端 OpenAPI   http://127.0.0.1:$BackendPort/docs"
Write-Host "  健康检查       http://127.0.0.1:$BackendPort/healthz"
Write-Host "  前端           http://127.0.0.1:$FrontendPort"
Write-Host ''
Write-Host '演示控制（需登录 + X-Workspace-Id）：'
Write-Host "  POST /api/v1/demo/actions/reset        翻车 3 秒恢复（重建 seed）"
Write-Host "  POST /api/v1/demo/actions/tick         推进业务时钟"
Write-Host "  POST /api/v1/demo/actions/advance-to-less  推到送达并自动关闭"
Write-Host ''

if ($Background) {
    Write-Host '后台 job 模式：日志用 Receive-Job -Name logiops-backend/logiops-frontend -Keep；按 Ctrl+C 结束并停止 job。' -ForegroundColor Yellow
    try {
        while ($true) { Start-Sleep -Seconds 5 }
    } finally {
        Get-Job -Name 'logiops-*' -ErrorAction SilentlyContinue | Stop-Job -PassThru | Remove-Job -Force -ErrorAction SilentlyContinue
        Write-Host '已停止后台 job。'
    }
}
