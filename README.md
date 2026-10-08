<div align="center">

# LogiOps · 物流异常协同平台

**系统负责发现与汇总事实，规则负责定级，大模型负责理解与起草，人负责决策，后端负责执行。**

[![CI](https://github.com/OWNER/logiops/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/logiops/actions/workflows/ci.yml)

<!-- ↑ CI 徽章占位：把 URL 里的 OWNER 换成你的 GitHub 账号（仓库名 logiops），工作流见 .github/workflows/ci.yml -->

FastAPI · SQLAlchemy 2.0 · MySQL 8 · Alembic · Vue 3.5 · Vite 6 · Element Plus · LLM（OpenAI 兼容，默认离线回放）

</div>

> ⚠️ **本项目所有数据均为虚构**：客户名、联系人、电话（`138****0001`）、车牌（`津A·12345`）、订单号、轨迹、承运商消息、知识库文档全部由 `seed` 按固定随机种子 `20260930` 生成，仅用于演示与测试，**不含任何真实企业或个人信息**。
> 默认 `AI_MODE=replay`（读取录制好的 fixtures 离线回放），不联网、不调用外部模型、不产生费用。

---

## 1. 这是什么

一票运输异常，跟单员通常要在 4~5 个系统之间来回核对信息才能做出判断。LogiOps 把这套流程收进一个平台，并且**刻意把 LLM 关进可信边界**：

| 面试官在看的点 | 本项目的答案 |
|---|---|
| 模糊业务问题能否拆成可执行规格 | 订单/异常双状态机 + SLA 计算式 + 风险评分表 + 22 张表字段级数据字典 + 接口全清单（`docs/00-项目基线-MVP.md` §7–§10） |
| LLM 边界怎么划 | AI **全只读**（7 个只读 Tool；**整个 `app/ai/` 包禁止 import Repository/Session 类型**，静态测试覆盖全包）；**等级由规则算**，LLM 只给解释与建议；所有写意图 → 审批单 → 人批准 → 后端执行器（§11） |
| AI 功能可测/可复现/可审计 | `AI_MODE=replay` + 固定基准日 + ReplayClock + 有界循环（**上限 14 步/90s，实测 8 步**）+ Pydantic schema 校验 + 事实比对 + 每次调用落库留痕（§11/§13） |
| 工程洁癖 | 分层硬约束、Alembic 迁移、幂等 seed、乐观锁（409）、多租户越权隔离（404）、一键启动、一键验收（§6/§14） |
| 诚实说明取舍 | 15 条 ADR 含被否方案（LangGraph、向量库、MQ、SSE…）：`docs/04-决策记录ADR.md`、基线附录 A/C |

> ## ⚠️ 当前实现状态（2026-10，以本块为准）
>
> 项目拥有者决定先**停掉两项能力、保留代码**，完整方案与剩余工作见 [`docs/08-时间与延误口径简化方案.md`](docs/08-时间与延误口径简化方案.md)：
>
> | 能力 | 运行状态 | 说明 |
> |---|---|---|
> | **AI（T1 解析 / T2 分析 / T3 草稿）** | ⛔ **已停用**（`AI_ENABLED=false`） | 不调用大模型、不产出建议；界面显示「AI 已停用（待重构）」并隐藏分析按钮。代码保留，后续重新构造 |
> | **ETA 速度模拟** | ⛔ **已停用**（`ETA_ENABLED=false`） | 不再按车速/里程推算到达时间；界面已无「当前 ETA / 首次 ETA / 速度」 |
> | **延误** | ✅ **按「预计到达时间」判定**（口径 2026-10-08） | 在订单上登记 **预计到达时间**（`PATCH /orders/{id}` 的 `planned_delivery_at`）那一刻就判：`预计到达 − 承诺送达` 与 SLA 规则比，**超了才自动建延误异常单**（`detection_rule=DELIVERED_BREACH`），不超不建单；**没登记预计到达时间就不判**。在途不按预测 ETA 报延误。旧口径（送达时按 `实际送达 − 承诺送达` 判定）已下线，「修正实际送达时间」只改订单事实、不再影响延误 |
> | **风险等级** | ✅ 一张单只受一个问题影响 | 车辆故障单 = 车辆故障(1) + 客户等级（VIP 1 / SVIP 2），**无 SLA 影响**；延误单 = 延误档位(1/2/3) + 客户等级，封顶 4（不再重复加"违约 1"） |
> | **异常状态** | ✅ 收敛为 **4 个** | 待确认 → 处理中 → 已解决 → 已关闭（旧的 `CONFIRMING` 并入处理中、`ANALYZING` 随 AI 停用废弃；`DETECTED → PROCESSING` 由人点「确认异常」触发，保留"机器提议、人确认"） |
> | **时钟** | ✅ 默认**真实时间**（`CLOCK_MODE=system`），可**运行时切换** | 全系统直接用系统 `now()`，不再有"业务时间停在基准日"这回事；演示页有「真实时间 / 虚拟时钟」开关（`POST /demo/actions/set-clock-mode`，与顶部 AI 模式开关同一机制：立即生效、不写库、重启后回到 `.env`）。切到虚拟时钟后 `ReplayClock` + `tick`/`set-clock` 照旧可用；真实时间模式下这三个接口返回 409 `DEMO_CLOCK_DISABLED`。单元测试与一键验收仍固定 `replay` 保证确定性 |
> | 承诺到达时间 | ✅ 保留 | 仍由 SLA 规则算：发车时间 + 规则 `deadline_offset_hours`（匹配顺序 客户 → 等级 → 默认） |
>
> **因此下文凡提到「AI 分析 / 有界循环 ≤14 步 / ETA 三算法 / AI 事实校验 / 限流 / ReplayClock 固定基准日」的段落，指的是"保留在代码中、当前运行时不启用"的能力**——它们仍是设计与实现的组成部分，但演示与验收时不会执行。
> 当前可完整演示的闭环：**建单 → 派车（车-司机强绑定）→ 录轨迹 → 自动检测建车辆故障异常 → 确认/处置/车辆修复 → 送达 →（超时则自动建延误单）→ 修正实际送达 → 关闭 → 审计可查**。

**5 分钟演示入口**：打开 CASE-A（`SO20260930021`，天津→上海 VIP 单）→ AI 分析 → 录入承运商消息 → 逐条审批 → 执行 → tick 推进到送达与自动关闭 → 审计可查。逐分钟台词见 [`docs/06-演示脚本.md`](docs/06-演示脚本.md)。

---

## 2. 架构一页图

```text
Vue3 SPA
   │  REST + Bearer JWT + X-Workspace-Id
   ↓
FastAPI Router（薄：鉴权 → 调 Service → 序列化）
   ├─ 业务 Service ────────┐
   ├─ 异常 Service（状态机）│
   └─ AI Orchestrator ─────┤
            │ 只读 Tool     │
            ↓              ↓
        Tool 层 ──────→ Service 层 → Repository 层 → MySQL
```

**硬约束**（写在代码 review 与 CI 检查里，基线 §6）：

1. Router 不写业务逻辑、不直接访问 Repository，只做鉴权/参数/编排；
2. Repository 不写业务判断，只做查询与持久化；
3. AI 层只有 `app/ai/tools/` 的**只读**工具能触达 Service，AI 模块禁止 import Repository 与 Session；
4. 所有写操作只能由 `app/services/` 的执行器完成，且必须携带 `actor`（用户）与 `source`（MANUAL / APPROVED_AI / SYSTEM）；
5. 状态变更只能走 `services/state_machine.py` 的 `transition()`，禁止直接赋值 `status`。

---

## 3. 5 分钟跑起来

### 3.1 环境（本机实测，基线附录 D）

| 依赖 | 本机实测 | 说明 |
|---|---|---|
| Windows 11 + PowerShell | Windows PowerShell **5.1**（`pwsh` 7 未安装） | 脚本同时兼容 5.1 与 7；命令里的 `pwsh` 可换成 `powershell` |
| Python | 3.12.10 | 后端要求 `>=3.12` |
| uv | 0.12.17 | 依赖与虚拟环境（`backend/.venv`） |
| Node / corepack | v24.19.0 / 0.35.0 | 前端用 `corepack pnpm`，**不需要全局装 pnpm** |
| MySQL | 8.0.46，服务 `MySQL80` 跑在 3306 | 路径 A 默认 |
| Docker | **未安装**（无 WSL 发行版） | 路径 B 与镜像构建未实测，见 §8 |

### 3.2 路径 A：本机 MySQL（默认，不需要 Docker）

```powershell
# ① 一次性：建库建号（需要 MySQL root 密码）
mysql -uroot -p < scripts/init_db.sql
#    验收：能连上专用账号（应用不要用 root）
mysql -ulogiops -plogiops -h127.0.0.1 logiops -e "select 1"

# ② 一键：迁移 + seed + 起后端(8000) + 起前端(5173)，各开一个窗口
pwsh -File scripts/dev.ps1 -Seed

# ③ 打开
#    前端        http://127.0.0.1:5173      （登录：owner@logiops.dev / Demo@12345，4 个角色见 §13.2）
#    后端 OpenAPI http://127.0.0.1:8000/docs
#    健康检查     http://127.0.0.1:8000/healthz
```

不想用一键脚本，就按老办法手动来（等价命令）：

```powershell
cd backend
uv sync
uv run alembic upgrade head          # 迁移
uv run python -m app.cli init-schema # 兜底建表 + MySQL FULLTEXT(ngram) 索引
uv run python -m app.seed --reset --demo
uv run uvicorn app.main:app --reload # http://127.0.0.1:8000/docs

cd frontend
corepack pnpm install
corepack pnpm dev                    # http://127.0.0.1:5173
```

> **Demo 时钟提示**：`python -m app.seed --reset --demo` 是独立进程，它会把业务时钟归零到 `DEMO_BASE_DATE`
> 并把基准写入 `system_setting`（`demo.base_date` / `demo.clock_offset_minutes` / `demo.scenario`），
> 但**不会**重置已经在运行的 uvicorn 进程内存里的 `ReplayClock`。
> 演示/联调前请用 `POST /api/v1/demo/actions/reset`（它在 uvicorn 进程内先重置时钟再重建 seed，进程内自洽）
> 或直接重启 uvicorn；否则库里 CASE-A 的时间线与 `/demo/state` 会差一个 offset。

### 3.3 路径 B：Docker Compose（可选，本机未安装 Docker，未实测）

```powershell
docker compose up -d mysql                       # MySQL 8.0 映射到宿主机 3307
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.seed --reset --demo
uv run uvicorn app.main:app --reload

cd frontend
corepack pnpm install
corepack pnpm dev
```

也可以整栈容器化（`mysql + backend + frontend`，前端 nginx 把 `/api` 代理到 backend）：

```powershell
docker compose up -d --build      # 前端 http://localhost:5173 ，后端 http://localhost:8000/docs
docker compose down               # 加 -v 会连数据卷一起删
```

> **两条路径只差一个 `DATABASE_URL`**：本机 `127.0.0.1:3306` / 容器 `127.0.0.1:3307`（容器内为 `mysql:3306`），业务代码零差异（基线 §5.1）。`scripts/dev.ps1 -WithDocker` 即走路径 B。

---

## 4. 一键脚本（`scripts/`，全部可从仓库根目录执行）

| 脚本 | 作用 | 常用命令 |
|---|---|---|
| [`scripts/dev.ps1`](scripts/dev.ps1) | 一键起后端+前端（默认路径 A） | `pwsh -File scripts/dev.ps1 -Seed`、`-Drop`（清库重建）、`-WithDocker`、`-Background`、`-Help` |
| [`scripts/seed.ps1`](scripts/seed.ps1) | 生成/重置演示数据（§13.2 固定种子） | `pwsh -File scripts/seed.ps1 -InitSchema -Scenario case-a`、`-Help` |
| [`scripts/acceptance.ps1`](scripts/acceptance.ps1) | 一键验收（基线 §14.5 六步） | `pwsh -File scripts/acceptance.ps1`、`-Database Sqlite`、`-SkipTests`、`-Help` |
| [`scripts/init_db.sql`](scripts/init_db.sql) | 建 `logiops`/`logiops_test` 与专用账号 | `mysql -uroot -p < scripts/init_db.sql` |

`acceptance.ps1` 的六步（对应 §14.5）：

```text
1) 迁移（alembic upgrade head，失败回退 init-schema）+ seed --reset --demo
2) pytest + 覆盖率（artifacts/pytest_output.txt、artifacts/junit.xml、artifacts/coverage.json）
3) TestClient 跑 CASE-A 全链路 → artifacts/acceptance_run1.json
4) 重置后重跑 → artifacts/acceptance_run2.json
5) 归一化（忽略时间戳与自增 id）比对 → 不一致 exit 1（差异报告 artifacts/acceptance_diff.json）
6) 汇总：用例数 / 覆盖率 / 耗时 / AI 步骤数 / 风险等级
退出码：0 通过；1 用例失败或两次不一致；2 依赖缺失（seed / fixtures / 接口 / 数据库）
```

---

## 5. 测试与验收

```powershell
cd backend
uv run pytest -q                                     # 全部（tests/unit + api + ai + integration）
uv run pytest tests/unit -q                          # 纯函数：状态机/SLA/风险/ETA/脱敏
uv run pytest --cov=app --cov-report=term-missing    # 覆盖率（§14.2：整体 line ≥70%）
uv run ruff check .                                  # lint
uv run mypy app                                      # 核心模块类型检查

# 一键验收（本机 Windows 全流程，跑两次并 diff；依赖缺失时 exit 2）
pwsh -File scripts/acceptance.ps1
# MySQL 专用账号还没建好时可先用 sqlite 跑通链路（MySQL 专有的 ngram 全文索引会退化）
pwsh -File scripts/acceptance.ps1 -Database Sqlite -SkipTests
```

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）：`ubuntu-latest` + MySQL 8.0 service container（带 health check），Python 3.12 + uv → `ruff check` → `alembic upgrade head` → `pytest --cov`；前端单独 job 走 `corepack pnpm install` + `vue-tsc` + `vite build`。

---

## 6. 目录结构

```text
logiops/
├─ README.md                     # 本文件
├─ docker-compose.yml            # mysql / backend / frontend（路径 B，本机未实测）
├─ .env.example                  # 全部环境变量（§5.2）
├─ .github/workflows/ci.yml      # CI：lint + alembic + pytest（MySQL service）+ 前端构建
├─ .vscode/                      # ruff / Prettier / Vue 推荐配置
├─ docs/
│  ├─ 00-项目基线-MVP.md          # 唯一真相：数据字典/接口/AI 契约/排期/验收（§1–§17 + 附录）
│  ├─ 01-数据字典.md … 06-演示脚本.md
├─ backend/
│  ├─ Dockerfile                 # 多阶段（uv 依赖层 + slim 运行层）
│  ├─ pyproject.toml / uv.lock   # 依赖锁定
│  ├─ alembic/                   # 迁移（versions/ 下为首个迁移）
│  ├─ app/                       # core / db / models / schemas / repositories / services / api / ai / knowledge
│  ├─ tests/                     # unit / api / ai / integration / fixtures
│  └─ seed/                      # 固定 seed 数据生成器 + 脚本化案例
├─ frontend/
│  ├─ Dockerfile                 # node:24-alpine + corepack pnpm 构建 → nginx
│  └─ src/                       # api / views / components / stores / router / utils / types
└─ scripts/
   ├─ dev.ps1 / seed.ps1 / acceptance.ps1
   ├─ acceptance_case_a.py       # CASE-A 全链路回放 + 归一化比对
   ├─ acceptance_probe.py        # 验收用的小探针（避免 PS 5.1 引号/stderr 陷阱）
   └─ init_db.sql
```

---

## 7. 有意省略清单（范围冻结，基线 §3.2）

以下**明确不做**，不是"没来得及"：

- 真实 GPS / 企业微信 / 短信 / 邮件接入（通知为 `SENT_MOCK` 模拟发送）；
- 路径规划、自动调度算法、费用结算；
- 多异常类型产品化（仅预留枚举，不铺开）；
- MQ / 微服务 / K8s（事件同步触发 + 可推进时钟，保证可复现）；
- 高并发压测与生产级性能优化（仅本地性能基线 §14.4）；
- 向量库（默认关闭，检索接口保留，P2 可换 Qdrant）；
- ECharts 大屏、Excel 导出、i18n、SSO/OAuth、refresh token；
- 软删除回收站、异常 reopen、邮件邀请成员、移动端适配。

---

## 8. 已知限制与"尚未验证"（诚实声明）

| 项 | 状态 |
|---|---|
| `docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile` | **未实测**：本机未安装 Docker。只做了 YAML 语法自检：`cd backend; uv run python -c "import yaml;yaml.safe_load(open(r'..\docker-compose.yml',encoding='utf-8'));print('yaml ok')"` |
| `.github/workflows/ci.yml` | **未实测**：本机没有 GitHub runner；本地等价命令已逐条跑过（ruff / alembic / pytest / corepack pnpm build） |
| `pwsh`（PowerShell 7） | 本机未安装；脚本已用 Windows PowerShell **5.1** 实测 `-Help` 与建表流程，并兼容 7 |
| `.ps1` 文件编码 | **必须保存为 UTF-8 with BOM**：Windows PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK 解析，中文字符串可能吞掉后面的 `]`/`'` 导致语法错误（`scripts/*.ps1` 已带 BOM） |
| MySQL 全文检索 | `FULLTEXT ... WITH PARSER ngram` 仅 MySQL 8 支持；`-Database Sqlite` 模式自动退化为 LIKE 兜底 |
| AI `live` 模式 | 需要 `LLM_API_KEY`；默认 `replay` 离线可演示，成本 0 |
| 独立进程 `app.seed` 与运行中的 uvicorn | seed 只能重置自己进程的业务时钟；不影响已运行 uvicorn 的内存 `ReplayClock`。演示前用 `POST /api/v1/demo/actions/reset` 或重启 uvicorn（见 §3.2 提示） |

---

## 9. 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/00-项目基线-MVP.md`](docs/00-项目基线-MVP.md) | **唯一真相**：决策总览、范围冻结、技术栈、仓库结构、数据字典（22 表）、业务规则、权限矩阵、接口契约、AI 契约（三张设计表）、测试与验收、排期、Demo 脚本、ADR、环境实测 |
| [`docs/01-数据字典.md`](docs/01-数据字典.md) | 表清单与枚举要点 + 指向基线 §7 + 可执行校验命令 |
| [`docs/02-接口契约.md`](docs/02-接口契约.md) | 通用约定、错误码、接口分组 + 指向 §10 + OpenAPI 导出命令 |
| [`docs/03-AI契约与三张设计表.md`](docs/03-AI契约与三张设计表.md) | AI 边界、有界循环、HITL、知识检索 + 指向 §11 + 校验命令 |
| [`docs/04-决策记录ADR.md`](docs/04-决策记录ADR.md) | 15 条决策与被否方案 + 指向附录 A/C |
| [`docs/05-验收报告.md`](docs/05-验收报告.md) | 待填实测表格（用例数/覆盖率/两次 diff/性能/风险等级） |
| [`docs/06-演示脚本.md`](docs/06-演示脚本.md) | 5 分钟逐分钟台词、演示前检查清单、翻车恢复 |

---

<div align="center">

**这个项目的价值不是"用了大模型"，而是"把 LLM 关进了可信的边界里"。**

</div>
