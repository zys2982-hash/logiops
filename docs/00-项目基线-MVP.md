# LogiOps 物流异常协同平台 —— 可开工项目基线（MVP · 面试作品版）

> 版本：v1.0（定稿，可直接开工）
> 定位：**个人面试演示项目**，不面向真实生产环境
> 基线来源：`LogiOps_产品需求与研发任务框架_MVP.md`（v0.9 愿景稿）+ 本文件补齐的契约层
> 补齐范围：数据字典、枚举与状态机、业务规则、接口契约、AI 契约（含原稿要求的"三张设计表"）、
> 权限矩阵、模拟环境与可复现方案、测试与验收标准、排期、Demo 脚本、决策记录（ADR）

---

## 0. 怎么用这份文档

| 你要做的事 | 看哪一节 |
|---|---|
| 5 分钟搞清所有已定结论 | §1 决策总览 |
| 建库 / 写 Model | §7 数据模型（数据字典） |
| 写业务逻辑 | §8 业务规则（状态机 / SLA / 检测 / ETA / 风险） |
| 写接口 | §10 接口契约 + §9 权限矩阵 |
| 写 AI 模块 | §11 AI 契约（三张设计表在 §11.2） |
| 写前端 | §12 前端设计 |
| 造数据 / 演示 | §13 模拟环境 + §16 Demo 脚本 |
| 写测试 / 验收 | §14 测试与验收 |
| 面试怎么讲 | §19 面试话术 + 附录 A ADR |
| 知道与原稿差在哪 | 附录 C 差异对照 |

**冲突时以本文件为准。** 本文件里没有"待确认"字样的内容，都已替你拍定；附录 B 仅列 5 件不影响开工的个人事务。

---

## 1. 决策总览（一页看完）

| # | 议题 | 结论 | 关键理由（面试可直接说） |
|---|---|---|---|
| D1 | 项目口径 | 面试演示作品，不是生产系统；一切取舍以"能讲清、能跑通、能复现"为准 | 演示项目追求**闭环可信**，不追求吞吐 |
| D2 | 范围 | 冻结为：1 种异常类型（车辆故障致延误）+ 完整闭环 + 确定性规则 + LLM 受限参与 + 人工确认 | 故事讲得完整比功能多更值钱 |
| D3 | 后端 | Python 3.12 + FastAPI + **同步** SQLAlchemy 2.0 + PyMySQL + Alembic + MySQL 8.0 | 瓶颈在 LLM 外部调用而非 DB；`def` 端点自动走线程池，避免 async 踩坑 |
| D4 | 前端 | Vue 3.5 + TypeScript + Vite 6 + Pinia + Element Plus + Axios + dayjs | 主流组合，面试官一眼能读懂 |
| D5 | 仓库 | 单仓（monorepo）：`backend/` `frontend/` `docs/` `scripts/` + docker-compose | 一人开发，减少跨仓协调成本 |
| D6 | 多租户 | 每张业务表带 `workspace_id`，请求头 `X-Workspace-Id` 显式指定，越权统一返回 404 | 显式优于隐式；404 不泄露资源是否存在 |
| D7 | 风险等级 | **确定性规则表**计算（SLA 延误分钟数 + VIP 加权），LLM 不得决定等级 | 原稿的硬要求；也是"AI 不是套壳"的立论 |
| D8 | AI 权限 | **全部 Tool 只读**；写操作（改 ETA / 建跟进 / 存通知）走 `approval` 审批单，由业务 Service 执行 | 一次性解决原稿"写操作须人工确认"与 `create_followup_task` 的矛盾 |
| D9 | RAG | 不引入向量库。知识库 = 仓库内 Markdown → 切分入 MySQL（`WITH PARSER ngram` 全文检索）→ top-k=5，**必须带来源** | 知识条目只有几十条，关键词召回足够且可解释；预留 `Retriever` 接口，后续可切 Qdrant |
| D10 | 前端 AI 交互 | 业务页面 + AI 面板，**轮询** `GET /ai-analyses/{id}` 获取工具步骤，不用 SSE/WebSocket | 轮询 5 行代码搞定，且同步栈不需要事件循环 |
| D11 | 可复现 | 引入 `Clock` 抽象：`AI_MODE=replay`、`DEMO_BASE_DATE=2026-09-30`、固定随机种子 20260930、`POST /demo/actions/tick` 推进时间 | Demo 现场不翻车；测试完全确定性 |
| D12 | LLM 测试 | 集成测试用 FakeLLM（录制 fixtures），真实调用需 `pytest -m live` 显式开启 | 无网络依赖、无费用、可复现 |
| D13 | 时间处理 | 数据库统一存 **UTC**（`DATETIME(3)`），业务口径 Asia/Shanghai，输出 ISO8601 带 `Z` | 一道小工具函数换来防时区 bug 的口碑 |
| D14 | 枚举存储 | `VARCHAR(32)` + 应用层 `StrEnum` 校验，不用 MySQL `ENUM` 类型 | 加枚举值不必改表；迁移友好 |
| D15 | 调度 | 不引入 Celery/APScheduler。检测与 ETA 重算**同步触发**于轨迹写入；定时语义用 `POST /demo/actions/tick` 模拟 | 无 MQ 也能讲清"事件驱动"，且行为可测 |
| D16 | 错误响应 | 成功直接返回资源/分页对象；错误统一 `{"error":{"code","message","details"}}` + 语义化 HTTP 状态码 | 比 `{code,message,data}` 包装更 RESTful，也便于前端统一拦截 |
| D17 | 异常关闭 | `RESOLVED`=影响已消除；`CLOSED`=归档终态（人工关闭或订单送达 24h 后自动）。**不提供 reopen** | 状态机少一个环，就少一半 bug |
| D18 | 通知渠道 | 不接短信/邮件/企微。通知落在 `notification` 表，状态 `DRAFT→APPROVED→SENT_MOCK`，前端提供"复制文本" | 明确写"模拟发送"，比假装接了渠道更诚实 |

---

## 2. 项目定位与演示目标（面试版口径）

### 2.1 一句话定位

> 面向物流运输跟单人员的异常协同平台：**系统负责发现与汇总事实，规则负责定级，大模型负责理解与起草，人负责决策，后端负责执行**。

### 2.2 这个项目要证明的 5 件事（面试官真正在看的）

1. 能把一个模糊业务问题拆成**可执行的状态机 + 规则表 + 接口契约**；
2. 知道 LLM 应该被关在什么边界里（只读 + 结构化输出 + 校验 + 人工审批）；
3. 做出的 AI 功能是**可测、可复现、可审计**的，而不是"跑一次看起来对"；
4. 有工程洁癖：分层、迁移、幂等、乐观锁、越权隔离、一键启动、一键验收；
5. 能诚实说明取舍与被否方案（原稿要求"不以用了 LangGraph/RAG 为成功标准"）。

### 2.3 成功标准（可测，替代原稿 §22 的模糊表述）

| 编号 | 标准 | 判定方式 |
|---|---|---|
| S1 | 一票"车辆故障导致延误"的异常，能在 2 分钟内从发现走到自动关闭 | 跑 §16 Demo 脚本，全程录屏 |
| S2 | 风险等级由规则计算，构造 4 组边界数据得到 LOW/MEDIUM/HIGH/CRITICAL | 单测 `test_risk_rules.py`（分支 100%） |
| S3 | LLM 输出被 schema 校验，注入非法/矛盾输出时系统拒绝执行并降级 | 单测 `test_ai_output_guard.py` |
| S4 | AI 生成的客户通知中，ETA / 订单号 / 客户名与数据库完全一致（防幻觉回归测试） | 单测 + 集成测试断言字段来源 |
| S5 | 越权访问（跨 Workspace、VIEWER 执行写操作）一律失败 | 集成测试 `test_rbac_isolation.py` |
| S6 | 同一 Demo 重复运行，结果（异常 ID、风险等级、建议条数、工具调用步序）完全一致 | `scripts/acceptance.ps1` 跑两次并 diff 输出 JSON |
| S7 | AI 辅助流程相对"手工流程"的耗时对比表有真实数据 | `docs/demo-metrics.md`（§17 协议） |

---

## 3. 范围冻结

### 3.1 做（P0–P2 全部对齐到 4 个阶段，见 §15）

- 认证 / Workspace / RBAC / 成员管理
- 客户、承运商、车辆、司机、订单、运输轨迹、SLA 规则
- 异常检测（规则驱动）、异常主单、承运商消息、异常时间线
- AI：消息解析、上下文聚合、异常分析、处理建议、客户通知草稿
- 人工确认（审批单）、执行器、跟进任务、通知（模拟）、审计日志
- 知识库（Markdown 全文检索，带来源引用）
- Docker Compose 一键启动、Seed 固定数据、一键验收脚本

### 3.2 不做（明确写进 README 的"有意省略"）

真实 GPS/企微/短信/邮件接入；路径规划；自动调度算法；费用结算；多异常类型产品化（仅预留枚举）；
MQ/微服务/K8s；高并发压测与生产级优化；向量库（默认关闭，留接口）；ECharts 大屏；Excel 导出；
i18n；SSO/OAuth；refresh token；软删除回收站；异常 reopen；邮件邀请成员；移动端适配。

---

## 4. 技术栈与版本（冻结）

| 层 | 选型 | 版本 | 备注 |
|---|---|---|---|
| 语言 | Python | 3.12.x | 不用 3.13，避免依赖轮子滞后 |
| Web 框架 | FastAPI | 0.115.x | 自动 OpenAPI，作为验收物 |
| ASGI | uvicorn[standard] | 0.34.x | 单进程 |
| ORM | SQLAlchemy | 2.0.x | **同步** `Session`，`Mapped[]` 声明式 |
| 驱动 | PyMySQL | 1.1.x | 纯 Python，Windows 免编译 |
| 迁移 | Alembic | 1.14.x | `alembic upgrade head` 写入启动文档 |
| 校验 | Pydantic | 2.10.x | + pydantic-settings |
| 数据库 | MySQL | 8.0（docker） | `utf8mb4` / `utf8mb4_0900_ai_ci`；FULLTEXT `WITH PARSER ngram` |
| 认证 | PyJWT | 2.10.x | HS256，access token 12h |
| 密码 | passlib + **bcrypt==4.0.1** | 1.7.4 | 必须锁 bcrypt 4.0.1（4.1+ 与 passlib 有告警/不兼容史） |
| 包管理 | uv | latest | `pyproject.toml` + `uv.lock`；提供 pip 回退说明 |
| Lint/格式 | ruff | 0.8.x | `ruff check` + `ruff format` |
| 类型 | mypy | 1.14.x | 非严格模式，仅核心模块 |
| 测试 | pytest + httpx TestClient | 8.3.x | 覆盖率 pytest-cov |
| LLM 客户端 | openai（OpenAI 兼容） | 1.59.x | `LLM_BASE_URL` 可指向 DeepSeek / 通义兼容端点 |
| 前端 | Vue | 3.5.x | `<script setup>` + TS |
| 构建 | Vite | 6.x | 代理 `/api` → 后端 8000 |
| 状态 | Pinia | 2.3.x | 仅 auth / workspace 两个 store |
| 路由 | Vue Router | 4.5.x | 路由守卫做登录与角色控制 |
| UI | Element Plus | 2.9.x | 中文企业风，不再自造轮子 |
| 请求 | Axios | 1.7.x | 统一拦截器：401 → 登出；error.code → 文案 |
| 时间 | dayjs | 1.11.x | UTC 转 Asia/Shanghai 展示 |
| 前端测试 | Vitest + vue-tsc | 2.1.x / 5.x | 只测 utils 与关键 store |
| E2E（可选） | Playwright | 1.49.x | 1 条主链路，P2 |
| 容器 | Docker Compose | v2 | mysql + backend + frontend 三个服务 |

---

## 5. 仓库结构与本地启动

```text
logiops/
├─ README.md                     # 5 分钟跑起来 + 有意省略清单 + 架构图
├─ docker-compose.yml            # mysql / backend / frontend
├─ .env.example                  # 全部环境变量（§5.2）
├─ docs/
│  ├─ 00-项目基线-MVP.md          # 本文件
│  ├─ 01-数据字典.md              # 由 §7 导出（可自动生成）
│  ├─ 02-接口契约.md              # 由 OpenAPI 导出 + 手写约定
│  ├─ 03-AI契约与三张设计表.md
│  ├─ 04-决策记录ADR.md
│  ├─ 05-验收报告.md
│  ├─ 06-演示脚本.md
│  └─ demo-metrics.md            # 效率对比实测数据
├─ backend/
│  ├─ pyproject.toml
│  ├─ alembic/                   # 迁移脚本
│  ├─ app/
│  │  ├─ main.py                 # FastAPI 实例、路由注册、异常处理器
│  │  ├─ core/                   # config / security / clock / errors / logging / deps
│  │  ├─ db/                     # session / base
│  │  ├─ models/                 # SQLAlchemy 模型（§7 一一对应）
│  │  ├─ schemas/                # Pydantic 出入参
│  │  ├─ repositories/           # 只做数据访问，无业务判断
│  │  ├─ services/               # 业务规则：state_machine/sla/risk/detection/eta/approval
│  │  ├─ api/v1/                 # routers（薄，只做编排与鉴权）
│  │  ├─ ai/                     # llm_client / prompts / tools / agent / retriever / guard
│  │  └─ knowledge/              # 知识库 Markdown 原文
│  ├─ seed/                      # 固定 seed 数据生成器 + 5 个脚本化案例
│  └─ tests/                     # unit / api / ai / integration / fixtures
├─ frontend/
│  ├─ src/{api,views,components,stores,router,utils,types}/
│  └─ vite.config.ts
└─ scripts/
   ├─ dev.ps1                    # Windows 一键起后端+前端
   ├─ seed.ps1
   └─ acceptance.ps1             # §14.5 一键验收
```

### 5.1 启动方式

