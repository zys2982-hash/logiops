<#
.SYNOPSIS
    LogiOps 演示数据生成（seed --reset --demo，幂等可重建）。

.DESCRIPTION
    封装基线 §13.2 的 seed 命令，默认执行：
        uv run python -m app.seed --reset --demo
    （在 backend/ 下执行；固定随机种子 20260930 + 固定基准日 2026-09-30，重复执行结果一致）

    依赖：backend/app/seed/ 由后端实现。若模块不存在，脚本明确报错并 exit 2，不会静默通过。

.PARAMETER InitSchema
    先执行 uv run python -m app.cli init-schema（保证表与 FULLTEXT 索引存在）。

.PARAMETER Drop
    与 -InitSchema 一起使用：先删表再建表（清空数据）。

.PARAMETER KeepExisting
    不传 --reset（默认传，表示清空后重建）。

.PARAMETER NoDemo
    不传 --demo（默认传，生成 §13.2 的演示数据 + §13.3 的 5 个脚本化案例）。

.PARAMETER Scenario
    可选，透传 --scenario <name>（例如 case-a）。不传则不追加该参数。

.PARAMETER WithDocker
    使用 docker compose 的 MySQL 3307（路径 B）。默认路径 A：本机 3306。

.PARAMETER Help
    打印帮助并退出。

.EXAMPLE
    pwsh -File scripts/seed.ps1 -Help

.EXAMPLE
    pwsh -File scripts/seed.ps1 -InitSchema -Scenario case-a
#>
[CmdletBinding()]
param(
    [switch]$Help,
    [switch]$InitSchema,
    [switch]$Drop,
    [switch]$KeepExisting,
    [switch]$NoDemo,
    [string]$Scenario = '',
    [switch]$WithDocker
)

# 注意：Windows PowerShell 5.1 在 $ErrorActionPreference='Stop' 时会把原生程序（uv/pytest/alembic）
# 的 stderr 升级为终止性错误。这里用 'Continue'，失败一律通过 $LASTEXITCODE 与显式 exit 判定。
$ErrorActionPreference = 'Continue'

function Show-Help {
    Write-Host ''
    Write-Host 'LogiOps seed.ps1 —— 生成/重置演示数据（默认 uv run python -m app.seed --reset --demo）' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '用法：'
    Write-Host '  pwsh -File scripts/seed.ps1 [-InitSchema] [-Drop] [-KeepExisting] [-NoDemo] [-Scenario case-a] [-WithDocker]'
    Write-Host ''
    Write-Host '参数：'
    Write-Host '  -InitSchema     先执行 init-schema（建表 + FULLTEXT ngram 索引）'
    Write-Host '  -Drop           配合 -InitSchema：先删表（会清空数据）'
    Write-Host '  -KeepExisting   不传 --reset（默认会清空重建）'
    Write-Host '  -NoDemo         不传 --demo'
    Write-Host '  -Scenario NAME  透传 --scenario NAME（可选）'
    Write-Host '  -WithDocker     用 127.0.0.1:3307（路径 B，本机未装 Docker）'
    Write-Host '  -Help           显示本帮助'
    Write-Host ''
    Write-Host '前置：'
    Write-Host '  - 本机 MySQL 已启动，且已执行 scripts/init_db.sql 建库建号'
    Write-Host '  - 后端依赖已同步：cd backend; uv sync'
    Write-Host ''
    Write-Host '退出码：0 成功；1 seed 执行失败；2 依赖缺失（app.seed 未实现/环境不满足）'
    Write-Host ''
}

