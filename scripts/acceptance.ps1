<#
.SYNOPSIS
    LogiOps 一键验收（基线 §14.5）：迁移 → seed → pytest → CASE-A 跑两次 → 归一化比对 → 汇总。

.DESCRIPTION
    严格按基线 §14.5 的六步执行：
      1) 起库 + 迁移（alembic upgrade head，失败回退 init-schema）+ seed --reset --demo
      2) pytest（unit/api/ai/integration）并输出覆盖率
      3) TestClient 跑 CASE-A 全链路 → artifacts/acceptance_run1.json
      4) 重置（再 seed 一次）后重跑 → artifacts/acceptance_run2.json
      5) 两次结果归一化（忽略时间戳与自增 id）后比对，不一致 → exit 1
      6) 汇总：用例数 / 覆盖率 / 耗时 / AI 步骤数 / 风险等级

    依赖缺失（app.seed 未实现、AI replay fixtures 缺失、接口/服务层未就绪、数据库连不上）
    一律明确提示并 **exit 2**，不会静默通过。

.PARAMETER Database
    Mysql（默认，路径 A 本机 3306）或 Sqlite（$env:DATABASE_URL 指向 artifacts/acceptance.db）。
    Sqlite 仅用于 MySQL 专用账号还没建好时先把验收链路跑起来。

.PARAMETER Scenario
    传给 seed 的场景名，默认 case-a。

.PARAMETER SkipSeed
    跳过 seed 与两次运行之间的重置（此时两次运行一致性不作为判定依据，会打印警告）。

.PARAMETER SkipTests
    跳过 pytest（只跑 CASE-A 两次比对）。

.PARAMETER IgnoreMissingDeps
    调试用：依赖自检失败时只警告不中断（默认中断并 exit 2）。

.PARAMETER OutDir
    产物目录，默认 artifacts（相对仓库根）。

.PARAMETER Help
    打印帮助并退出。

.EXAMPLE
    pwsh -File scripts/acceptance.ps1 -Help

.EXAMPLE
    pwsh -File scripts/acceptance.ps1

.EXAMPLE
    # MySQL 账号未就绪时先用 sqlite 跑通链路
    pwsh -File scripts/acceptance.ps1 -Database Sqlite -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$Help,
    [ValidateSet('Mysql', 'Sqlite')]
    [string]$Database = 'Mysql',
    [string]$Scenario = 'case-a',
    [switch]$SkipSeed,
    [switch]$SkipTests,
    [switch]$IgnoreMissingDeps,
    [string]$OutDir = 'artifacts'
)

function Show-Help {
    Write-Host ''
    Write-Host 'LogiOps acceptance.ps1 —— 一键验收（基线 §14.5）' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '用法：'
    Write-Host '  pwsh -File scripts/acceptance.ps1 [-Database Mysql|Sqlite] [-Scenario case-a] [-OutDir artifacts]'
    Write-Host '                                     [-SkipSeed] [-SkipTests] [-IgnoreMissingDeps]'
    Write-Host ''
    Write-Host '六步流程（§14.5）：'
    Write-Host '  1) 迁移（alembic upgrade head，失败回退 init-schema）+ seed --reset --demo'
    Write-Host '  2) pytest + 覆盖率'
    Write-Host '  3) CASE-A 全链路 → artifacts/acceptance_run1.json'
    Write-Host '  4) 重置后重跑 → artifacts/acceptance_run2.json'
    Write-Host '  5) 归一化（忽略时间戳与自增 id）比对，不一致 exit 1'
    Write-Host '  6) 汇总用例数/覆盖率/耗时/AI 步骤数/风险等级'
    Write-Host ''
    Write-Host '产物：'
    Write-Host '  artifacts/acceptance_run1.json    第一次全链路原始结果'
    Write-Host '  artifacts/acceptance_run2.json    第二次'
    Write-Host '  artifacts/acceptance_diff.json    归一化差异报告'
    Write-Host '  artifacts/pytest_output.txt       pytest 完整输出'
    Write-Host '  artifacts/junit.xml               用例数（JUnit XML，验收汇总以此为准）'
    Write-Host '  artifacts/coverage.json           pytest-cov 覆盖率数据'
    Write-Host ''
    Write-Host '退出码：0 通过；1 用例失败/链路不符/两次不一致；2 依赖缺失'
    Write-Host ''
    Write-Host '前置：'
    Write-Host '  - MySQL：本机 3306 + scripts/init_db.sql 已执行（路径 A）'
    Write-Host '  - 后端依赖：cd backend; uv sync'
    Write-Host '  - AI_MODE=replay 且 backend/tests/fixtures/ai/*.json 已录制'
    Write-Host ''
}