**路径 A（默认，本机无 Docker 也能跑 —— 见附录 D 环境实测）**

```powershell
# 一次性：建库建号（需要 MySQL root 密码，见附录 D-B 的 P3）
mysql -uroot -p < scripts/init_db.sql          # 创建 logiops / logiops_test 与专用账号 logiops
cd backend; uv sync; uv run alembic upgrade head; uv run python -m seed --reset --demo
uv run uvicorn app.main:app --reload           # http://localhost:8000/docs
cd frontend; corepack enable; pnpm install; pnpm dev   # http://localhost:5173
```

**路径 B（有 Docker 时，用于展示"一键容器化"）**

```powershell
docker compose up -d mysql
cd backend; uv sync; uv run alembic upgrade head; uv run python -m seed --reset --demo
uv run uvicorn app.main:app --reload
cd frontend; pnpm install; pnpm dev
```

两条路径**只差 `DATABASE_URL`**（本机 3306 / 容器 3307），业务代码零差异。
`scripts/dev.ps1` 默认走路径 A，加 `-WithDocker` 走路径 B；`scripts/acceptance.ps1` 只用路径 A（不依赖 Docker）；
CI（GitHub Actions）用 runner 自带 MySQL service container，与本地环境无关。

### 5.2 环境变量清单（`.env.example` 全量）

```ini
APP_ENV=local
API_PREFIX=/api/v1
SECRET_KEY=dev-only-change-me
ACCESS_TOKEN_TTL_MINUTES=720

# 数据库：路径 A = 本机 MySQL 8.0.46（默认）；路径 B = docker compose（3307）
DATABASE_URL=mysql+pymysql://logiops:logiops@127.0.0.1:3306/logiops?charset=utf8mb4
TEST_DATABASE_URL=mysql+pymysql://logiops:logiops@127.0.0.1:3306/logiops_test?charset=utf8mb4

# 演示与可复现
DEMO_BASE_DATE=2026-09-30T09:00:00+08:00
DEMO_RANDOM_SEED=20260930
CLOCK_MODE=replay            # replay | system
AI_MODE=replay               # replay | live   ← 面试默认 replay，绝不现场翻车
AI_REPLAY_DIR=backend/tests/fixtures/ai

# LLM（仅 AI_MODE=live 时生效）
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE_ANALYSIS=0.2
LLM_TEMPERATURE_NOTICE=0.3
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_MAX_INPUT_TOKENS=8000
LLM_MAX_OUTPUT_TOKENS=1500
LLM_DAILY_COST_LIMIT_CNY=20

DETECT_STALL_MINUTES=120
DETECT_DEBOUNCE_MINUTES=30
RISK_VIP_UPGRADE=true
KNOWLEDGE_TOP_K=5
```

---

## 6. 架构与分层

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

**硬约束（写在 README，并有 lint 之外的 review 检查）：**

1. Router 不写业务逻辑、不直接访问 Repository，只做鉴权/参数/编排。
2. Repository 不写业务判断，只做查询与持久化。
3. AI 层**只有 `ai/tools/` 里的只读工具**能触达 Service；AI 模块禁止 import Repository 与 Session。
4. 所有写操作只能由 `services/` 下的执行器完成，且必须携带 `actor`（用户）与 `source`（MANUAL / APPROVED_AI / SYSTEM）。
5. 状态变更只能通过 `services/state_machine.py` 的 `transition()`，禁止直接对 `status` 赋值。
   → 用一个 pytest 用例 + 自定义检查脚本（`backend/scripts/check_layering.py`）守这条线，面试时是可讲的加分项。

---

## 7. 数据模型（数据字典）

### 7.1 通用约定

```text
主键      id BIGINT UNSIGNED AUTO_INCREMENT
租户      workspace_id BIGINT NOT NULL + 索引（user 表除外）
时间      created_at / updated_at DATETIME(3) NOT NULL，存 UTC
审计字段  created_by BIGINT NULL（系统写 NULL）
并发      version INT NOT NULL DEFAULT 1（乐观锁，写操作带 expected_version）
软删除    is_deleted TINYINT(1) NOT NULL DEFAULT 0（仅主数据表；日志类表不设）
枚举      VARCHAR(32)，应用层 StrEnum 校验（见 §7.2）
金额      本项目无金额字段（结算不在范围）
外键      全部建 FK 且不级联删除（RESTRICT），保证演示数据不会莫名消失
字符集    utf8mb4 / utf8mb4_0900_ai_ci
```

### 7.2 全局枚举字典

```text
Role              OWNER | ADMIN | OPERATOR | VIEWER
MemberStatus      ACTIVE | INVITED | REMOVED
CustomerLevel     NORMAL | VIP | SVIP
CarrierStatus     ACTIVE | SUSPENDED
VehicleStatus     IDLE | IN_TRANSIT | REPAIRING | OFFLINE
DriverStatus      AVAILABLE | ON_TRIP | OFF_DUTY
OrderStatus       CREATED | DISPATCHED | IN_TRANSIT | DELIVERED | CLOSED | CANCELLED
TrackingEventType DEPART | ARRIVE | STOP | RESUME | REPAIR_START | REPAIR_END | DELIVER | NOTE
TrackingSource    MOCK | DRIVER | CARRIER | OPERATOR | SYSTEM
SlaScopeType      DEFAULT | CUSTOMER_LEVEL | CUSTOMER
ExceptionType     VEHICLE_BREAKDOWN | DELAY_RISK   (MVP 产品化仅前者)
ExceptionLevel    LOW | MEDIUM | HIGH | CRITICAL
ExceptionStatus   DETECTED | CONFIRMING | ANALYZING | PROCESSING | RESOLVED | CLOSED
ExceptionEventType DETECTED | MESSAGE_ADDED | CONFIRMED | ANALYSIS_REQUESTED | ANALYSIS_READY |
                   ANALYSIS_FAILED | APPROVED | REJECTED | EXECUTED | EXECUTE_FAILED |
                   ETA_UPDATED | STATUS_CHANGED | COMMENT | FOLLOWUP_DONE | CLOSED
ActorType         USER | SYSTEM | AI
MessageChannel    MANUAL_PASTE | MOCK_WECHAT | MOCK_SMS
ParseStatus       PENDING | PARSED | FAILED
AnalysisStatus    PENDING | RUNNING | READY | FAILED
StepStatus        RUNNING | OK | ERROR
ApprovalAction    UPDATE_ETA | CREATE_FOLLOWUP | SAVE_NOTICE | SEND_NOTICE | CLOSE_EXCEPTION
ApprovalStatus    PENDING | APPROVED | REJECTED | EXPIRED | EXECUTED | FAILED
FollowupStatus    OPEN | DONE | CANCELLED
FollowupSource    AI_SUGGESTED | MANUAL | SYSTEM
NotificationChannel MANUAL_COPY | MOCK_EMAIL
NotificationStatus  DRAFT | APPROVED | SENT_MOCK | SKIPPED
DetectionRule     STALL_OVER_THRESHOLD | ETA_BREACH_SLA | MANUAL
```

### 7.3 表清单

```text
T01 user                用户
T02 workspace           工作区
T03 workspace_member    成员与角色
T04 customer            客户
T05 carrier             承运商（原稿缺失实体，本次补上）
T06 vehicle             车辆
T07 driver              司机
T08 order               运输订单
T09 tracking_event      运输轨迹事件（追加写）
T10 sla_rule            SLA 规则
T11 exception_case      异常主单（核心）
T12 exception_event     异常时间线（追加写，即"处理记录"）
T13 carrier_message     承运商消息原文与解析结果
T14 ai_analysis         AI 分析任务与结果
T15 ai_analysis_step    AI 工具调用步骤（前端进度条的数据来源）
T16 approval            人工确认单（AI 建议 → 人批准 → 后端执行）
T17 followup_task       异常跟进任务
T18 notification        客户通知
T19 audit_log           操作审计（追加写）
T20 knowledge_doc       知识库文档
T21 knowledge_chunk     知识库分片（FULLTEXT ngram）
T22 system_setting      运行时设置（基准日、时钟偏移、AI 模式）
```

### 7.4 字段定义