if ($Help) {
    Show-Help
    exit 0
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$ProbeScript = Join-Path $PSScriptRoot 'acceptance_probe.py'

if (-not (Test-Path (Join-Path $BackendDir 'pyproject.toml'))) {
    Write-Host "[错误] 找不到 backend/pyproject.toml：$BackendDir" -ForegroundColor Red
    exit 2
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '[错误] 未找到 uv（https://docs.astral.sh/uv/）。' -ForegroundColor Red
    exit 2
}

if ($WithDocker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host '[错误] 未找到 docker；本机实测未安装 Docker，请去掉 -WithDocker 走路径 A。' -ForegroundColor Red
        exit 2
    }
    $env:DATABASE_URL = 'mysql+pymysql://logiops:logiops@127.0.0.1:3307/logiops?charset=utf8mb4'
    Write-Host '数据库：路径 B（docker compose，127.0.0.1:3307）'
} else {
    if ($env:DATABASE_URL) {
        # 已被调用方（如 acceptance.ps1 -Database Sqlite）或用户环境显式指定，沿用
        Write-Host "数据库：沿用 DATABASE_URL = $env:DATABASE_URL"
    } else {
        Write-Host '数据库：路径 A（本机 MySQL 127.0.0.1:3306，配置默认值）'
    }
}
# 时钟：与运行时口径一致用真实时间（seed 出来的演示数据时间戳就是"现在"）；
# 需要可复现的固定基准日时，调用方显式设 CLOCK_MODE=replay（acceptance.ps1 就是这么做的）
if (-not $env:CLOCK_MODE) { $env:CLOCK_MODE = 'system' }
$env:AI_MODE = 'replay'
if (-not $env:AI_REPLAY_DIR) { $env:AI_REPLAY_DIR = 'tests/fixtures/ai' }

$t0 = Get-Date
Push-Location $BackendDir
try {
    # --- 依赖自检：app.seed 是否已实现 -------------------------------------------------
    Write-Host '[1/3] 检查 app.seed 是否可用 ...'
    & uv run python $ProbeScript check-seed | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 依赖缺失：backend/app/seed/ 尚未实现（import app.seed 失败）。' -ForegroundColor Red
        Write-Host '       请等后端交付 seed 模块后重试；验收脚本此时也会以 exit 2 退出。' -ForegroundColor Yellow
        Write-Host '       相关基线：§13.2 Seed 清单、§13.3 脚本化案例。' -ForegroundColor Yellow
        exit 2
    }

    # --- 可选：先建表 -----------------------------------------------------------------
    if ($InitSchema) {
        Write-Host '[2/3] init-schema ...'
        $schemaArgs = @('run', 'python', '-m', 'app.cli', 'init-schema')
        if ($Drop) { $schemaArgs += '--drop' }
        & uv @schemaArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[错误] init-schema 失败：请确认 MySQL 服务已启动、已执行 scripts/init_db.sql。' -ForegroundColor Red
            exit 2
        }
    } else {
        Write-Host '[2/3] 跳过 init-schema（加 -InitSchema 可先建表）。'
    }

    # --- seed ------------------------------------------------------------------------
    $seedArgs = @('run', 'python', '-m', 'app.seed')
    if (-not $KeepExisting) { $seedArgs += '--reset' }
    if (-not $NoDemo) { $seedArgs += '--demo' }
    if ($Scenario -ne '') { $seedArgs += @('--scenario', $Scenario) }

    Write-Host ('[3/3] uv ' + ($seedArgs -join ' '))
    & uv @seedArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Host "[错误] seed 执行失败（退出码 $code）。" -ForegroundColor Red
        Write-Host '       排查：MySQL 是否运行（3306）→ 是否执行过 scripts/init_db.sql → 表是否存在（-InitSchema）→ 详见上方错误输出。' -ForegroundColor Yellow
        exit 1
    }
} finally {
    Pop-Location
}

$elapsed = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
Write-Host ''
Write-Host "seed 完成，用时 ${elapsed}s（固定种子 20260930，重复执行结果一致）。" -ForegroundColor Green
Write-Host '下一步：pwsh -File scripts/dev.ps1  或  pwsh -File scripts/acceptance.ps1' -ForegroundColor Green
exit 0