if ($Help) {
    Show-Help
    exit 0
}

$totalTimer = Get-Date

# 注意：Windows PowerShell 5.1 在 $ErrorActionPreference='Stop' 时会把原生程序（uv/pytest/alembic）
# 的 stderr 升级为终止性错误。这里用 'Continue'，成败一律通过 $LASTEXITCODE 与显式 exit 判定。
$ErrorActionPreference = 'Continue'

function Write-Section([string]$Text) {
    Write-Host ''
    Write-Host "── $Text " -ForegroundColor Cyan
}

function Get-Count([string]$Text, [string]$Pattern) {
    $match = [regex]::Match($Text, $Pattern)
    if ($match.Success) { return [int]$match.Groups[1].Value }
    return 0
}

# ---------------------------------------------------------------------------
# 路径与前置
# ---------------------------------------------------------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$HelperScript = Join-Path $PSScriptRoot 'acceptance_case_a.py'
$ProbeScript = Join-Path $PSScriptRoot 'acceptance_probe.py'
$SeedScript = Join-Path $PSScriptRoot 'seed.ps1'
if ([System.IO.Path]::IsPathRooted($OutDir)) {
    $ArtifactsDir = $OutDir
} else {
    $ArtifactsDir = Join-Path $RepoRoot $OutDir
}
$Run1 = Join-Path $ArtifactsDir 'acceptance_run1.json'
$Run2 = Join-Path $ArtifactsDir 'acceptance_run2.json'
$DiffJson = Join-Path $ArtifactsDir 'acceptance_diff.json'
$PytestLog = Join-Path $ArtifactsDir 'pytest_output.txt'
$JunitXml = Join-Path $ArtifactsDir 'junit.xml'
$CoverageJson = Join-Path $ArtifactsDir 'coverage.json'
$SqliteDb = Join-Path $ArtifactsDir 'acceptance.db'

Write-Host ''
Write-Host '=== LogiOps 一键验收（基线 §14.5）===' -ForegroundColor Cyan
Write-Host "仓库根目录：$RepoRoot"
Write-Host "数据库模式：$Database    产物目录：$ArtifactsDir"

if (-not (Test-Path (Join-Path $BackendDir 'pyproject.toml'))) {
    Write-Host "[exit 2] 找不到 backend/pyproject.toml：$BackendDir" -ForegroundColor Red
    exit 2
}
if (-not (Test-Path $HelperScript)) {
    Write-Host "[exit 2] 找不到 scripts/acceptance_case_a.py（CASE-A 回放器）。" -ForegroundColor Red
    exit 2
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '[exit 2] 未找到 uv（https://docs.astral.sh/uv/）。' -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Path $ArtifactsDir -Force | Out-Null

# 数据库 URL：Sqlite 模式显式切库；Mysql 模式沿用 .env / 配置默认（本机 3306）
if ($Database -eq 'Sqlite') {
    if (Test-Path $SqliteDb) {
        Remove-Item -LiteralPath $SqliteDb -Force
        Write-Host "已删除旧的 sqlite 文件：$SqliteDb"
    }
    $sqlitePath = ($SqliteDb -replace '\\', '/')
    $env:DATABASE_URL = "sqlite+pysqlite:///$sqlitePath"
} else {
    if (-not $env:DATABASE_URL) {
        $env:DATABASE_URL = 'mysql+pymysql://logiops:logiops@127.0.0.1:3306/logiops?charset=utf8mb4'
    }
}
$env:CLOCK_MODE = 'replay'
$env:AI_MODE = 'replay'
if (-not $env:AI_REPLAY_DIR) { $env:AI_REPLAY_DIR = 'tests/fixtures/ai' }

# ---------------------------------------------------------------------------
# 步骤 0：依赖自检（缺依赖 → exit 2，不静默通过）
# ---------------------------------------------------------------------------
$depErrors = @()
Push-Location $BackendDir
try {
    $effectiveUrl = & uv run python $ProbeScript mask-db
    Write-Host "生效 DATABASE_URL：$effectiveUrl"

    & uv run python $ProbeScript check-seed | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $depErrors += 'app.seed 未实现（uv run python -m app.seed --reset --demo 不可用，基线 §13.2）'
    }

    $fixtureDir = Join-Path $BackendDir 'tests\fixtures\ai'
    $fixtureCount = 0
    if (Test-Path $fixtureDir) {
        $fixtureCount = @(Get-ChildItem -Path $fixtureDir -Filter '*.json' -File -ErrorAction SilentlyContinue).Count
    }
    if ($env:AI_MODE -ne 'live' -and $fixtureCount -eq 0) {
        $depErrors += "AI replay fixtures 缺失：$fixtureDir 下没有 *.json（基线 §11.3 / 附录 D-B P4）"
    }

    & uv run python $ProbeScript check-db | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $depErrors += '数据库连接失败：请确认 MySQL 服务（3306）与 scripts/init_db.sql 已执行，或改用 -Database Sqlite'
    }
} finally {
    Pop-Location
}