```text
T01 user
id                BIGINT PK
email             VARCHAR(128) NOT NULL UNIQUE
name              VARCHAR(64)  NOT NULL
password_hash     VARCHAR(255) NOT NULL
phone             VARCHAR(32)  NULL              -- 假号，演示用
avatar_url        VARCHAR(255) NULL
status            VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'
last_login_at     DATETIME(3)  NULL
created_at/updated_at DATETIME(3)

T02 workspace
id BIGINT PK | name VARCHAR(64) NOT NULL | code VARCHAR(32) NOT NULL UNIQUE
owner_user_id BIGINT NOT NULL | status VARCHAR(16) DEFAULT 'ACTIVE'
created_at/updated_at

T03 workspace_member
id PK | workspace_id FK | user_id FK | role VARCHAR(16) | status VARCHAR(16) DEFAULT 'ACTIVE'
invited_by BIGINT NULL | joined_at DATETIME(3) NULL
UNIQUE(workspace_id,user_id) | idx(user_id)

T04 customer
id PK | workspace_id FK idx | code VARCHAR(32) NOT NULL | name VARCHAR(128) NOT NULL
level VARCHAR(16) DEFAULT 'NORMAL'
contact_name VARCHAR(64) | contact_phone VARCHAR(32) | contact_email VARCHAR(128) NULL
notify_pref VARCHAR(32) DEFAULT 'MANUAL_COPY' | remark VARCHAR(255) | status VARCHAR(16)
UNIQUE(workspace_id,code) | idx(workspace_id,level,is_deleted)

T05 carrier
id PK | workspace_id FK idx | code VARCHAR(32) | name VARCHAR(128) NOT NULL
contact_name VARCHAR(64) | contact_phone VARCHAR(32) | service_level VARCHAR(16) DEFAULT 'NORMAL'
status VARCHAR(16) DEFAULT 'ACTIVE' | remark VARCHAR(255)
UNIQUE(workspace_id,code)

T06 vehicle
id PK | workspace_id FK idx | plate_no VARCHAR(16) NOT NULL | vehicle_type VARCHAR(32)
capacity_ton DECIMAL(6,2) NULL | carrier_id FK NULL | status VARCHAR(16) DEFAULT 'IDLE'
current_driver_id FK NULL | current_city VARCHAR(64) NULL
remark VARCHAR(255)
UNIQUE(workspace_id,plate_no) | idx(workspace_id,status)

T07 driver
id PK | workspace_id FK idx | name VARCHAR(64) | phone VARCHAR(32) | carrier_id FK NULL
license_no VARCHAR(32) | status VARCHAR(16) DEFAULT 'AVAILABLE'

T08 order
id PK | workspace_id FK idx | order_no VARCHAR(32) NOT NULL
customer_id FK | carrier_id FK NULL | vehicle_id FK NULL | driver_id FK NULL
origin_city VARCHAR(64) | dest_city VARCHAR(64)
cargo_desc VARCHAR(128) NULL | weight_ton DECIMAL(6,2) NULL | distance_km INT NULL
status VARCHAR(16) DEFAULT 'CREATED'
sla_rule_id FK NULL
dispatched_at DATETIME(3) NULL
promised_delivery_at DATETIME(3) NULL      -- SLA 快照（下单/发车时算好，之后不变）
original_eta_at DATETIME(3) NULL           -- 首次 ETA
current_eta_at DATETIME(3) NULL            -- 当前 ETA（随轨迹重算）
delivered_at DATETIME(3) NULL
remark VARCHAR(255)
UNIQUE(workspace_id,order_no) | idx(workspace_id,status) | idx(workspace_id,customer_id)

T09 tracking_event（追加写，无 version/is_deleted）
id PK | workspace_id FK idx | order_id FK idx
event_type VARCHAR(24) | city VARCHAR(64) | address VARCHAR(128) NULL
lat DECIMAL(9,6) NULL | lng DECIMAL(9,6) NULL
occurred_at DATETIME(3) NOT NULL idx
source VARCHAR(16) DEFAULT 'MOCK'
speed_kmh DECIMAL(5,1) NULL | payload_json JSON NULL | created_at

T10 sla_rule
id PK | workspace_id FK idx | name VARCHAR(64)
scope_type VARCHAR(16)                       -- DEFAULT / CUSTOMER_LEVEL / CUSTOMER
scope_value VARCHAR(32) NULL                 -- 'VIP' / customer.code / NULL
deadline_offset_hours INT NOT NULL           -- 承诺到达 = 发车时间 + 该小时数
max_delay_minutes INT NOT NULL DEFAULT 0     -- 允许延迟（超出即违约）
priority INT NOT NULL DEFAULT 100            -- 取匹配项中优先级最高（数值最小）者
action_policy_json JSON NULL                 -- 处理动作偏好（是否强制通知客户等）
description VARCHAR(255) | is_active TINYINT(1) DEFAULT 1
UNIQUE(workspace_id,scope_type,scope_value)

T11 exception_case（核心表）
id PK | workspace_id FK idx | case_no VARCHAR(32) NOT NULL     -- EX20260930001
order_id FK idx | customer_id FK | vehicle_id FK NULL | carrier_id FK NULL
type VARCHAR(32) NOT NULL DEFAULT 'VEHICLE_BREAKDOWN'
level VARCHAR(16) NOT NULL DEFAULT 'MEDIUM'
status VARCHAR(16) NOT NULL DEFAULT 'DETECTED'
detected_by VARCHAR(16) NOT NULL             -- SYSTEM / OPERATOR
detection_rule VARCHAR(32) NULL              -- STALL_OVER_THRESHOLD / ETA_BREACH_SLA / MANUAL
occurred_at DATETIME(3) NOT NULL             -- 异常发生时间（业务时间）
stall_since DATETIME(3) NULL                 -- 开始停滞时间
root_cause_code VARCHAR(32) NULL             -- VEHICLE_BREAKDOWN / TRAFFIC / WEATHER / ...
root_cause_note VARCHAR(255) NULL
impact_summary VARCHAR(255) NULL
promised_delivery_at DATETIME(3) NULL        -- 快照
current_eta_at DATETIME(3) NULL
expected_eta_at DATETIME(3) NULL             -- 重算后的预计到达
sla_delay_minutes INT NULL                   -- expected - promised（可为负）
sla_breached TINYINT(1) NOT NULL DEFAULT 0
risk_score INT NULL                          -- 规则得分
risk_factors_json JSON NULL                  -- [{code,label,weight,detail}] 可解释
assigned_to BIGINT NULL                      -- 处理人
resolved_at DATETIME(3) NULL | closed_at DATETIME(3) NULL
close_reason VARCHAR(32) NULL                -- DELIVERED / INVALID / MANUAL
merged_count INT DEFAULT 0
version INT | created_by BIGINT NULL | created_at/updated_at | is_deleted
UNIQUE(workspace_id,case_no) | idx(workspace_id,status,level) | idx(workspace_id,order_id) |
idx(workspace_id,sla_breached,created_at)

T12 exception_event（追加写）
id PK | workspace_id FK | exception_id FK idx | event_type VARCHAR(24)
actor_type VARCHAR(8) | actor_id BIGINT NULL
from_status VARCHAR(16) NULL | to_status VARCHAR(16) NULL
detail_json JSON NULL | note VARCHAR(500) NULL
occurred_at DATETIME(3) idx

T13 carrier_message
id PK | workspace_id FK | exception_id FK idx | order_id FK idx
channel VARCHAR(16) DEFAULT 'MANUAL_PASTE'
sender_name VARCHAR(64) | sender_role VARCHAR(16) DEFAULT 'CARRIER'
raw_text TEXT NOT NULL | received_at DATETIME(3)
parse_status VARCHAR(16) DEFAULT 'PENDING'
parse_result_json JSON NULL     -- {exception_type,location,status,estimated_recovery_at,confidence,missing[]}
parser_version VARCHAR(32) NULL | parse_error VARCHAR(255) NULL
attachment_urls_json JSON NULL | created_by | created_at

T14 ai_analysis
id PK | workspace_id FK | exception_id FK idx | analysis_no VARCHAR(40) UNIQUE
task_type VARCHAR(32) NOT NULL               -- PARSE_MESSAGE / ANALYZE_EXCEPTION / DRAFT_NOTICE
status VARCHAR(16) DEFAULT 'PENDING'
triggered_by BIGINT | input_hash CHAR(64)    -- 幂等 + 复用判断
model VARCHAR(64) | prompt_version VARCHAR(16)
output_json JSON NULL                        -- §11.2 表2 的 schema
raw_output MEDIUMTEXT NULL                   -- 原始文本，便于排查与复盘
risk_level_calculated VARCHAR(16) NULL       -- 规则算出的等级（与 LLM 输出分离）
error_code VARCHAR(32) NULL | error_message VARCHAR(255) NULL
tokens_in INT NULL | tokens_out INT NULL | latency_ms INT NULL
reused_from_id BIGINT NULL | is_replay TINYINT(1) DEFAULT 0
started_at/finished_at DATETIME(3) | created_at
idx(workspace_id,exception_id,created_at)

T15 ai_analysis_step
id PK | analysis_id FK idx | step_no INT | step_type VARCHAR(16)  -- LLM / TOOL / VALIDATE
tool_name VARCHAR(32) NULL | args_json JSON NULL | result_summary VARCHAR(255) NULL
status VARCHAR(16) | duration_ms INT NULL | error VARCHAR(255) NULL | created_at

T16 approval
id PK | workspace_id FK | exception_id FK idx | analysis_id FK NULL
action_type VARCHAR(24) NOT NULL
target_type VARCHAR(24) NULL | target_id BIGINT NULL
ai_payload_json JSON NOT NULL                -- AI 原始建议（不可改，留痕）
final_payload_json JSON NULL                 -- 人工修改后的实际执行内容
diff_json JSON NULL                          -- 两者差异（前端高亮"人工改了什么"）
status VARCHAR(16) DEFAULT 'PENDING'
decided_by BIGINT NULL | decided_at DATETIME(3) NULL | reject_reason VARCHAR(255) NULL
executed_at DATETIME(3) NULL | execution_result_json JSON NULL
retry_count INT DEFAULT 0 | expires_at DATETIME(3) NULL
created_at/updated_at | version
idx(workspace_id,status) | idx(exception_id,status)

T17 followup_task
id PK | workspace_id FK | exception_id FK idx
title VARCHAR(128) | content VARCHAR(500) NULL
assignee_user_id BIGINT NULL | due_at DATETIME(3) NULL
priority VARCHAR(16) DEFAULT 'NORMAL'
status VARCHAR(16) DEFAULT 'OPEN' | source VARCHAR(16) DEFAULT 'MANUAL'
source_approval_id BIGINT NULL
done_at DATETIME(3) NULL | done_by BIGINT NULL | remark VARCHAR(255)
created_at/updated_at

T18 notification
id PK | workspace_id FK | exception_id FK idx | customer_id FK
channel VARCHAR(16) DEFAULT 'MANUAL_COPY'
subject VARCHAR(128) NULL | content TEXT NOT NULL
ai_draft_content TEXT NULL                   -- AI 原稿留痕
status VARCHAR(16) DEFAULT 'DRAFT'
approved_by BIGINT NULL | approved_at DATETIME(3) NULL | sent_at DATETIME(3) NULL
source_approval_id BIGINT NULL | created_at/updated_at

T19 audit_log（追加写，不可改）
id PK | workspace_id FK idx | actor_type VARCHAR(8) | actor_id BIGINT NULL
action VARCHAR(64) NOT NULL                  -- order.update / exception.resolve / approval.execute ...
resource_type VARCHAR(32) | resource_id BIGINT NULL
before_json JSON NULL | after_json JSON NULL
source VARCHAR(16)                           -- MANUAL / APPROVED_AI / SYSTEM
request_id VARCHAR(36) NULL | ip VARCHAR(45) NULL | user_agent VARCHAR(255) NULL
occurred_at DATETIME(3) idx

T20 knowledge_doc
id PK | workspace_id FK NULL                 -- NULL = 全租户共享知识
doc_code VARCHAR(32) UNIQUE | title VARCHAR(128) | category VARCHAR(32)
version VARCHAR(16) | source_path VARCHAR(255) | status VARCHAR(16) DEFAULT 'ACTIVE'
checksum CHAR(64) | updated_at

T21 knowledge_chunk
id PK | doc_id FK idx | chunk_no INT | section_path VARCHAR(255)
content TEXT NOT NULL | token_estimate INT | checksum CHAR(64)
FULLTEXT KEY ft_content (content) WITH PARSER ngram

T22 system_setting
setting_key VARCHAR(64) PK | setting_value VARCHAR(255) | updated_at
-- 键：demo.base_date / demo.clock_offset_minutes / ai.mode / demo.scenario
```

### 7.5 ER 关系（文字版）

```text
user 1─n workspace_member n─1 workspace 1─n customer/carrier/vehicle/driver/sla_rule
workspace 1─n order ─n─1 customer, ─n─1 carrier, ─n─1 vehicle, ─n─1 driver, ─n─1 sla_rule
order 1─n tracking_event
order 1─n exception_case ─1─1 当前 SLA 快照（冗余字段，不建表）
exception_case 1─n carrier_message / exception_event / ai_analysis / approval / followup_task / notification
ai_analysis 1─n ai_analysis_step；ai_analysis 1─n approval（建议转审批单）
workspace 1─n audit_log；knowledge_doc 1─n knowledge_chunk（workspace 可空=全局）
carrier 1─n vehicle（vehicle.carrier_id）；carrier 1─n driver（driver.carrier_id）
vehicle 1─0..1 driver（vehicle.current_driver_id，固定主驾）
```

**车-司机强绑定（ADR-A18，业务规则）**：车辆在运营时**由其固定主驾驾驶**，因此

- 派车（`PATCH /orders/{id}` 带 `vehicle_id` / `driver_id`）：
  - 车辆已有固定主驾 → `order.driver_id` **必须等于它**，未指定则**自动带入**；换成同承运商的别的司机 → `422`；
  - 车辆未绑定主驾 → 未指定司机时 `422`（"在用车辆必须有固定司机"），指定同承运商司机时**自动建立绑定**（车与司机 1:1）。
- 车辆侧绑定（`POST|PATCH /vehicles`）：主驾必须与车辆同承运商，否则 `422`（`tests/test_vehicle_driver_binding.py`）。
- 前端派车卡片按此设计：**先选承运商 → 选司机 → 车辆自动导入（只读）**；换人入口统一在「车辆」页（改绑定）。

---

## 8. 业务规则（确定性，全部可单测）

> 这一节是"AI 之外的真相来源"。规则全部纯函数化：输入数据 → 输出结论，不碰 Session。

### 8.1 订单状态机

| 起始 | 事件 | 终态 | 触发者 | 副作用 |
|---|---|---|---|---|
| CREATED | 派车（PATCH /orders/{id} 填 vehicle/carrier） | DISPATCHED | OPERATOR+ | 记录 `dispatched_at`，按 SLA 规则算 `promised_delivery_at`、`original_eta_at` |
| DISPATCHED | 首条 DEPART 轨迹 | IN_TRANSIT | SYSTEM | `current_eta_at` 初始化 = original |
| IN_TRANSIT | DELIVER 轨迹 | DELIVERED | SYSTEM/OPERATOR | 记 `delivered_at`；触发关联异常的 resolve 尝试 |
| DELIVERED | 24h 后自动 或 人工 | CLOSED | SYSTEM/OPERATOR | 归档 |
| CREATED / DISPATCHED | 取消 | CANCELLED | ADMIN+ | 未关闭异常一并 CLOSED（reason=ORDER_CANCELLED） |

非法流转一律 `409 STATE_TRANSITION_INVALID`。

### 8.2 异常状态机（6 态）

| 起始 | 事件 / 接口 | 终态 | 允许角色 | 副作用 |
|---|---|---|---|---|
| — | 检测规则命中 或 `POST /exceptions` | DETECTED | SYSTEM / OPERATOR+ | 写主单 + `exception_event(DETECTED)` + 审计 |
| DETECTED | 录入承运商消息 `POST /exceptions/{id}/messages` | CONFIRMING | OPERATOR+ | 写 `carrier_message`，触发 PARSE_MESSAGE，写 `MESSAGE_ADDED` |
| DETECTED | 人工确认信息完整 `POST /exceptions/{id}/confirm` | CONFIRMING | OPERATOR+ | 写 `CONFIRMED` |
| DETECTED | 判定误报 `POST /exceptions/{id}/close {reason:INVALID}` | CLOSED | OPERATOR+ | 终态，`close_reason=INVALID` |
| CONFIRMING | `POST /exceptions/{id}/analyze` | ANALYZING | OPERATOR+ | 创建 `ai_analysis(RUNNING)`，返回 202 + analysis_id |
| ANALYZING | AI 分析 READY（独立事务落库） | PROCESSING | SYSTEM | 写规则等级（覆盖 LLM 的 level）、生成 0..n 条 `approval(PENDING)`，写 `ANALYSIS_READY` |
| ANALYZING | AI 分析 FAILED 或超时 90s | CONFIRMING | SYSTEM | 写 `ANALYSIS_FAILED`，前端提示可重试或手工处理 |
| PROCESSING | 订单 DELIVERED 或 新轨迹恢复且规则判定风险解除 | RESOLVED | SYSTEM | 重算 ETA 与 SLA，写 `ETA_UPDATED` + `STATUS_CHANGED` |
| PROCESSING | 人工 `POST /exceptions/{id}/resolve`（需 note） | RESOLVED | OPERATOR+ | 同上 + 审计 |
| RESOLVED | 订单送达后 24h 自动 或 人工 `POST /exceptions/{id}/close` | CLOSED | SYSTEM / OPERATOR+ | `close_reason=DELIVERED/MANUAL`，写 `CLOSED`；**终态不可逆** |
| 任意非终态 | 强制归档 | CLOSED | ADMIN+ | 必须带 note，审计标记 `FORCED_CLOSE` |

规则细节：

- 同一订单**至多一个未关闭异常**（`DETECTED/CONFIRMING/ANALYZING/PROCESSING/RESOLVED`）；再次命中则合并：`merged_count+1`、刷新 `expected_eta_at/sla_*/level`、写一条 `exception_event`，不新建主单。
- 分析进行中重复点击「AI 分析」：若最新 `ai_analysis` 为 `READY` 且 `input_hash` 未变且 `finished_at` 在 15 分钟内 → 直接复用（`reused_from_id`），返回 200 而非 202；若状态为 `RUNNING` → 409 `AI_ANALYSIS_IN_PROGRESS`。
- 审批单 24h 未决策 → 定时/`tick` 置 `EXPIRED`（不自动执行）。
- `CLOSED` 不提供 reopen（ADR-A11）。
- **订单送达时的收口规则**（实现定稿，勿当 bug 修）：
  - 异常已进入 `PROCESSING` → `RESOLVED`（写 `resolved_at`），24h 后自动 `CLOSED`；
  - 异常仍在 `DETECTED / CONFIRMING / ANALYZING`（尚未人工确认或未走完分析）→ 直接 `CLOSED`，`close_reason=DELIVERED`。
    理由：状态机刻意没有 `CONFIRMING → RESOLVED` 这条边，"只有 PROCESSING 才能 RESOLVED"这条纪律要保留；
    未进入处理阶段的异常，订单送达即归档，同样不会"货到了异常还挂着"。
- **机器提议阶段等级只升不降**：异常处于 `DETECTED / CONFIRMING / ANALYZING` 时，`tick` 的合并刷新
  `refresh_case_impact(allow_downgrade=False)` —— `level / risk_score / risk_factors` 保持，SLA 数字仍按事实刷新。
  风险真解除时由 `PROCESSING → RESOLVED` 或"送达即闭环"收口，避免"系统悄悄把高危降级成中危"。
- **Demo 推进目标的选择是确定性的**：`advance_until_delivered` 优先选"带承运商消息的脚本化案例"（Demo 主案例），
  否则选最近创建的未关闭异常；**不要**用 `created_at asc`（seed 的历史池 created_at 被回填，会选错）。
  已关闭时直接幂等返回 `ALREADY_CLOSED`（`ticks=0`）。

### 8.3 SLA 规则与计算

```text
规则匹配（priority 升序取第一个命中）：
  1) scope_type=CUSTOMER       and scope_value=order.customer.code
  2) scope_type=CUSTOMER_LEVEL and scope_value=customer.level
  3) scope_type=DEFAULT
承诺到达时刻： promised_delivery_at = order.dispatched_at + rule.deadline_offset_hours
违约判定：     sla_delay_minutes = round((expected_eta_at - promised_delivery_at) / 1min)
               sla_breached = sla_delay_minutes > rule.max_delay_minutes
日历口径：     Asia/Shanghai，24×7，不考虑节假日（明确声明，见 ADR-A14）
违约金/赔付：  不在范围
```

Seed 内置规则：`DEFAULT` = 发车后 30h / 允许延迟 30min；`CUSTOMER_LEVEL=VIP` = 发车后 24h / 允许延迟 0min；`CUSTOMER=VIP-01` = 发车后 24h / 允许延迟 0min（更具体者优先）。

### 8.4 异常检测规则（替代原稿含糊的 Step 1）

| 规则码 | 条件 | 说明 |
|---|---|---|
| `STALL_OVER_THRESHOLD` | 订单 `IN_TRANSIT` 且距最后一条位置变化 ≥ `DETECT_STALL_MINUTES`（默认 120） | 判定为疑似车辆故障，`type=VEHICLE_BREAKDOWN`，`detection_rule=STALL_OVER_THRESHOLD` |
| `ETA_BREACH_SLA` | 重算后 `expected_eta_at > promised_delivery_at + max_delay_minutes` | `type=DELAY_RISK`；若已有未关闭异常则合并升级为 `VEHICLE_BREAKDOWN` |
| `MANUAL` | `POST /exceptions` | 运营手工建单，必须填 `type/occurred_at/note` |

- 去抖：同订单、同规则、`DETECT_DEBOUNCE_MINUTES`（默认 30）内不重复生成，仅合并刷新。
- 误报兜底：所有自动创建的异常都停在 `DETECTED`，必须人工（或承运商消息）确认后才进入分析流程——即"机器提议，人确认"。
- 触发时机：`POST /orders/{id}/tracking-events` 写入后**同步**执行"ETA 重算 → 检测"；`POST /demo/actions/tick` 用于演示推进。

### 8.5 ETA 重算规则（可解释，不用 ML）

```text
若 vehicle.status == REPAIRING 且 carrier_message 解析出 estimated_recovery_at：
    resume_at = estimated_recovery_at
否则：
    resume_at = clock.now()

剩余里程 remaining_km = order.distance_km × (1 − 已完成行程比例)
    行程比例按 起点→当前城市 的预设里程表估算（seed 内含城市里程表），无数据时用 0.6 兜底
平均速度 avg_speed_kmh：取最近 2 小时 DEPART/ARRIVE 事件计算，不足则用默认 40
expected_eta_at = resume_at + ceil(remaining_km / avg_speed_kmh) 小时
写回：order.current_eta_at / exception_case.expected_eta_at
eta_method = 'REPAIR_WAIT' | 'MOVING_AVG_SPEED' | 'FALLBACK'
```

必须输出并记录 `eta_method`（可解释性 + 单测断言点）。

### 8.6 风险等级规则（`level` 由这里决定，LLM 无权改）

```text
step1 基础分：按 sla_delay_minutes（无 ETA 时按 0）
   未违约或延误 ≤ 0      → 0
   1 .. 120 min         → 1
   121 .. 360 min       → 2
   > 360 min            → 3
step2 加分：
   customer.level == VIP   → +1   (SVIP → +2)
   type == VEHICLE_BREAKDOWN → +1
   sla_breached            → +1
step3 映射（score, 上限 4）：
   0 → LOW | 1-2 → MEDIUM | 3 → HIGH | 4 → CRITICAL
输出：risk_score、risk_factors_json（逐项列出 code/label/weight/detail，前端直接展示"为什么是高危"）
```

Seed 主案例（`SO20260930021`）：延误 270min → 基础分 2；VIP +1；VEHICLE_BREAKDOWN +1；SLA 违约 +1
→ 合计 5，封顶后 `risk_score=4` → `CRITICAL`。前端列表按 `risk_score desc` 排序，Demo 用它占据榜首。

### 8.7 值的格式化与脱敏

```text
时间输出     ISO8601 UTC（…Z），前端用 dayjs 转 Asia/Shanghai 展示
手机号       mask_phone(): 13800000001 → 138****0001（写 LLM prompt 前必须过）
客户名       允许发送（seed 为虚构名）；工号/内部 ID 不发送
日志/审计    raw_text 与 LLM 原文入库，但 API 响应中 phone 一律脱敏
```
---

## 9. 权限矩阵（RBAC × 多租户）

### 9.1 多租户规则

```text
1) user 不属于任何 workspace（全局表），其余业务表必须有 workspace_id。
2) 每个请求必须确定"当前工作区"：
   - 优先取请求头 X-Workspace-Id
   - 缺省取该用户最早加入且状态 ACTIVE 的 workspace
3) 校验：该 user 在目标 workspace 有 ACTIVE 成员记录，否则 403 PERM_WORKSPACE_NOT_MEMBER。
4) Repository 层所有查询强制附加 workspace_id 过滤（由 BaseRepository 统一注入，业务代码无法绕过）。
5) 跨租户访问他人资源统一返回 404 RESOURCE_NOT_FOUND（不返回 403，避免泄露资源存在性）。
6) 越权访问要写审计日志（action=security.cross_tenant_denied），便于"我考虑过这个攻击面"的讲述。
```

### 9.2 角色 × 操作矩阵

| 操作 | VIEWER | OPERATOR | ADMIN | OWNER |
|---|---|---|---|---|
| 登录 / 查看自己信息 | ✅ | ✅ | ✅ | ✅ |
| 查看 Dashboard / 客户 / 订单 / 车辆 / 轨迹 / SLA | ✅ | ✅ | ✅ | ✅ |
| 查看异常列表与详情 / 时间线 / 审计 | ✅ | ✅ | ✅ | ✅ |
| 创建/修改 客户·承运商·车辆·司机·订单·SLA 规则 | ❌ | ❌ | ✅ | ✅ |
| 录入轨迹 / 录入承运商消息 | ❌ | ✅ | ✅ | ✅ |
| 创建异常 / 确认 / 触发 AI 分析 | ❌ | ✅ | ✅ | ✅ |
| 审批（批准/驳回/修改）AI 建议 | ❌ | ✅ | ✅ | ✅ |
| 执行（批准后由后端执行写操作） | ❌ | ✅ | ✅ | ✅ |
| 跟进任务：创建 / 完成 | ❌ | ✅ | ✅ | ✅ |
| 通知：批准 / 模拟发送 | ❌ | ✅ | ✅ | ✅ |
| 异常 resolve / close（正常路径） | ❌ | ✅ | ✅ | ✅ |
| 异常强制关闭（FORCED_CLOSE） | ❌ | ❌ | ✅ | ✅ |
| 手动建异常并指定等级 | ❌ | ❌ | ✅ | ✅ |
| 成员管理（增删改角色） | ❌ | ❌ | ✅ | ✅ |
| 知识库重建索引 | ❌ | ❌ | ✅ | ✅ |
| 删除 Workspace / 转移 OWNER | ❌ | ❌ | ❌ | ✅ |
| Demo 控制接口（tick / reset） | `APP_ENV=local` 且 ADMIN+ |

实现方式：`core/permissions.py` 中 `ROLE_PERMS: dict[Role, set[Perm]]`，依赖注入 `require(Perm.EXCEPTION_HANDLE)`，
矩阵本身有单测（每个角色 × 每个权限双向断言），面试可现场演示"把矩阵改一行，测试立刻红"。

---

## 10. 接口契约

### 10.1 通用约定（前后端唯一真相，写进 `docs/02-接口契约.md`）

```text
前缀      /api/v1
命名      JSON 字段一律 snake_case（与 Python 一致，前端在 types 层做映射，不做运行时转换）
鉴权      Authorization: Bearer <jwt>；JWT payload = {sub:user_id, email, exp, iat}
工作区    X-Workspace-Id: <id>（可选，缺省用默认工作区）
成功响应  直接返回资源对象或分页对象，不套 code/data
分页      ?page=1&page_size=20（1 起，page_size ≤ 100，默认 20）
          响应 {items:[...], total, page, page_size}
排序      ?sort=-risk_score,created_at（- 表示倒序；白名单字段，非法字段 400）
时间      入参出参均 ISO8601 UTC（2026-09-30T11:05:00Z），前端负责本地化展示
错误响应  HTTP 语义状态码 + {"error":{"code":"STATE_TRANSITION_INVALID","message":"...","details":{}}}
          details 里带机器可读字段（例如 {"from":"ANALYZING","to":"DETECTED"})
幂等      写操作（PATCH / resolve / close / approve）必须带 expected_version，不匹配 → 409 OPTIMISTIC_LOCK_CONFLICT
请求追踪  响应头 X-Request-Id（同时写入审计日志）
限流      演示级：AI 分析接口按用户 1 次/5 秒（429 RATE_LIMITED），其余不限制
OpenAPI  /docs 与 /openapi.json 均可用，导出为 docs/openapi.json 作为交付物
```

### 10.2 错误码表

| code | HTTP | 含义 |
|---|---|---|
| `AUTH_INVALID_CREDENTIALS` | 401 | 账号或密码错误 |
| `AUTH_TOKEN_EXPIRED` | 401 | token 过期 |
| `AUTH_TOKEN_INVALID` | 401 | token 非法 |
| `PERM_DENIED` | 403 | 角色权限不足 |
| `PERM_WORKSPACE_NOT_MEMBER` | 403 | 不属于该工作区 |
| `RESOURCE_NOT_FOUND` | 404 | 资源不存在或跨租户 |
| `VALIDATION_ERROR` | 422 | 参数校验失败（details.fields） |
| `STATE_TRANSITION_INVALID` | 409 | 状态机不允许的流转 |
| `OPTIMISTIC_LOCK_CONFLICT` | 409 | version 不匹配（有人先改了） |
| `DUPLICATE_ENTITY` | 409 | 唯一键冲突（如 order_no 重复） |
| `OPEN_EXCEPTION_EXISTS` | 409 | 该订单已有未关闭异常（details.exception_id） |
| `AI_ANALYSIS_IN_PROGRESS` | 409 | 同一异常已有分析在跑 |
| `APPROVAL_ALREADY_DECIDED` | 409 | 审批单已被处理 |
| `AI_OUTPUT_INVALID` | 502 | LLM 输出未通过 schema 校验（重试后仍失败） |
| `LLM_UNAVAILABLE` | 503 | 模型不可达/超时/超额，已降级 |
| `KNOWLEDGE_EMPTY` | 200+空 | 检索无命中（不算错误，返回空并提示） |
| `RATE_LIMITED` | 429 | 触发限流 |
| `INTERNAL_ERROR` | 500 | 未捕获异常（响应不含堆栈，日志含 request_id） |

### 10.3 接口清单