if ($depErrors.Count -gt 0) {
    Write-Host ''
    Write-Host '[exit 2] 依赖缺失，验收无法开始：' -ForegroundColor Red
    foreach ($item in $depErrors) { Write-Host "  - $item" -ForegroundColor Red }
    Write-Host ''
    Write-Host '处理建议：' -ForegroundColor Yellow
    Write-Host '  1) pwsh -File scripts/seed.ps1           # seed 模块就绪后'
    Write-Host '  2) mysql -uroot -p < scripts/init_db.sql  # 建库建号（本机 MySQL）'
    Write-Host '  3) pwsh -File scripts/acceptance.ps1 -Database Sqlite   # 先用 sqlite 跑通链路'
    Write-Host '  4) 只想看脚本自检：pwsh -File scripts/acceptance.ps1 -Help'
    if (-not $IgnoreMissingDeps) { exit 2 }
    Write-Host '[warn] 已指定 -IgnoreMissingDeps：继续执行（结果可能失败）。' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 步骤 1：迁移 + seed
# ---------------------------------------------------------------------------
Write-Section '[1/6] 迁移 + seed --reset --demo'
$seedOk = $true
Push-Location $BackendDir
try {
    & uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[warn] alembic upgrade head 失败（可能暂无迁移脚本），回退到 init-schema。' -ForegroundColor Yellow
    }
    & uv run python -m app.cli init-schema
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[exit 2] init-schema 失败：数据库不可用或账号未建（scripts/init_db.sql）。' -ForegroundColor Red
        if (-not $IgnoreMissingDeps) { exit 2 }
    }
} finally {
    Pop-Location
}

if ($SkipSeed) {
    Write-Host '[warn] -SkipSeed：跳过 seed 与重置，两次运行一致性不作为判定依据。' -ForegroundColor Yellow
} else {
    & $SeedScript -Scenario $Scenario
    $seedCode = $LASTEXITCODE
    if ($seedCode -ne 0) {
        Write-Host "[exit $seedCode] seed 失败（0 才算成功）。" -ForegroundColor Red
        if (-not $IgnoreMissingDeps) { exit $seedCode }
        $seedOk = $false
    }
}