```text
【认证】/auth
POST   /auth/register            注册（email/password/name）→ 201 user
POST   /auth/login               → 200 {access_token, token_type, expires_in, user}
GET    /auth/me                  当前用户 + 工作区成员列表
POST   /auth/logout              客户端丢弃 token（服务端无黑名单，明确声明）

【工作区】/workspaces
GET    /workspaces               我加入的工作区
POST   /workspaces               创建工作区（创建者自动 OWNER）
GET    /workspaces/current       当前工作区详情
GET    /workspaces/current/members
POST   /workspaces/current/members          直接添加成员（按 email，不做邮件邀请）
PATCH  /workspaces/current/members/{id}     改角色（不能改 OWNER 以外的人为 OWNER）
DELETE /workspaces/current/members/{id}     移除（不能移除自己/OWNER）

【主数据】客户 / 承运商 / 车辆 / 司机
GET|POST        /customers           ?q&level&page&page_size&sort
GET|PATCH       /customers/{id}
GET|POST        /carriers            GET|PATCH /carriers/{id}
GET|POST        /vehicles            ?status&carrier_id     GET|PATCH /vehicles/{id}
GET|POST        /drivers             GET|PATCH /drivers/{id}

【订单】/orders
GET    /orders                   ?status&customer_id&order_no&created_from&created_to&sort&page
POST   /orders                   创建（CREATED）
GET    /orders/{id}              详情（含 customer/carrier/vehicle/司机 摘要 + sla 快照 + 当前异常摘要）
PATCH  /orders/{id}              改基础信息；填 vehicle/carrier 时按状态机派车
GET    /orders/{id}/tracking-events
POST   /orders/{id}/tracking-events   写入轨迹 → 同步触发 ETA 重算与异常检测
GET    /orders/{id}/exceptions

【SLA 规则】/sla-rules
GET|POST /sla-rules              GET|PATCH /sla-rules/{id}

【异常】/exceptions
GET    /exceptions               ?status&level&type&customer_id&sla_breached&assigned_to&sort=-risk_score&page
POST   /exceptions               手工建单（MANUAL，ADMIN+）
GET    /exceptions/{id}          详情：主单 + 订单/客户/车辆快照 + 最新 ETA + 最新分析摘要
PATCH  /exceptions/{id}          改 assigned_to / remark（不改 status）
POST   /exceptions/{id}/confirm          DETECTED → CONFIRMING
POST   /exceptions/{id}/analyze          CONFIRMING → ANALYZING，202 + {analysis_id}
POST   /exceptions/{id}/resolve          → RESOLVED（body: note）
POST   /exceptions/{id}/close            → CLOSED（body: reason_code, note）
GET    /exceptions/{id}/events           时间线（分页）
GET    /exceptions/{id}/messages         承运商消息列表
POST   /exceptions/{id}/messages         录入消息 → 触发解析（同步返回 PENDING + message_id）

【AI 分析】/ai-analyses
GET    /ai-analyses/{id}         状态 + output + risk_level_calculated + 步骤进度（前端轮询此接口）
GET    /ai-analyses/{id}/steps   工具调用明细
POST   /ai-analyses/{id}/retry   FAILED 时重跑（复用 input_hash）

【人工确认】/approvals
GET    /exceptions/{id}/approvals
POST   /approvals/{id}/approve   body: {expected_version, final_payload}（可修改 AI 建议）
                                 执行成功后 status=EXECUTED，返回 execution_result
POST   /approvals/{id}/reject    body: {expected_version, reason}
POST   /approvals/batch-approve  body: {exception_id, approval_ids[], auto_execute}
                                 → 逐条执行，返回每条的成败（部分失败可单独重试）
POST   /approvals/{id}/execute   针对 FAILED 的重试执行

【跟进任务】/followups
GET    /exceptions/{id}/followups
POST   /followups                手工建（source=MANUAL）
PATCH  /followups/{id}           改 assignee/due_at/status（DONE 记 done_by/done_at）

【通知】/notifications
GET    /exceptions/{id}/notifications
GET    /notifications/{id}
PATCH  /notifications/{id}       编辑正文（仅 DRAFT 可改，保留 ai_draft_content）
POST   /notifications/{id}/mark-sent   模拟发送 → SENT_MOCK + 审计
POST   /notifications/{id}/skip        → SKIPPED（需 reason）

【Dashboard】/dashboard
GET    /dashboard/summary        今日订单/运输中/异常/高风险/待处理/已解决 + SLA 违约数
GET    /dashboard/trend          近 7 天异常与违约趋势（seed 数据，供前端简单折线）

【审计】/audit-logs
GET    /audit-logs               ?resource_type&resource_id&actor_id&action&occurred_from&occurred_to&page

【知识库】/knowledge
GET    /knowledge/docs           列出文档与分片数
POST   /knowledge/reindex        从 backend/app/knowledge/*.md 重建分片（ADMIN+）

【系统】/healthz、/demo
GET    /healthz                  {status, db, clock_mode, ai_mode}
GET    /demo/state               基准日、时钟偏移、当前场景
POST   /demo/actions/tick        body: {minutes: 60}  推进时钟（本地/ADMIN+）
POST   /demo/actions/advance-to-less   推进到订单送达并触发自动关闭
POST   /demo/actions/reset       重置为 seed 初始态
```

关键示例（三张最重要的请求/响应，其余由 OpenAPI 生成）：

```http
POST /api/v1/exceptions/12/analyze
{ "expected_version": 3 }
→ 202 { "analysis_id": 88, "status": "PENDING" }
→ 409 { "error": { "code": "AI_ANALYSIS_IN_PROGRESS", "message": "该异常已有分析任务在执行", "details": {"analysis_id": 87} } }
```

```http
GET /api/v1/ai-analyses/88
→ 200 {
  "id": 88, "status": "READY", "is_replay": true,
  "steps": [
    {"step_no": 1, "step_type": "TOOL", "tool_name": "get_order",           "status": "OK", "duration_ms": 12, "result_summary": "SO20260930021 天津→上海 IN_TRANSIT"},
    {"step_no": 2, "step_type": "TOOL", "tool_name": "get_tracking_events", "status": "OK", "duration_ms": 9,  "result_summary": "6 条轨迹，最后位置 济南 19:00"},
    {"step_no": 3, "step_type": "TOOL", "tool_name": "get_customer_sla",    "status": "OK", "duration_ms": 8,  "result_summary": "VIP：发车后 24h，允许延迟 0min"},
    {"step_no": 4, "step_type": "TOOL", "tool_name": "get_vehicle",         "status": "OK", "duration_ms": 7,  "result_summary": "津A·12345 REPAIRING"},
    {"step_no": 5, "step_type": "TOOL", "tool_name": "get_exception_history","status":"OK", "duration_ms": 6, "result_summary": "近 90 天 0 次"},
    {"step_no": 6, "step_type": "TOOL", "tool_name": "search_knowledge",    "status": "OK", "duration_ms": 14, "result_summary": "命中 3 条（车辆故障处理规范#2.1）"},
    {"step_no": 7, "step_type": "VALIDATE", "status": "OK", "duration_ms": 3, "result_summary": "schema 校验通过"}
  ],
  "risk_level_calculated": "CRITICAL",
  "output": {
    "summary": "车辆在济南爆胎维修，预计 20:00 恢复，预计到达 22:30，将超出 VIP 客户承诺时间。",
    "root_cause": {"code": "VEHICLE_BREAKDOWN", "note": "承运商反馈右后轮爆胎，正在等待修理厂"},
    "impact": {"delay_minutes": 270, "sla_breached": true, "affected_customer_level": "VIP"},
    "suggestions": [
      {"code": "UPDATE_ETA", "title": "将 ETA 更新为 22:30", "rationale": "以承运商给出的 20:00 恢复时间+剩余里程估算"},
      {"code": "CREATE_FOLLOWUP", "title": "20:30 回访承运商确认是否已恢复", "assignee_role": "OPERATOR"},
      {"code": "SAVE_NOTICE", "title": "生成并发送延误通知给客户", "rationale": "VIP 客户 SLA 违约，规范要求 30 分钟内告知"}
    ],
    "open_questions": ["修理厂是否已确认配件到位？"],
    "evidence_refs": [
      {"type": "TRACKING_EVENT", "id": 4127, "note": "19:00 仍位于济南"},
      {"type": "CARRIER_MESSAGE", "id": 33, "note": "“车在济南爆胎了…预计晚上 8 点恢复”"},
      {"type": "KNOWLEDGE_CHUNK", "id": 7, "doc": "车辆故障处理规范", "section": "2.1 车辆故障"}
    ]
  }
}
```

```http
POST /api/v1/approvals/51/approve
{ "expected_version": 1,
  "final_payload": { "eta_at": "2026-09-30T14:30:00Z", "reason": "承运商反馈 20:00 恢复" } }
→ 200 { "id": 51, "status": "EXECUTED", "diff": {"changed": ["eta_at"], "ai_value": "...", "final_value": "..."},
        "execution_result": {"order_id": 21, "updated_fields": ["current_eta_at"], "audit_log_id": 903, "followup_task_id": 77} }
```

---

## 11. AI 契约（含原稿要求的"三张设计表"）

### 11.1 边界与原则（硬约束）

```text
1) AI 只读：Tool 全部只读；AI 模块禁止 import Repository/Session（有静态检查）
2) AI 不决定 level：level 由 §8.6 规则计算，LLM 只能给 root_cause/摘要/建议
3) AI 不编事实：通知草稿中出现的 ETA/订单号/客户名/延误分钟必须来自 Tool 返回，校验阶段逐字段比对
4) AI 的所有输出必须过 Pydantic schema；失败 → 修复重试 1 次 → 仍失败 → 任务 FAILED + 降级
5) AI 的每一个写意图都变成 approval，由人批准后交 Service 执行
6) 每次分析都落库（含 prompt_version、raw_output、token、耗时、工具步骤），可按 case_no 复盘
7) AI 失败不阻塞业务：异常仍可由人工 resolve/close（这是"人在环上"的证明）
```

### 11.2 三张设计表（原稿 §23 的交付物，此处一次给全）

**表 1 · AI 输入**

| 任务 | 触发点 | 输入来源 | 具体入参 | PII | 幂等键 |
|---|---|---|---|---|---|
| `T1 PARSE_MESSAGE` 承运商消息解析 | `POST /exceptions/{id}/messages` | carrier_message.raw_text + 订单/车辆上下文 | raw_text、order_no、origin/dest、vehicle.plate_no、当前时间 | 手机号已脱敏 | sha256(raw_text + 当前状态) |
| `T2 ANALYZE_EXCEPTION` 异常分析与建议 | `POST /exceptions/{id}/analyze` | 6 个只读 Tool + 规则计算结果 | order、tracking_events、customer、sla_rule、vehicle、exception_history、knowledge_chunks、**规则算出的 sla_delay/sla_breached** | 手机号已脱敏 | sha256(exception 关键字段 + 最新轨迹 id + 消息 id) |
| `T3 DRAFT_NOTICE` 客户通知草稿 | T2 完成后自动串联 | **系统已确认的事实**（结构化，非原始文本） | order_no、customer_name、promised_delivery_at、expected_eta_at、delay_minutes、cause 概述、处理进展 | 不含手机号 | sha256(T2 输出 id + 事实快照) |

**表 2 · AI 输出（JSON Schema，全部 `additionalProperties=false`）**

| 任务 | 字段 | 类型 | 必填 | 校验规则 | 是否用于写库 |
|---|---|---|---|---|---|
| T1 | `exception_type` | enum(VEHICLE_BREAKDOWN, DELAY_RISK, OTHER) | ✅ | 必须在枚举内 | 否（建议更新 type，走审批） |
| T1 | `location` | string ≤64 | ✅ | 非空 | 否 |
| T1 | `status` | enum(REPAIRING, WAITING_PARTS, BREAKDOWN, MOVING, UNKNOWN) | ✅ | | 否 |
| T1 | `estimated_recovery_at` | string(datetime, 可为 null) | ✅ | 必须晚于 occurred_at 且早于 +48h；相对时间（“晚上8点”）由 prompt 提供当前日期解析 | 否 |
| T1 | `confidence` | number 0–1 | ✅ | <0.6 时前端标黄并要求人工确认 | 否 |
| T1 | `missing_info` | string[] | ✅ | 可为空 | 否 |
| T2 | `summary` | string ≤300 | ✅ | 只能复述输入事实 | 否 |
| T2 | `root_cause.code` | enum(VEHICLE_BREAKDOWN, TRAFFIC, WEATHER, CUSTOMS, CUSTOMER, UNKNOWN) | ✅ | | 是（需审批） |
| T2 | `root_cause.note` | string ≤200 | ✅ | | 是 |
| T2 | `impact.delay_minutes` | integer | ✅ | **必须等于后端计算的 sla_delay_minutes（±5min 容差）**，否则校验失败 | 否（用后端值） |
| T2 | `impact.sla_breached` | boolean | ✅ | 必须等于后端计算结果 | 否 |
| T2 | `suggestions[]` | object[] (code/title/rationale) | ✅ | 1–5 条；code ∈ ApprovalAction 白名单 | 转 approval |
| T2 | `open_questions[]` | string[] | ✅ | ≤5 | 否 |
| T2 | `evidence_refs[]` | object[] (type/id/note) | ✅ | id 必须存在于本次 Tool 返回结果中（防编造来源） | 否 |
| T3 | `subject` | string ≤60 | ✅ | | 是（需审批） |
| T3 | `content` | string ≤500 | ✅ | 必须含 order_no 与 expected_eta；**用正则抽取数字/单号与事实比对** | 是 |
| T3 | `tone` | enum(FORMAL, APOLOGETIC) | ✅ | | 否 |

**表 3 · 所需业务数据与 Tool**

| Tool | 用途 | 入参 | 出参与摘要 | 底层 Service | 只读 | 权限 |
|---|---|---|---|---|---|---|
| `get_order` | 订单上下文 | order_id | order_no/status/customer_id/origin/dest/distance_km/promised_delivery_at/current_eta_at | `OrderService.get` | ✅ | 会话上下文内 |
| `get_tracking_events` | 轨迹时间线 | order_id, limit≤50 | 最近事件（时间/城市/类型/来源） | `TrackingService.recent` | ✅ | 同上 |
| `get_customer` | 客户上下文 | customer_id | name/level/contact(masked)/notify_pref | `CustomerService.get` | ✅ | 手机号脱敏 |
| `get_customer_sla` | SLA 规则 | customer_id | scope/offset_hours/max_delay_minutes/promised_delivery_at | `SlaService.resolve` | ✅ | |
| `get_vehicle` | 车辆状态 | vehicle_id | plate_no/status/carrier/current_city | `VehicleService.get` | ✅ | |
| `get_exception_history` | 历史异常 | customer_id/order_id, days=90 | 条数、类型、平均处理时长 | `ExceptionService.history` | ✅ | 仅计数与摘要 |
| `search_knowledge` | 规范检索 | query, top_k≤5 | chunks（content/source_doc/section/chunk_id/score） | `KnowledgeService.search` | ✅ | 必须带来源 |
| （无写工具） | 写意图 → `proposed_actions` → approval | | | | | |

> 原稿把 `create_followup_task` 放在 Tool 列表里，与"写操作必须人工确认"冲突。
> 本基线把它改为 **AI 只输出建议项 → 后端生成 approval → 人批准后由 `ApprovalExecutor` 调 Service 创建**（ADR-A5）。

### 11.3 有界 Agent 循环

```text
形态      单轮 tool-calling 循环（不使用 LangGraph，见 ADR-A6）
最大步数  8（含最终输出步骤）
总超时    90s（LLM 单次 60s）
工具白名单 仅 §11.2 表 3 的 7 个
循环终止  产出合法 JSON 或超步数/超时/连续两次校验失败
降级      写 ai_analysis.status=FAILED + error_code（LLM_UNAVAILABLE / AI_OUTPUT_INVALID），
          前端展示"AI 暂不可用，可重试或手工处理"，异常状态由 ANALYZING 回退 CONFIRMING
重放      AI_MODE=replay 时，按 input_hash 从 fixtures 目录读取录制结果（含 steps），
          完全不走网络 → 面试现场与 CI 都确定
```

### 11.4 Prompt 与版本管理

```text
位置        backend/app/ai/prompts/{t1_parse_message,t2_analyze_exception,t3_draft_notice}/v1.md
版本        prompt_version = "v1"（写入 ai_analysis，可对比不同版本效果）
模板变量    用 {placeholder} 显式声明 + 单测断言"模板变量与代码传入一致"（防漏传导致幻觉）
system 约定  只输出 JSON；不得编造未提供的字段；无依据时填 null 并写入 open_questions
few-shot    T1 放 3 个例子（含"晚上8点"这类相对时间解析）；T2 不放假数据示例，避免模型抄示例
```

### 11.5 人工确认（HITL）执行链

```text
T2 输出 → 后端把 suggestions 转成 approval(PENDING)（1 条建议 = 1 张审批单）
        → 前端在异常详情页逐条展示"AI 建议 / 依据 / [编辑] [批准] [驳回]"
人批准   POST /approvals/{id}/approve {expected_version, final_payload}
        → ApprovalExecutor（事务内）：
            1) 校验：审批单 PENDING 且 version 匹配且操作者权限足够
            2) 按 action_type 分发：
               UPDATE_ETA       → OrderService.update_eta() + 重算 SLA + 写 exception_event(ETA_UPDATED)
               CREATE_FOLLOWUP  → FollowupService.create(source=AI_SUGGESTED)
               SAVE_NOTICE      → NotificationService.create(status=DRAFT)
               SEND_NOTICE      → NotificationService.mark_sent(status=SENT_MOCK)
               CLOSE_EXCEPTION  → 状态机 close
            3) 写异常时间线 + 审计（记录 source=APPROVED_AI，ai_payload 与 final_payload 都留痕）
            4) approval → EXECUTED，execution_result_json 落结果
失败     → approval=FAILED + execution_result_json.error；人或系统可 POST /approvals/{id}/execute 重试（retry_count+1）
不可执行 拒绝（REJECTED，必填 reason）与过期（EXPIRED，24h）都不产生任何业务副作用
```

### 11.6 知识库检索（不引向量库）

```text
语料      backend/app/knowledge/*.md（5 篇：异常处理规范 / SLA规则 / VIP服务规则 / 车辆故障处理规范 / 客户通知规范）
切分      按 ## 小节切，超 500 字再按段落切；chunk 保留 section_path
索引      写入 knowledge_chunk，MySQL FULLTEXT(content) WITH PARSER ngram
检索      SELECT ... MATCH(content) AGAINST (:q IN BOOLEAN MODE) ORDER BY score DESC LIMIT 5
          无命中 → LIKE 兜底分词召回；仍为空 → 返回 []，Agent 提示"无规范可依"并降低建议置信度
来源      每个 chunk 返回 doc_title + section_path + chunk_id，前端可点击查看原文；
          T2 的 evidence_refs 必须引用真实 chunk_id
可切换     KnowledgeService.search 接口保留，P2 可换成 Qdrant 向量检索而不动 Agent（ADR-A4）
```

### 11.7 模型、成本与脱敏

```text
模型      LLM_MODEL 默认 deepseek-chat（OpenAI 兼容端点，JSON 友好、中文好、便宜）
可替换    改 LLM_BASE_URL/LLM_MODEL 即可换 qwen-plus / gpt-4o-mini，代码零改动
成本上限  单次 ≤8k in / 1.5k out；演示默认 AI_MODE=replay（0 成本）；live 模式设每日 ¥20 上限，超限直接降级
脱敏      写 prompt 前经 core/masking.py：手机号 → 138****0001，邮箱 → a***@x.com，不发送内部主键之外的敏感字段
日志      每次调用记录 model/prompt_version/tokens/latency/raw_output（原始文本入库便于复盘，API 不返回内部 id 细节）
合规声明  seed 数据全为虚构（客户名、车牌、电话均为假数据）；如接真实企业数据，需先做数据出境与保密评审（本作品不涉及）
```

### 11.8 AI 失败与降级预案

| 失效场景 | 系统行为 | 用户可见 |
|---|---|---|
| LLM 超时/5xx | 重试 2 次（1s/3s 退避）→ 失败 | 分析卡片显示失败 + [重试]，异常可手工处理 |
| 输出非法 JSON | 带错误信息修复重试 1 次 → 失败 | 同上，日志留 raw_output |
| 事实不一致（ETA/单号被编造） | 校验拒绝，进修复重试 → 失败 | 标红"AI 输出与系统事实不一致，已拦截"（可复现的防幻觉演示） |
| 检索无命中 | 正常继续，open_questions 提示 | 建议区标注"未找到相关规范" |
| token/成本超限 | 直接降级 | "今日 AI 额度已用完" |
| 现场无网络 | `AI_MODE=replay` 回放 fixture | 界面标注"回放模式"（不影响演示流畅度） |

---

## 12. 前端设计

### 12.1 路由与页面

```text
/login  /register
/dashboard                     6 张统计卡 + 近 7 天趋势 + 高风险异常 Top5
/orders                       列表（状态/客户/时间筛选 + 分页 + 排序）
/orders/:id                   详情 + 轨迹时间线 + 派车操作 + 关联异常
/exceptions                   异常中心（列表：订单/客户/异常/等级/SLA影响/状态/更新时间 + 搜索筛选排序）
/exceptions/:id               异常详情（本项目的门面，布局见 12.2）
/customers  /vehicles  /drivers  /sla-rules     主数据 CRUD（ADMIN+ 显示编辑按钮）
/carriers                     **不在左侧菜单**（ADR-A17）：只是归属字典，入口在「车辆」页操作区；
                              路由与页面保留，管理员可直达；承运商名称在车辆/司机/订单/异常页作为字段展示
/knowledge                    知识库文档与分片查看（点击回到原文小节）
/audit                        审计日志（筛选 + 详情抽屉）
/members                      成员与角色
/demo                         Demo 控制台（tick/advance/reset/AI 模式切换，仅本地）
```

### 12.2 异常详情页布局（演示主战场）

```text
┌ 顶部：SO20260930021  车辆故障  [CRITICAL]  PROCESSING  SLA 违约 270min   处理人：张三    [确认][AI分析][处理完成][关闭]
├ 左列（事实）                      │ 中列（AI 面板）                 │ 右列（协同）
│ · 订单信息（客户/等级/起终/车辆/司机）│ · [AI 分析此异常] 按钮           │ · 承运商消息（原文 + 解析结果对比）
│ · 运输轨迹时间线（含异常标记）      │ · 进度：✓get_order ✓轨迹 ✓SLA … │ · 客户通知（草稿/编辑/批准/复制/模拟发送）
│ · SLA 影响卡（承诺/预计/延误/违约）  │ · 结论：摘要/原因/影响/建议       │ · 跟进任务（勾选完成）
│ · 风险等级 + 为什么（risk_factors）  │ · 证据来源（可点击跳原文）        │ · 操作记录（时间线）
│                                   │ · 待确认事项                     │
│                                   │ · 建议 → 审批单（批准/编辑/驳回） │
└───────────────────────────────────┴─────────────────────────────────┴──────────────────┘
```

关键交互约定：

```text
1) AI 分析：点按钮 → POST /analyze → 每 1.5s 轮询 GET /ai-analyses/{id} → READY/FAILED 停止（最多 90s 超时提示）
   步骤逐条"点亮"（用 UNRESOLVED 的 step 列表驱动，不做假动画）
2) 审批：编辑后必须展示 diff（AI 原值 vs 人工值），批准前二次确认；批量批准逐条执行并展示每条结果
3) 写操作全部带 expected_version；收到 409 时提示"数据已被他人更新，已为你刷新"
4) 权限：路由守卫 + 按钮级 `v-if=can('exception.handle')`；无权限按钮直接不渲染（而非点了报错）
5) 时间统一用 utils/datetime.ts 把 UTC 转 Asia/Shanghai；相对时间用 dayjs relativeTime 中文
6) 演示态：顶部横幅显示 "AI 模式：回放 / 实时"、"业务时间：2026-09-30 19:05"，让面试官一眼知道这是可控 demo
```

### 12.3 前端工程约定

```text
api/          每个模块一个 ts 文件，统一走 request.ts（拦截器：401 登出、错误 code → 中文文案、自动带 X-Workspace-Id）
types/        与后端 schema 一一对应（手写，保持显式；不做运行时自动转换，字段就是 snake_case）
stores/       auth（token/user/permissions）、workspace（当前工作区）
utils/        datetime.ts / permissions.ts / format.ts（含 riskLevel 颜色与文案映射）
components/   RiskTag / SlaImpactCard / TrackingTimeline / AiPanel / ApprovalCard / EvidenceList
```

---

## 13. 模拟环境与 Seed（可复现是硬指标）

### 13.1 时钟抽象与基准日

```text
core/clock.py:  Clock.now() -> datetime(UTC)
  SystemClock  当前真实时间
  ReplayClock  DEMO_BASE_DATE + system_setting['demo.clock_offset_minutes']（默认 0）
CLOCK_MODE=replay（默认）时全系统使用 ReplayClock，包括"订单送达 24h 后自动关闭"这类时间推理
→ 演示与测试中的"时间流逝"由 POST /demo/actions/tick {minutes} 显式推进，因此完全可复现
```

### 13.2 Seed 清单（固定随机种子 20260930，可 `--reset` 幂等重建）

```text
workspace  1 个（"顺捷物流"）+ 4 个用户（owner/admin/operator/viewer，密码统一 Demo@12345）
客户       12 个（NORMAL 8 / VIP 3 / SVIP 1），假电话 138****
承运商     4 个      车辆 24 台      司机 24 名
SLA 规则   DEFAULT(30h/30min)、CUSTOMER_LEVEL:VIP(24h/0)、CUSTOMER:VIP-01(24h/0)
订单       1000 单（300 已完成 / 400 运输中 / 300 待发车，含 50 单异常订单）
轨迹       5000+ 条（按订单时间线生成，固定种子）
知识库     5 篇 Markdown → 约 40 个 chunk → 自动建索引
异常       50 个（覆盖各等级/各状态分布，供列表与 Dashboard 演示）
```

### 13.3 脚本化案例（演示固定台词，每次结果一致）

| 案例 | 订单 | 设定 | 演示价值 |
|---|---|---|---|
| CASE-A（主） | `SO20260930021` 天津→上海，VIP-01 | 见下方「CASE-A 权威数值」：09:00 时点轨迹已停滞 180min → `STALL_OVER_THRESHOLD` 建单；承运商消息"车在济南爆胎了…预计晚上 8 点恢复"；延误 300min、`sla_breached=true`、`risk_score=4`、CRITICAL | 全闭环 + 规则定级 + AI 受限 + 人工审批 |
| CASE-B（已完成） | 另一 VIP 单 | 已有完整历史：分析、审批、通知、跟进、关闭、审计 | 展示"已解决"记录与审计细节，说明留痕完整 |
| CASE-C（误报） | 普通单 | 静止 130min 但实为装卸排队，运营确认后 `close(reason=INVALID)` | 展示误报控制与人工兜底 |
| CASE-D（边界） | NORMAL 单 | 延误 25min，规则允许 30min → 不违约，MEDIUM | 展示规则真的在算，而不是随口说 |
| CASE-E（高并发感） | 5 单 | 同时存在不同等级与状态 | 列表筛选/排序/分页演示 |

#### CASE-A 权威数值（勘误：以本节为准，§4 的叙述性时间只用于讲故事）

> 原稿 §4 举例"19:00 济南 / ETA 22:30 / 延误 4.5h"是**规则定下来之前的示意数字**，
> 它与 §8.3 的"VIP-01 = 发车后 24h 承诺"无法同时成立。下表是**真库（MySQL）实测值**，
> 由 `app/rules` 真算得出，已通过 `tests/integration/test_case_a_flow.py` 校验（Asia/Shanghai 展示，DB 存 UTC）：

```text
业务基准时间（ReplayClock 起点） 2026-09-30 09:00 (+08) = 2026-09-30T01:00Z
dispatched_at                   2026-09-30 01:30 (+08) = 2026-09-29T17:30Z
tracking（关键三段）              ARRIVE 济南 06:00 (+08) → STOP 济南 06:15 (+08)  ← 最后一条"移动类"轨迹 06:00
检测时点                         基准 09:00 → 停滞 180min ≥ 120min → STALL_OVER_THRESHOLD
SLA 规则                         CUSTOMER:VIP-01 → 24h / 允许延迟 0min
promised_delivery_at             2026-10-01 01:30 (+08) = 2026-09-30T17:30Z  （= dispatched + 24h ✓）
承运商消息                        "车在济南爆胎了，现在联系修理厂，预计晚上 8 点恢复。"
estimated_recovery_at            2026-09-30 20:00 (+08)
distance_km / 剩余里程            800 km / 400 km，按 40 km/h ≈ 10h
expected_eta_at                  2026-10-01 06:00 (+08) = 2026-09-30T22:00Z
sla_delay_minutes                270  → sla_breached = true（0 分钟容忍）
risk_score                       2(延误 270min) + 1(VIP) + 1(车辆故障) + 1(违约) = 5 → 封顶 4 → CRITICAL
risk_factors                     DELAY_BASE(2) / CUSTOMER_VIP(1) / VEHICLE_BREAKDOWN(1) / SLA_BREACH(1)
验收断言                          level=CRITICAL、risk_score=4、sla_breached=true、240 ≤ delay ≤ 300
```

Seed 生成器**必须用 `app/rules` 真算**上述 ETA/延误/等级，不允许硬编码等级字段。
前端 mock fixture 需与本表一致，保证"无后端兜底视图"和真实接口讲同一个故事。
（`docs/01`–`06`、README、前端 fixture 中的 CASE-A 数字若与本表冲突，一律以本表为准。）