# ---------------------------------------------------------------------------
# 步骤 2：pytest + 覆盖率
# ---------------------------------------------------------------------------
$pytestOk = $true
$pytestText = ''
$passed = 0
$failed = 0
$errors = 0
$skipped = 0
$coverage = $null
if ($SkipTests) {
    Write-Host '[warn] -SkipTests：跳过 pytest。' -ForegroundColor Yellow
} else {
    Write-Section '[2/6] pytest + 覆盖率'
    # 清掉上一轮产物，避免读到过期数字（用例数与覆盖率都以本次运行为准）
    foreach ($stale in @($JunitXml, $CoverageJson)) {
        if (Test-Path $stale) { Remove-Item -LiteralPath $stale -Force }
    }
    Push-Location $BackendDir
    try {
        # 用例数以 --junitxml 产物为准：Windows PowerShell 5.1 管道捕获原生输出时可能丢掉结尾的
        # "N passed" 汇总行（实测），因此不解析控制台文本。日志仍落盘供人工查看。
        # 说明：pytest-cov 的路径用 `--cov-report=json:<path>`（冒号是它的值语法），
        #       pytest 原生 --junitxml 用等号 `--junitxml=<path>`；两者不要混。
        $pytestOutput = & uv run pytest --cov=app --cov-report=term --cov-report=json:$CoverageJson --junitxml=$JunitXml -q *>&1 | Out-String
        $pytestCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Set-Content -LiteralPath $PytestLog -Value $pytestOutput -Encoding UTF8
    $pytestText = $pytestOutput

    $junitJson = ''
    if (Test-Path $JunitXml) {
        Push-Location $BackendDir
        try {
            $junitJson = & uv run python $ProbeScript junit $JunitXml
        } finally {
            Pop-Location
        }
    }
    if ($junitJson) {
        $junit = $junitJson | ConvertFrom-Json
        $passed = [int]$junit.passed
        $failed = [int]$junit.failures
        $errors = [int]$junit.errors
        $skipped = [int]$junit.skipped
    } else {
        # 兜底：junit.xml 没生成时退回文本解析（可能受 PS 5.1 截断影响，故只作保底）
        Write-Host '     [warn] 未生成 junit.xml，退回控制台文本统计（可能不完整）。' -ForegroundColor Yellow
        $passed = Get-Count $pytestText '(\d+) passed'
        $failed = Get-Count $pytestText '(\d+) failed'
        $errors = Get-Count $pytestText '(\d+) error'
    }
    if ($pytestCode -ne 0 -or $failed -gt 0 -or $errors -gt 0) {
        $pytestOk = $false
        Write-Host "[fail] pytest 未通过：passed=$passed failed=$failed error=$errors（完整输出 $PytestLog）" -ForegroundColor Red
    } else {
        Write-Host "[ok] pytest 通过：$passed passed" -ForegroundColor Green
    }
    if (Test-Path $CoverageJson) {
        Push-Location $BackendDir
        try {
            $coverage = & uv run python $ProbeScript coverage (Resolve-Path $CoverageJson).Path
        } finally {
            Pop-Location
        }
        Write-Host "     覆盖率（line）：$coverage%（§14.2 阈值 ≥70%）"
    } else {
        Write-Host '     [warn] 未生成 coverage.json（pytest-cov 未安装？uv sync 后重试）。' -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 步骤 3/4/5：CASE-A 两次 + 归一化比对
# ---------------------------------------------------------------------------
$runOk = $true
$mismatch = $false
Write-Section '[3/6] CASE-A 全链路 run1'
Push-Location $BackendDir
try {
    $run1Output = & uv run python $HelperScript run --out $Run1 --scenario $Scenario *>&1 | Out-String
    $run1Code = $LASTEXITCODE
} finally {
    Pop-Location
}
Set-Content -LiteralPath (Join-Path $ArtifactsDir 'acceptance_run1.log') -Value $run1Output -Encoding UTF8
Write-Host $run1Output
if ($run1Code -ne 0) {
    Write-Host "[exit $run1Code] CASE-A run1 未通过（2=依赖缺失，1=链路不符）。" -ForegroundColor Red
    if (-not $IgnoreMissingDeps) { exit $run1Code }
    $runOk = $false
}

if (-not $SkipSeed -and $runOk) {
    Write-Host '     重置演示数据（保证 run2 从同一初始态开始）...'
    & $SeedScript -Scenario $Scenario
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[exit $LASTEXITCODE] 重置失败，无法保证 run2 可比。" -ForegroundColor Red
        if (-not $IgnoreMissingDeps) { exit $LASTEXITCODE }
        $runOk = $false
    }
}

if ($runOk) {
    Write-Section '[4/6] CASE-A 全链路 run2'
    Push-Location $BackendDir
    try {
        $run2Output = & uv run python $HelperScript run --out $Run2 --scenario $Scenario *>&1 | Out-String
        $run2Code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Set-Content -LiteralPath (Join-Path $ArtifactsDir 'acceptance_run2.log') -Value $run2Output -Encoding UTF8
    Write-Host $run2Output
    if ($run2Code -ne 0) {
        Write-Host "[exit $run2Code] CASE-A run2 未通过。" -ForegroundColor Red
        if (-not $IgnoreMissingDeps) { exit $run2Code }
        $runOk = $false
    }
}

if ($runOk -and (Test-Path $Run1) -and (Test-Path $Run2)) {
    Write-Section '[5/6] 归一化比对（忽略时间戳与自增 id）'
    Push-Location $BackendDir
    try {
        $cmpOutput = & uv run python $HelperScript compare $Run1 $Run2 --json $DiffJson *>&1 | Out-String
        $cmpCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Write-Host $cmpOutput
    if ($cmpCode -ne 0) {
        $mismatch = $true
        Write-Host '[fail] 两次运行不一致（§14.5 要求完全一致）。' -ForegroundColor Red
    }
} else {
    $mismatch = $true
    Write-Host '[warn] 缺少 run1/run2 产物，跳过比对并计为不通过。' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 步骤 6：汇总
# ---------------------------------------------------------------------------
Write-Section '[6/6] 验收汇总'

$summaryJson = ''
if (Test-Path $Run1) {
    Push-Location $BackendDir
    try {
        $summaryJson = & uv run python $ProbeScript summary $Run1
    } finally {
        Pop-Location
    }
}
$summary = $null
if ($summaryJson) { $summary = $summaryJson | ConvertFrom-Json }

$elapsed = [math]::Round(((Get-Date) - $totalTimer).TotalSeconds, 1)
$overall = 0
if (-not $pytestOk -or $mismatch -or -not $runOk -or -not $seedOk) { $overall = 1 }

$riskLevel = '低'
if ($overall -ne 0) { $riskLevel = '高（存在失败项）' }
elseif ($null -ne $coverage -and [double]$coverage -lt 70) { $riskLevel = '中（覆盖率低于 §14.2 的 70%）' }

Write-Host ''
Write-Host '┌─ 验收结果 ─────────────────────────────────────────────' -ForegroundColor Cyan
Write-Host ("│ 用例数（pytest）      : {0} passed / {1} failed / {2} error / {3} skipped" -f $passed, $failed, $errors, $skipped)
Write-Host ("│ 覆盖率（line）        : {0}" -f $(if ($null -eq $coverage) { 'n/a（-SkipTests 或 pytest-cov 缺失）' } else { "$coverage%" }))
Write-Host ("│ 总耗时                : {0}s" -f $elapsed)
if ($null -ne $summary) {
    Write-Host ("│ AI 工具步骤数         : {0}" -f $summary.steps)
    Write-Host ("│ CASE-A 规则定级       : {0}" -f $summary.risk_level)
    Write-Host ("│ 异常终态              : {0} / {1}" -f $summary.final_status, $summary.final_level)
    Write-Host ("│ 审批执行              : {0} / {1}" -f $summary.approvals_executed, $summary.approvals_total)
} else {
    Write-Host '│ AI 工具步骤数         : n/a（无 run1 产物）'
}
Write-Host ("│ 两次运行一致性        : {0}" -f $(if ($mismatch) { '不一致 / 未验证' } else { '一致（归一化后完全相同）' }))
Write-Host ("│ 风险等级              : {0}" -f $riskLevel)
Write-Host '└────────────────────────────────────────────────────────' -ForegroundColor Cyan
Write-Host ''
Write-Host "产物目录：$ArtifactsDir" -ForegroundColor Green

if ($overall -eq 0) {
    Write-Host '验收通过（基线 §14.5 六步全绿）。' -ForegroundColor Green
} else {
    Write-Host '验收未通过：见上文 fail/warn 与产出的 JSON。' -ForegroundColor Red
}
exit $overall