### 13.4 Demo 控制接口

```text
POST /demo/actions/tick {minutes:60}          推进时钟；期间自动执行：轨迹生成 → ETA 重算 → 检测 → 审批过期检查 → 自动关闭检查
POST /demo/actions/advance-to-less            一步推到"送达并自动关闭"（内部循环 tick 直到 DELIVERED）
POST /demo/actions/reset                      重建 seed 并重置时钟（演示翻车后 3 秒恢复）
GET  /demo/state                              {base_date, offset_minutes, scenario, ai_mode, clock_mode}
```

---

## 14. 测试与验收

### 14.1 测试分层

```text
tests/unit/          纯函数：状态机、SLA、风险规则、ETA、去抖/合并、脱敏、权限矩阵、审批 diff
tests/api/           TestClient：认证、RBAC 403、跨租户 404、CRUD、分页/排序/筛选、乐观锁 409、状态机 409、错误码
tests/ai/            FakeLLM（fixtures 录制）：解析抽取、工具调用顺序、步数上限、非法 JSON 修复重试、
                     超时降级、事实一致性拦截（防幻觉回归）、prompt 模板变量完整性
tests/integration/   一条 E2E：建单 → 轨迹 → 检测 → 消息解析 → 分析 → 审批 → 执行 → 跟进 → 送达 → 自动关闭 → 审计可查
tests/live/          @pytest.mark.live 真实调用模型（默认 skip，需 LLM_API_KEY）
数据库               API/集成测试跑 docker 内 MySQL（logiops_test），用例开始事务、结束回滚；单测不碰 DB
FakeLLM 设计         读 fixtures/{input_hash}.json 返回 {steps, output}；写入模式 AI_RECORD=1 可录制新 fixture
E2E（可选 P2）        Playwright 一条主链路（登录→异常中心→AI 分析→批准→看到 EXECUTED）
```

### 14.2 覆盖要求

```text
状态机 / SLA / 风险规则 / ETA / 权限矩阵    分支覆盖 100%（缺一分支即失败）
后端整体                                    line ≥ 70%（pytest-cov，CI 阈值写进 pyproject）
AI 模块                                      工具调用与校验路径 line ≥ 80%
前端                                          vitest 覆盖 utils + stores（不追求组件覆盖率），vue-tsc 零错误
```

### 14.3 阶段验收 DoD（每阶段都要满足：冒烟通过 + 测试通过 + README 同步 + 截图入 docs）

| 阶段 | 交付 | 验收（可演示） |
|---|---|---|
| 1 业务底座 | 认证/RBAC/工作区/客户/承运商/车辆/司机/订单/轨迹/SLA + Seed + 前端登录+列表+详情 | 用 operator 账号创建一票订单并派车，能看到 promised_delivery_at 自动算出；VIEWER 无法改单 |
| 2 异常系统 | 检测规则 + 异常主单/时间线 + 承运商消息 + 异常中心与详情页 | 对 CASE-A 触发自动建单，等级由规则算出；CASE-C 可判误报关闭；CASE-D 显示不违约 |
| 3 AI | 消息解析 + 分析 + 建议 + 通知草稿 + 进度轮询 + 三张设计表落地 | 录入“车在济南爆胎了…预计晚上8点恢复”，系统正确抽取 4 个字段并给出 CRITICAL 级分析；故意改动事实后校验拦截 |
| 4 闭环 | 审批 + 执行器 + 跟进 + 通知 + 审计 + 自动关闭 + Docker + 一键验收 | 跑 §16 Demo 脚本全程无手工改库；两次运行输出 JSON 完全一致；审计页可查到每一步来源 |

### 14.4 性能基线（本地 docker，seed 规模）

```text
列表接口 P95 ≤ 300ms      详情接口 P95 ≤ 500ms      Dashboard ≤ 500ms
AI 分析（replay）≤ 2s     AI 分析（live）≤ 60s       前端首屏 ≤ 2s
并发冒烟：locust 10 用户只读混合 1 分钟无 5xx（P2，可选）
```

### 14.5 一键验收脚本（`scripts/acceptance.ps1`）

```powershell
# 1) 起库 + 迁移 + seed --reset
# 2) 跑 pytest（unit/api/ai/integration）并输出覆盖率
# 3) 用 TestClient 跑一遍 CASE-A 全链路，把结果 JSON 落盘 artifacts/acceptance_run1.json
# 4) 重置后重跑一次录盘 run2.json
# 5) 比较两次结果（忽略时间戳与自增 id），不一致则非零退出
# 6) 输出汇总：用例数/覆盖率/耗时/AI 步骤数/风险等级
```

---

## 15. 里程碑与排期（单人 10 个工作日，含 1 天缓冲）

| 天 | 阶段 | 产出 |
|---|---|---|
| D1 | 阶段 0 脚手架 | 仓库结构、docker-compose、Alembic 初始迁移、CI（ruff+pytest）、`healthz`、README 首屏 |
| D2–D4 | 阶段 1 业务底座 | T01–T10 模型与接口 + 权限矩阵 + Seed + 前端登录/Dashboard/订单列表与详情 |
| D5–D6 | 阶段 2 异常系统 | 检测 + 状态机 + 异常中心/详情页 + 承运商消息 + 时间线 + CASE-A/C/D 可复现 |
| D7–D8 | 阶段 3 AI | LLM 客户端 + FakeLLM + 3 个 prompt + 7 个 Tool + 有界循环 + 校验/降级 + AI 面板轮询 |
| D9 | 阶段 4 闭环 | approval + executor + followup + notification + audit + 自动关闭 + `tick` |
| D10 | 收尾 | 一键验收 + demo-metrics 实测 + 演示录屏 + README/架构图 + 简历条目 + 缓冲 |

**交付物清单**：可运行仓库、`docker compose up` 一键起、OpenAPI、docs 六件套、验收报告、5 分钟演示视频、
简历一句话描述 + 3 条可量化的面试讲述点。

---

## 16. 最终 Demo 脚本（5 分钟，逐分钟）

```text
0:00–0:30  背景与问题：跟单员在异常发生时要在 4~5 个系统间核对信息，一票异常平均耗时 N 分钟
0:30–1:00  架构一页图：Router→Service→Repository；AI 只读 Tool，写操作走审批（强调"AI 不碰库"）
1:00–1:30  登录（operator）→ Dashboard：50 个异常、SLA 违约数、高风险 Top5
1:30–3:00  打开 CASE-A：轨迹时间线显示 19:00 起济南停滞 → 系统已自动建单且等级 CRITICAL
           点【AI 分析】：步骤逐条点亮（get_order→轨迹→客户→SLA→车辆→历史→规范）
           输出：原因/影响/建议/待确认 + 证据来源可点击
           展示"等级是规则算的"：打开 risk_factors 面板，逐项说明加分来源
3:00–3:40  录入承运商消息（原文粘贴）→ 解析结果与原文对照 → AI 生成客户通知草稿
           故意指给面试官看：草稿里的 ETA/单号与系统一致；现场演示"喂假事实 → 被拦截"
3:40–4:20  逐条审批：把 AI 建议的 ETA 从 22:30 手工改成 22:45 → 展示 diff → 批准
           → 后端执行：ETA 更新、跟进任务创建、通知入草稿、审计入账（一屏看到 4 条结果）
4:20–4:50  Demo 控制台 tick 推进：车辆恢复 → ETA 重算 → 订单送达 → 24h 后异常自动关闭
           打开操作记录与审计：谁、何时、基于什么（MANUAL/APPROVED_AI/SYSTEM）
4:50–5:00  效率对比表（真实实测）：信息收集 / 分析 / 确认 / 全流程耗时，传统 vs AI 辅助
           收尾一句：这个项目的价值不是"用了大模型"，而是"把 LLM 关进了可信的边界里"
```

---

## 17. 效果度量协议（替代原稿"开发完成后实测"的含糊表述）

```text
对照对象  同一 CASE-A，两种流程各跑 3 次
传统流程  只给原始材料（订单截图/轨迹表/承运商消息原文/规范 PDF），被测者手工：找信息 → 判断 → 写通知
           计时点：T0 开始 → T1 信息收集完成（口述"信息齐了"）→ T2 给出处理决定 → T3 通知草稿完成
AI 流程   使用本系统，同样计时点（AI 分析完成即 T2 的辅助点，但仍以人确认为准）
记录      每次耗时写入 docs/demo-metrics.md（含日期、被测流程、T1/T2/T3、总耗时、备注）
输出      一张对比表 + 一句结论；若 AI 流程更慢（可能因数据准备），如实写明并解释原因
禁止      预先编造数字；表里必须写明测量方式与样本量（3 次，单人），并说明局限性
```

---

## 18. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| LLM 输出不稳定/编造 | 演示翻车 | 规则定级 + schema 校验 + 事实比对 + replay 模式 + 降级提示 |
| 现场无网络/额度不足 | 演示中断 | `AI_MODE=replay`（默认）+ 预置 fixtures + reset 接口 |
| 范围膨胀（想做多种异常） | 做不完 | 范围冻结 §3；枚举预留但不产品化 |
| 同步栈下 AI 调用阻塞 | 请求变慢 | AI 分析异步落库（202 + 轮询），`def` 端点走线程池；演示规模无压力 |
| 状态机遗漏分支 | 数据不一致 | 状态机集中实现 + 分支 100% 覆盖 + 非法流转 409 单测 |
| 多租户过滤漏写 | 越权 | Repository 统一注入 workspace_id + 跨租户测试用例 |
| Demo 数据不可复现 | 无法验证 | 固定种子 + 基准日 + ReplayClock + 一键验收脚本 diff 两次结果 |
| 单人体力/时间不足 | 交付缩水 | P2（Playwright、locust、gantt 大屏）可砍；P0/P1 必须完成 |

---

## 19. 面试话术（预判问题 + 标准答法）

| 问题 | 答法（一句话版，细节引 ADR） |
|---|---|
| 为什么用同步 SQLAlchemy 而不是 async？ | 瓶颈在 LLM 外部调用，不在 DB QPS；FastAPI 的 `def` 端点自动进线程池，同步栈更少踩坑，也更适合单人演示——要换 async 只需换 engine 与 session 依赖（ADR-A1） |
| 为什么不上 MQ / Celery？ | MVP 的事件只有"轨迹写入后重算 ETA"，同步触发就够；定时语义用可显式推进的时钟替代，换来的是**行为可复现、可单测**（ADR-A2） |
| AI 是不是在乱给风险等级？ | 不是。等级 100% 由规则表算，并输出 risk_factors 逐项可解释；LLM 只负责理解与起草，改不了等级（§8.6） |
| 为什么不做向量数据库？ | 知识条目几十条，ngram 全文检索召回足够且结果可解释、可追溯到小节；我保留了 Retriever 接口，量级上来再换 Qdrant（ADR-A4） |
| AI 会不会直接改数据？ | 不会。工具全只读，AI 只能产出审批单；执行由后端 Service 在事务里做，且 AI 原稿与人工修改都留痕（ADR-A5） |
| 怎么防幻觉？ | 三道闸：schema 校验 → 事实比对（ETA/单号/延误分钟必须等于后端值）→ 证据引用必须来自本次工具返回；还有专门的回归测试用例（§11.2 表2、S3/S4） |
| 怎么保证演示每次一样？ | 固定基准日 + 固定随机种子 + ReplayClock + AI replay fixture + 一键验收脚本跑两次 diff（S6） |
| 多租户怎么做的？ | 每表 workspace_id，BaseRepository 强制注入过滤，越权返回 404，越权尝试写审计；有跨租户测试用例（§9.1） |
| 状态机怎么保证不出错？ | 集中式 transition() + 非法流转一律 409 + 分支 100% 覆盖 + 时间线与审计双写（§8.2） |
| 这个项目最难的点是什么？ | 不是接模型，而是**把"事实、规则、模型、人、执行"五者的边界划清**：谁定级、谁能写库、什么必须留痕。我为此专门设计了规则表、审批单与执行器（§11.5） |
| 如果给你更多时间做什么？ | 1) 多异常类型产品化；2) 检索升级为向量+rerank；3) 用真实历史数据做 AI 输出质量评估集；4) 事件总线替换同步触发 |

---

## 附录 A · 决策记录（ADR，含被否方案）

| 编号 | 决策 | 理由 | 代价 | 被否方案 |
|---|---|---|---|---|
| A1 | 同步 SQLAlchemy 2.0 | 团队/单人友好，瓶颈不在 DB | 无 async 吞吐优势 | async SQLAlchemy（学习/踩坑成本高） |
| A2 | 无 MQ，事件同步触发 + 可推进时钟 | 事件单一、行为可复现可测 | 不是"生产级异步" | Celery/APScheduler/RabbitMQ（演示价值低于复杂度） |
| A3 | 风险等级由规则表决定，LLM 只给解释与建议 | 可解释、可测、可审计；原稿硬要求 | 规则需人工维护 | 纯 LLM 定级（不可解释、不可复现） |
| A4 | 知识检索用 MySQL ngram 全文，不用向量库 | 语料小、可解释、零额外服务 | 语义召回弱于 embedding | Qdrant + embedding（多一个服务，收益不成比例）；保留接口便于升级 |
| A5 | AI 全只读 + approval 审批单 + 后端执行器 | 一次性解决原稿"写操作需人工确认"与 `create_followup_task` 的矛盾 | 多一张表与一次交互 | AI 直接调写工具（不可审计、无法追责） |
| A6 | 不使用 LangGraph，手写有界 tool 循环（≤8 步/90s） | 单场景线性流程，手写更透明、可断言步序 | 复杂编排能力有限 | LangGraph（为框架而框架，面试反而被追问为什么需要图） |
| A7 | 前端轮询而非 SSE/WebSocket | 5 行代码、与同步栈一致、断线易恢复 | 有 1.5s 延迟 | SSE/WS（要处理连接生命周期，收益低） |
| A8 | 固定基准日 + ReplayClock | Demo 与测试完全确定性 | 需额外一层时钟抽象（约 30 行） | 直接用 `now()`（每次演示数据都不同，无法 diff） |
| A9 | 存 UTC，展示 Asia/Shanghai | 避免时区类 bug，面试加分 | 前端需一层转换 | 全存本地时间（简单但埋雷） |
| A10 | 枚举用 VARCHAR + StrEnum，不用 MySQL ENUM | 加减枚举值不改表，迁移友好 | 失去 DB 层约束（应用层+单测覆盖） | MySQL ENUM（ALTER 成本高） |
| A11 | 异常 CLOSED 终态，不提供 reopen | 状态机少一环少一半 bug；误关可用新异常关联 | 极端场景需新建单 | reopen（引入历史状态回滚复杂度） |
| A12 | 错误响应 `{error:{code,message,details}}`，不套 `{code,data,message}` | 更 RESTful，前端拦截器统一处理 | 与部分国内团队习惯不同（已在文档显式声明） | 统一包封 200 + code |
| A13 | 默认 `AI_MODE=replay` | 面试现场确定性优先，网络/额度不可控 | 需维护 fixtures | 默认 live（现场风险不可接受） |
| A14 | SLA 日历 24×7 不考虑节假日 | 演示范围内不需要，且规则可解释 | 与真实业务有差距（已在文档声明局限） | 引入工作日历表（范围外） |
| A15 | 单仓 monorepo + uv | 单人开发，依赖锁定与安装体验好 | 非大团队标准 | 多仓 + Poetry/requirements |
| A16 | **车辆与司机 1:1 固定绑定，不做排班/分配表**（`vehicle.current_driver_id` + 订单上 `driver_id` 快照） | 项目拥有者拍板：真实的换班/顶班/双驾由"人"记住，不体现在程序内；MVP 不做排班 | **查不到历史**（"上周三谁开这台车"若无订单就答不了）；**装不下双驾/顶班**；换人只能靠车辆编辑页人工维护 | `vehicle_driver_assignment(vehicle_id, driver_id, start_at, end_at, role, reason)` 分配表 + 换驾接口（若将来要做追责/油耗考核/双驾，按此升级） |
| A17 | **「承运商」不单独占菜单项**，只作为归属字典存在 | 项目拥有者看过依赖面后的取舍：它更像"字典/归属"，不值得占一个一级入口（实体保留：1042 处引用、4 个外键字段不动） | 管理员需从「车辆」页操作区的"承运商字典"入口进入（或直接访问 `/carriers`） | 彻底删除实体（会连带失去"承运商消息"这一核心演示场景的发送主体，返工 2–3 小时）；降级为字符串字段（失去唯一编码与级联） |
| A18 | **车-司机强绑定**：车辆在运营时由其固定主驾驾驶；派车时"选司机 → 车辆自动导入" | 项目拥有者语义澄清：绑定不是"参考关系"，而是硬约束（一台在用车辆不能换人开） | 换人必须先到「车辆」页改绑定；未绑定主驾的车不能派车（或派车时自动补绑定） | 松散关联（允许任意同承运商组合）——会出现"津A·12345 的固定主驾是李四却派给王五"，追责/油耗/联系人都失真 |

**由 A16/A18 派生的硬约束（已实现 + 有测试）**：

1. 车与司机**必须同属一家承运商**：创建/修改车辆时绑定别家司机 → `422 VALIDATION_ERROR`（回归用例 `tests/test_vehicle_driver_binding.py`）；
   把车辆承运商改挂到别家却不换司机，同样 422；车辆编辑页的主驾下拉只列所选承运商的司机。
2. 派车时**归属一致**（车/司机同属订单所选承运商）**且强绑定**成立（回归用例 `tests/test_dispatch_contract.py`，共 7 条）：
   - 车辆已有固定主驾：不给司机 → **自动带入**；给同承运商的别的司机 → `422 车辆与司机是固定绑定关系`；
   - 车辆未绑定主驾：不给司机 → `422 该车辆尚未绑定主驾（在用车辆必须有固定司机）`；给司机 → `200` 并**自动建立绑定**。
3. 前端派车卡片：**先选承运商 → 选司机（选项里括注其固定车牌）→ 车辆自动导入且只读**；司机未绑定车辆时给出警示并禁用提交。

---

## 附录 B · 需要你个人确认的 5 件小事（不阻塞开工，按默认值走也能全部跑通）

| # | 事项 | 默认值（我会按这个做） |
|---|---|---|
| B1 | 产品/仓库英文名 | 仓库 `logiops`，中文名"物流异常协同平台" |
| B2 | LLM 供应商与 Key | 默认 DeepSeek `deepseek-chat`；Key 放 `.env`（不入库）；没有 Key 也能全流程演示（replay） |
| B3 | 演示视频 | 需要，5 分钟，按 §16 脚本录屏；不配音，加字幕 |
| B4 | 简历/项目描述口径 | "独立设计并实现物流异常协同平台（FastAPI+Vue3+LLM）：以确定性规则定级、LLM 受限生成、人工审批执行，实现异常从发现到关闭的可复现闭环" |
| B5 | 是否需要我继续生成脚手架代码 | 默认下一步直接生成 `backend/` 骨架 + Alembic 首个迁移 + Docker Compose + Seed 脚本 |

---

## 附录 C · 与原稿的差异对照

### C1 补齐（原稿未说明、本基线已定）

数据字典（22 张表字段级）、全局枚举、订单/异常双状态机、SLA 计算式、异常检测规则与去抖、
ETA 重算算法、风险等级规则表、权限矩阵与多租户策略、接口全清单与通用约定、错误码表、
AI 三张设计表、Tool 入出参、输出 schema 与校验、prompt 版本管理、有界循环与降级、HITL 数据模型与执行器、
知识库检索方案、时钟与基准日、Seed 脚本化案例、测试分层与覆盖率、性能基线、一键验收、排期、Demo 脚本、度量协议、ADR。

### C2 修改（与原稿表述不同，均为解决原稿自身矛盾）

| 原稿 | 本基线 | 原因 |
|---|---|---|
| `create_followup_task` 属于第一版 AI Tool | 从 Tool 移除，改为 AI 建议 → approval → 后端执行 | 与"写操作必须人工确认""Agent 不直接操作数据库"矛盾 |
| Step 1 用绝对时间（2026-09-30 18:00）又要求每次可复现 | 固定基准日 + ReplayClock + tick | 绝对时间与可复现不可兼得 |
| 风险等级"必须由确定性规则参与"但无规则 | 给出完整评分表与 risk_factors 输出 | 否则无法实现也无法测试 |
| SLA 只有"最大允许延迟" | 完整规则匹配 + 承诺时刻计算 + 违约判定 + 日历口径声明 | 业务流程处处依赖它 |
| "订单完成后异常自动关闭" + `POST /close` | 明确 RESOLVED/CLOSED 语义 + 自动/人工两条路径 + 不可 reopen | 消除状态语义歧义 |
| 集成测试"pytest 全部通过" | 分层测试 + 覆盖率阈值 + LLM 假体 + 两次运行 diff | 让"通过"可验证 |
| 三张设计表（AI 输入/输出/所需数据与 Tool）为待办 | §11.2 直接给全 | 原稿第一阶段交付物 |

### C3 删减（明确不做，写进 README"有意省略"）

Qdrant/embedding 检索（留接口）、ECharts 大屏、Excel 导出、i18n、SSO、refresh token、
软删除回收站、异常 reopen、邮件邀请成员、微服务/K8s/MQ、生产级性能优化、移动端适配、E2E 全量覆盖（仅留 1 条 P2 用例）。

---

## 附录 D · 开工前置准备清单（含本机实测结果）

> 实测时间：开工前在本机执行环境审计（D: 盘工作区）；下表"现状"是测出来的，不是猜的。
> 结论：**只差 4 件小事就能开工**（P1 pnpm、P2 LLM Key、P3 MySQL 建库、P6 编辑器扩展），
> 其中 Docker 不是必需项（已改为路径 B，见 §5.1）。

### D-A 已就绪（无需处理）

| 项 | 实测 | 与基线要求 |
|---|---|---|
| Python | 3.12.10（`D:\python3.12`，唯一 3.12） | ✅ 与 §4 一致 |
| uv | 0.12.17 | ✅ 依赖管理与锁版本可用 |
| Node | v24.19.0（`D:\nodejs`） | ✅ 满足 Vite 6 |
| corepack | 0.35.0，可自动拉取 pnpm | ✅ 用它可以省掉全局安装 |
| git | 2.55.0，`user.name=zys2982-hash` / `user.email=zys2982@gmail.com` | ✅ 已配置提交身份 |
| MySQL | 8.0.46 服务 `MySQL80` 运行中，占用 3306 | ✅ 走 §5.1 路径 A |
| VS Code | 1.140.0，已装 Python + Pylance | ✅ 后端可开发 |
| 网络 | pypi / npm / api.deepseek.com / github 443 全部可达，无代理 | ✅ 无需配镜像 |
| 磁盘 | C: 51.1 GB 空闲 / D: 358.8 GB 空闲 | ✅ |
| 端口 | 8000、5173、3307 空闲（3306 被本机 MySQL 占用，19387 是 DSH GUI） | ✅ 不冲突 |
| Hyper-V/虚拟化 | VT 已在 BIOS 开启；`wsl.exe` 存在但**未安装任何发行版**；Docker 未安装 | ⚠️ 见 D4 |

### D-B 必须处理（阻塞开工，4 项）

| # | 事项 | 命令 / 做法 | 验收 |
|---|---|---|---|
| P1 | 安装 pnpm | `corepack enable`（或 `npm i -g pnpm@10`）；在 `frontend/package.json` 写 `"packageManager": "pnpm@<版本>"` 锁定 | `pnpm -v` 有输出 |
| P2 | 申请 LLM API Key | 到 DeepSeek 开放平台建 key，充值 ¥20（够演示几十次）；写入 `backend/.env` 的 `LLM_API_KEY`；**Key 不入库**（`.gitignore` 已含 `.env`） | `.env` 存在且 `AI_MODE=live` 时一次分析成功 |
| P3 | 确认 MySQL root 密码并建库建号 | 实测 `mysql -uroot` 无密码登录失败（exit 1），说明 root 有密码。用 root 执行 `scripts/init_db.sql`：建 `logiops`、`logiops_test` 两个 schema（`utf8mb4_0900_ai_ci`）+ 专用账号 `logiops`；**不要用 root 跑应用** | `mysql -ulogiops -p -h127.0.0.1 logiops -e "select 1"` 成功 |
| P4 | 录制 AI fixtures 的前置：先跑通 live 一次 | Key 到位后 `AI_RECORD=1 pytest -m live tests/live/` 录制 §11 三个任务的 fixtures，之后默认 `AI_MODE=replay` 离线可演示 | `backend/tests/fixtures/ai/*.json` 生成，`AI_MODE=replay` 下 Demo 全绿 |

### D-C 可选处理（不阻塞，按需）

| # | 事项 | 建议 |
|---|---|---|
| P5 | Docker Desktop + WSL2 | 本机 `HypervisorPresent=False`、无 WSL 发行版 → 装 Docker 需启用"虚拟机平台"+安装 WSL2 发行版+**重启**，C 盘约再占 3–6 GB。**建议放到阶段 4 之后再装**：先用 §5.1 路径 A 开发，最后若要演示"一键容器化"再装 |
| P6 | VS Code 扩展 | 补装：`charliermarsh.ruff`、`Vue.volar`、`dbaeumer.vscode-eslint`、`esbenp.prettier-vscode`（可选）、`humao.rest-client`（可选）。在 `.vscode/settings.json` 里指定"Python 用 ruff 格式化、Vue/TS 用 Prettier、保存时 format + organizeImports" |
| P7 | 录屏工具 | OBS 与本机 ffmpeg 均未检测到。用 Win11 自带 `Win+G`（Xbox Game Bar）录 5 分钟 Demo 即可；要更清晰再装 OBS |
| P8 | `gh` CLI / `winget` 未在 PATH | 建远程仓库用浏览器 + `git remote add` 即可；`winget` 需手动加到 PATH（`%LOCALAPPDATA%\Microsoft\WindowsApps`），或直接下安装包 |
| P9 | 代码托管 | 建一个 GitHub 仓库（建议 public，面试官能直接看）+ 至少 1 条 GitHub Actions 工作流（lint + pytest + MySQL service），让 README 顶部有 CI 徽章 |

### D-D 开工前必须"拍板"的事（决策已默认好，只需你点头）

1. 目录与命名：`D:\workspace1\LogiOps`，仓库名 `logiops`，中文名"物流异常协同平台"（附录 B1）
2. 模型与端点：`deepseek-chat` / `https://api.deepseek.com/v1`（附录 B2）——若要换通义/OpenAI，只改 `.env`
3. 数据库策略：默认走**本机 MySQL 3306**（路径 A），`docker-compose.yml` 仍作为交付物保留（附录 B2 衍生决策，本次新增）
4. 是否生成脚手架代码（附录 B5）——建议现在就生成，D1 当天就能有可运行骨架
5. 演示视频与简历口径（附录 B3/B4）

### D-E 开工后头一天（第 1 天）的验收物

```text
1) 仓库结构 + README 首屏（三条启动命令 + 有意省略清单）
2) pyproject.toml（uv 锁定）+ ruff/pytest 配置 + GitHub Actions
3) docker-compose.yml + scripts/init_db.sql + scripts/dev.ps1（默认路径 A）
4) 22 个 SQLAlchemy Model + Alembic 首个迁移（upgrade/downgrade 都能跑）
5) GET /healthz 返回 {status, db, clock_mode, ai_mode}，前端空白壳能连上 /api/v1
6) seed --reset --demo 幂等成功；pytest 全绿（此时只有 healthz 与规则单测）
```

---


