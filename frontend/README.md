# LogiOps 前端（Vue 3 + TypeScript + Vite + Element Plus）

物流异常协同平台的前端工程。对接基线文档 `docs/00-项目基线-MVP.md` §10（接口契约）、§12（前端设计）、§7.2（枚举）、§11.2（AI 输出结构）、§13（Demo 数据）。

---

## 1. 启动命令

前置：Node ≥ 20（本机实测 v24.19.0，位于 `D:\nodejs`）。**本机 pnpm 未全局安装**，一律用 corepack 调用（不要执行 `corepack enable`）：

```powershell
# 1) 安装依赖（本地已验证：退出码 0）
corepack pnpm install

# 2) 类型检查（vue-tsc，本地已验证：退出码 0）
corepack pnpm type-check

# 3) 生产构建（Vite 6，本地已验证：退出码 0，产物 dist/）
corepack pnpm build

# 4) 开发服务器（默认 http://127.0.0.1:5173，/api 代理到后端）
corepack pnpm dev

# 5) 预览构建产物
corepack pnpm preview
```

### 本机两个已实测的坑（重要）

1. **pnpm 12 的安装脚本 allow-list**：`esbuild`（下载平台二进制）和 `vue-demi`（生成 Vue 版本适配文件）的 postinstall 必须允许执行，否则 `vite dev/build` 会因为缺少 esbuild 原生二进制而失败，且 `corepack pnpm install` 直接以 `ERR_PNPM_IGNORED_BUILDS` 非零退出。**allow-list 必须写在 `pnpm-workspace.yaml`**（这是实测有效的唯一位置）：

   ```yaml
   # pnpm-workspace.yaml
   allowBuilds:
     esbuild: true
     vue-demi: true
   ```

   > 旧写法（`.npmrc` 里的数组型 `onlyBuiltDependencies`）在 pnpm 12 上会被忽略并继续报 `ERR_PNPM_IGNORED_BUILDS`；
   > 写成映射型 `allowBuilds` 放在 `.npmrc` 里同样不生效（实测），所以这两个文件分工是：`pnpm-workspace.yaml` 管构建脚本白名单，`.npmrc` 只放 `strict-peer-dependencies` 等常规配置。
   > 若曾用旧配置装过半成品依赖，执行一次 `corepack pnpm install --force` 重新执行脚本。

2. **构建/自测临时目录**：本机沙箱下 esbuild 需要可写的 TEMP，系统 TEMP 可能触发 `Access is denied`。构建前把 TEMP 指到工程内的 `.tmp/`（`.gitignore` 已忽略）：

   ```powershell
   New-Item -ItemType Directory -Force .tmp | Out-Null
   $env:TEMP = (Resolve-Path .tmp).Path; $env:TMP = $env:TEMP
   corepack pnpm build
   ```

   `vite.config.ts` 的 `server.watch.ignored` 用正则忽略 `.tmp/`、`.*.tmpdir/`、`*.tmp`，避免 Windows 文件占用（EBUSY）把 dev server 的 watcher 打崩（实测：编辑 `src/` 文件后只输出 `[vite] (client) page reload ...`，进程存活；`.tmp/` 不参与交付）。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `VITE_BACKEND_ORIGIN` | `http://127.0.0.1:8000` | dev server 的 `/api` 代理目标（`vite.config.ts` 读取的是 `process.env.VITE_BACKEND_ORIGIN`）。 |
| `VITE_USE_MOCKS` | `false` | 是否允许本地 fixture 兜底。**默认关闭**：真实错误（含 404）必须暴露；仅在无后端时想看骨架，才在 `.env.local` 显式设 `true`。生产构建设为 `true` 会**直接构建失败**。 |

后端地址：`http://127.0.0.1:8000`，请求前缀统一 `/api/v1`（`src/api/request.ts` 的 `baseURL`）。

---

## 2. 页面清单（路由）

| 路由 | 页面 | 说明 |
|---|---|---|
| `/login` `/register` | 登录 / 注册 | **不展示演示账号/口令**（2026-10-06 起，公网部署要求）：表单不预填、无一键填充；账号见 `docs/09` 或 `docs/10` |
| `/dashboard` | 总览 | 6 张统计卡 + 近 7 天异常/违约趋势 + 高风险异常 Top5（`risk_score desc`） |
| `/orders` | 订单列表 | 订单号/状态/客户筛选 + 分页 |
| `/orders/:id` | 订单详情 | 订单与 SLA 快照、轨迹时间线、派车（`order.manage`）、录入轨迹（`tracking.write`，写入后触发 ETA 重算与异常检测）、关联异常 |
| `/exceptions` | 异常中心 | 列：订单号/客户/等级/SLA 影响/状态/更新时间（不展示异常类型列；「等级」= **当前风险**，已解决/已关闭显示 0）；搜索、状态/等级/SLA 筛选、排序（默认风险分倒序）、分页、手工建单（`exception.create`，**不选类型**） |
| `/exceptions/:id` | **异常详情（门面）** | 三列布局，见 §3 |
| `/customers` `/carriers` `/vehicles` `/drivers` `/sla-rules` | 主数据 CRUD | 只有对应 `*.manage` 权限才渲染新增/编辑按钮，否则整列显示只读 |
| `/knowledge` | 知识库 | 文档与分片浏览、分片内搜索、重建索引（`knowledge.manage`） |
| `/audit` | 审计日志 | action/resource/actor 筛选 + 详情抽屉（before/after JSON） |
| `/members` | 成员与角色 | 成员增删改角色（`member.manage`）+ 角色×操作矩阵表 |
| `/demo` | 演示工具 | 虚拟时钟：快进 tick / 快进到结案 / 重置 seed；AI 模式只读展示；操作记录 |
| `/:pathMatch(.*)*` | 404 | 未实现路由兜底 |

路由守卫（`src/router/index.ts`）：未登录访问业务路由 → 跳 `/login?redirect=...`；已登录访问公开页 → 跳 `/dashboard`；`meta.perm` 不满足 → 提示并回总览。

---

## 3. 异常详情页（三列，`/exceptions/:id`）

```
┌ 顶部：订单号 车辆故障 [严重·4分] 处理中 SLA违约 处理人 异常编号 ┊ 确认/处理完成/指派/关闭/强制关闭
├ 左列（事实）                        │ 中列（AI 面板）                    │ 右列（协同）
│ · 订单信息（客户/等级/起终/车辆/司机）│ · [AI 分析此异常] + 状态标签        │ · 承运商消息（原文 + 解析结果对照表）
│ · 运输轨迹时间线（异常标记）         │ · 7 步工具调用逐条点亮              │ · 客户通知（草稿/编辑/批准/复制/模拟发送/跳过）
│ · SLA 影响卡（承诺/当前/预计/延误）  │ · 结论：摘要/根因/影响/建议          │ · 跟进任务（勾选完成 + 新建）
│ · 风险等级 + risk_factors 逐项说明   │ · 待确认事项 / 证据来源（点击跳原文）│ · 操作记录时间线（exception_event）
│                                    │ · 审批单卡片：AI 原值 vs 人工值 diff │
```

关键交互（与 §12.2 对齐）：

1. **AI 分析**：`POST /exceptions/{id}/analyze`（带 `expected_version`）→ **202 新建 / 200 命中复用都视为成功**，随后每 **1.5s** 轮询 `GET /ai-analyses/{id}`，`READY`/`FAILED` 停止，**90s** 超时提示；步骤按后端返回的 `steps[].status` 逐条点亮（不做假动画）；`FAILED` 展示错误与 `[重试]`（`POST /ai-analyses/{id}/retry`）。
2. **审批**：编辑后必须展示 diff（AI 原值 vs 人工值，人工改动字段高亮），批准前二次确认；驳回必填原因；支持批量批准（逐条执行并汇总每条结果）；`FAILED` 可 `[重新执行]`。
3. **乐观锁**：`PATCH /exceptions/{id}`、`resolve`、`close`、`approve`、`reject` **必须带 `expected_version`**；收到 409 提示"数据已被他人更新，已为你刷新"并重新拉取（拦截器统一处理 + 页面主动 `loadAll()`）。
4. **权限**：按钮级 `v-if="can('exception.handle')"` 等，无权限直接不渲染；前端权限码与后端 `backend/app/core/permissions.py` 的 `Perm` 枚举逐字对齐。
5. **时间**：全部走 `utils/datetime.ts` 把 UTC 转 `Asia/Shanghai`；相对时间用 dayjs relativeTime 中文。
6. **演示态**：顶部横幅固定显示 `AI 模式：回放/实时`、`业务时间：2026-09-30 19:05（Asia/Shanghai）`、基准日与时钟偏移。

---

## 4. 目录结构

```
src/
├── api/            # 每个模块一个文件，统一走 request.ts
│   ├── request.ts      # baseURL=/api/v1、Bearer token、X-Workspace-Id、错误体解析、401 登出、409 提示、fixture 兜底
│   ├── runtime.ts      # 拦截器 ↔ Pinia 的运行时桥（避免循环依赖）
│   ├── auth.ts workspaces.ts master.ts orders.ts exceptions.ts system.ts demo.ts
├── components/     # RiskTag / PanelCard / SlaImpactCard / TrackingTimeline / ExceptionTimeline
│                   # AiPanel / ApprovalCard / EvidenceList / CarrierMessagePanel / NotificationPanel
│                   # FollowupPanel / ExceptionTable / layout/{AppLayout,DemoBanner}
├── composables/    # useAiAnalysis（analyze + 1.5s 轮询 + 90s 超时）
├── mocks/          # fixtures.ts（§13 案例数据）+ registry.ts（按 §10 路径的本地兜底路由）
├── router/         # 路由与守卫
├── stores/         # auth(token/user/permissions) / workspace / demo(演示态) / ui
├── types/          # enums.ts（§7.2 枚举字典 + Perm）· api.ts（§10/§7.4 契约类型，snake_case）
├── utils/          # datetime.ts（UTC→Asia/Shanghai）· format.ts（枚举文案与颜色）· permissions.ts · storage.ts
└── views/          # 16 个页面 + NotFoundView
```

---

## 5. 与后端的约定（必须遵守）

- **前缀** `/api/v1`；**字段一律 snake_case**，前端不做运行时转换。
- **鉴权** `Authorization: Bearer <jwt>`；**工作区** `X-Workspace-Id: <id>`（未选工作区时不发送）。
- **成功响应**直接返回资源对象或分页对象，不套 `code/data`；分页 `{items,total,page,page_size}`（`page` 1 起，`page_size` ≤ 100）。
- **排序** `?sort=-risk_score,created_at`（`-` 为倒序）。
- **时间**入参出参均为 ISO8601 UTC（`2026-09-30T11:05:00Z`），前端负责本地化展示。
- **错误体** `{"error":{"code","message","details":{}}}`：拦截器按 §10.2 错误码表映射中文文案；401 登出；409 统一提示"数据已被他人更新，已为你刷新"。
- **部分接口返回形状**（**已用真实后端逐端点核对**，与 §10 文档有差异的地方以这里为准；前端两种都兼容）：
  - `GET /exceptions` → 分页对象，item 是**扁平摘要**（`order_no/customer_name/vehicle_plate/level/risk_score/sla_*/risk_factors/...`），嵌套 `order/customer/vehicle/sla/counts/latest_analysis` 在列表里是 `null`；
  - `GET /exceptions/{id}` → 扁平字段 + 嵌套 `order/customer/vehicle/sla/tracking_events/latest_carrier_message/history/latest_analysis/counts`；注意顶层 `order_no/customer_name/vehicle_plate/customer_level` 为空值，**要用嵌套对象**；
  - `GET /orders` 与 `GET /orders/{id}` → 扁平字段（`customer_name/customer_code/customer_level/vehicle_plate/carrier_name/driver_name`）+ `sla` 快照 + `open_exception`；
  - `GET /orders/{id}/tracking-events`、`/exceptions/{id}/events`、`/exceptions/{id}/followups` → `{items,total[,page,page_size]}`；
  - `GET /orders/{id}/exceptions`、`/exceptions/{id}/approvals`、`/notifications`、`/messages`、`/sla-rules`、`/workspaces/current/members` → **平数组**（SLA 规则不是分页）；
  - `GET /knowledge/docs` → `{items,total,chunk_total}`；`POST /knowledge/reindex` → `{indexed,docs,chunks,warning}`（warning 有值仍 200）；
  - `GET /ai-analyses/{id}` → `{status, steps:[{step_no,step_type,tool_name,args,status,result_summary,duration_ms}], output:{summary,root_cause,impact:{delay_minutes,sla_breached},suggestions,open_questions,evidence_refs}, risk_level_calculated, is_replay, model, prompt_version, tokens_*, latency_ms}`；**字段名是 `output`**（不是 `output_json`）；
  - `GET /exceptions/{id}` 的 `latest_analysis` 是**摘要 + 部分结论**（有 `risk_level_calculated/root_cause/impact/suggestions`，但**没有 `steps`/`output`**）→ 前端会自动再拉一次 `GET /ai-analyses/{id}` 补全步骤；
  - 审批单字段名：`ai_payload / final_payload / diff / execution_result`（**不带 `_json` 后缀**）；`diff.fields` 是 `{字段: {ai, final}}` 的 map；`POST /approvals/{id}/approve|reject` 返回 `{id,status,diff,execution_result,error_message,reject_reason}`；
  - 承运商消息解析结果字段名是 `parse_result`；异常时间线明细字段名是 `detail`；`ai_analysis_step` 的参数名是 `args`；
  - `GET /dashboard/summary` 用 `pending`（不是 `pending_handling`），另有 `delayed_orders/exceptions_total/sla_breached_open/by_level/by_status/high_risk_top/trend`；`GET /dashboard/trend` 的点是 `{date,detected,breached,resolved,closed}`；
  - `GET /auth/me` → `{user,role,permissions,workspace_id,workspace,workspaces}`（当前工作区是单数 `workspace`）；`GET /workspaces` → 数组（含 `role/member_count`）；
  - 主数据过滤参数：`customers ?q&level&status`、`carriers ?q&status`、`vehicles ?plate_no&status&carrier_id&driver_id`、`drivers ?q&status&carrier_id`；
  - `GET /healthz` 在 `/api/v1/healthz`（经 baseURL 访问），`db` 是布尔。
- **AI 分析**：`POST /exceptions/{id}/analyze` 的 **202（新建）/ 200（15 分钟内 input_hash 复用的 READY 结果）都算成功**，取 `analysis_id` 后开始轮询；`AI_MODE=replay` 时 202 响应里的 `status` 直接是 `READY`（同步完成），前端轮询一次即停；`409 AI_ANALYSIS_IN_PROGRESS` 的 `details.analysis_id` 会被前端直接接管轮询；状态机不允许时会 409 `STATE_TRANSITION_INVALID`（`details.kind/from/to/allowed`）。
- **Demo**：`GET /demo/state`（`base_date/offset_minutes/now_utc/clock_mode/ai_mode/workspace_id/seed_available`）、`POST /demo/actions/tick|advance-to-less|reset`（已与 `backend/app/api/v1/demo.py` 对齐；当前 tick/advance 有后端缺陷，见 §8.1）。
- **写入接口**：`PATCH` 主数据 / `resolve` / `close` / `approve` / `reject` / `PATCH /exceptions/{id}` 都要带 `expected_version`；缺失 422 `VALIDATION_ERROR`（`details.fields`），不匹配 409 `OPTIMISTIC_LOCK_CONFLICT`。
- **权限码**：`dashboard.view / customer.view … / exception.handle / exception.force_close / approval.decide / followup.write / notification.approve / member.manage / knowledge.manage / demo.control` 等，见 `src/types/enums.ts` 的 `Perm` 与 `ROLE_PERMS`（与 `backend/app/core/permissions.py` 逐字对齐）。

---

## 6. 后端未就绪时的本地兜底（fixture）

后端接口可能尚未实现（返回 404 / 网络不可达 / 5xx）时，`src/api/request.ts` 会自动回落到 `src/mocks/registry.ts`：

- 触发条件：**显式** `VITE_USE_MOCKS=true` 且出现网络错误或 5xx（`AI_OUTPUT_INVALID`/`LLM_UNAVAILABLE` 除外）。
  **404 永不兜底**——本系统里 404 有语义（跨租户越权、资源不存在），兜底会把真实结果渲染成假数据；
  **401/403/409 这类后端明确回答的错误也不兜底**（否则会掩盖真实问题：例如 token 失效时应提示重新登录，而不是显示演示数据）。
- 兜底数据：`src/mocks/fixtures.ts` —— CASE-A 主案例（权威数值：延误 300 分钟 / `sla_breached=true` / `risk_score=4` / CRITICAL）、50 条列表数据、8 步 AI 分析（后端上限 14 步）、3 张待决策审批单（含 diff 形状）、承运商消息原文/解析对照、通知、跟进任务、操作记录、审计、知识库分片、Dashboard。
- 兜底行为：`POST /exceptions/{id}/analyze` 返回 `analysis_id` 后，`GET /ai-analyses/{id}` 会按轮询次数**逐条点亮 steps**（每 1.5s 一步，约 10.5s 后 `READY`），用于验证前端轮询与进度渲染链路。真实后端在 `AI_MODE=replay` 下分析是**同步完成**的（202 响应里就是 `READY`），因此真实模式看不到逐条点亮——这符合 §12.2-1"不做假动画"的要求。
- 首次兜底会提示一次"后端未就绪，已使用本地演示数据（fixture）渲染页面"；横幅还会显示"本地演示数据"标签。
- 注意：fixture 全部为虚构数据；**默认关闭**，只有显式 `VITE_USE_MOCKS=true` 才会启用（生产构建禁止开启）。

---

## 7. 本轮验收记录（本机实测）

### 7.1 工程命令

| 命令 | 结果 |
|---|---|
| `corepack pnpm install` | ✅ 退出码 0（依赖：vue 3.5.43 / vite 6.4.3 / element-plus 2.14.6 / pinia 2.3.1 / vue-router 4.6.4 / axios 1.20.0 / dayjs 1.11.23；devDeps：vue-tsc 2.2.12 / typescript 5.6.3） |
| `corepack pnpm type-check` | ✅ 退出码 0（vue-tsc 2.2.12，strict + noUnusedLocals） |
| `corepack pnpm build` | ✅ 退出码 0，`✓ 1797 modules transformed`，产物 `dist/`（需先把 TEMP 指向 `.tmp/`，见 §1） |
| `corepack pnpm dev` | ✅ 启动于 `http://127.0.0.1:5173`（`VITE v6.4.3 ready in 809 ms`），页面 HTTP 200；验证后已关闭，未占用 5173 |

版本回退说明：`package.json` 使用 `^` 语义化范围，实际安装到的版本见上表；未发生需要回退的场景（Element Plus 与 Vite 均按预期解析）。

### 7.2 真实后端联调（`VITE_USE_MOCKS=false`，Edge headless + CDP）

联调环境：`DATABASE_URL=sqlite+pysqlite:///./dev.db` → `python -m app.cli init-schema` → `python -m app.seed --reset --demo` → `uvicorn app.main:app --port 8000`；账号 `operator@logiops.dev / Demo@12345`（OPERATOR，19 个 permissions）。

```
PASS /dashboard  /exceptions  /orders  /orders/21  /customers  /carriers  /vehicles
PASS /drivers    /sla-rules   /knowledge  /audit  /members  /demo
SUMMARY: 13/13 页面干净（真实接口 + 控制台 0 error）
```

关键链路：

- 异常中心首行 = CASE-A（`SO20260930021` / `EX20260930001` / 远洋集团 / 严重·4 分 / 已违约 / 处理中），排序 `-risk_score,-created_at`。
- 异常详情：订单信息（订单号/客户/等级/起终地/车辆/司机/承运商，取自嵌套 `order`/`customer`/`vehicle`）、运输轨迹时间线、SLA 影响卡、`risk_factors` 四项、承运商消息原文 + 解析对照表、操作记录时间线全部渲染。
- AI 分析：页面加载即自动回填最新分析（`8/8` 步 + 结论摘要 + 根因 + 影响 + 建议→审批单 + 证据来源）；点击「重新分析」走 `POST /exceptions/1/analyze`（202）→ 轮询 `GET /ai-analyses/{id}`。
- 审批：`POST /approvals/{id}/approve` 人工改 `reason` → 返回 `diff.changed=["reason"]` → 详情页卡片显示"已执行"与"人工修改了：变更原因"。
- 跟进任务/通知：`GET /exceptions/{id}/followups` 为 `{items,total}`、`/notifications` 为平数组，均正常解析。

### 7.3 fixture 兜底模式（后端关闭）

```
SUMMARY: 11/11 pages clean（errors=0）
列表首行: SO20260930021 | EX20260930001 | 远洋集团 | VIP · 津A·12345 | 车辆故障 | 严重·4 分 | 已违约 | 延误 4 小时 30 分钟 | 处理中
详情校验: orderNo/critical/breached/延误 4 小时 30 分钟/promised 2026-10-01 01:30/expected 2026-10-01 06:00/8-8 步/结论/risk_factors/parse_result/审批单 全部为 true
```

即"无后端兜底视图"与 §13.3「CASE-A 权威数值」讲同一个故事。


---

## 8. 已知限制、后端待修项与未完成项

### 8.1 后端待修项（联调实测发现，前端已能容错，但建议后端修）

| # | 现象 | 影响 | 前端现状 |
|---|---|---|---|
| 1 | ~~`POST /demo/actions/tick` 与 `/advance-to-less` 恒 409~~ **已由后端修复并复核通过**（tick/advance 均 200，advance 能把 `SO20260930021 / EX20260930001` 推到 `CLOSED`） | — | `/demo` 页正常；`stores/demo.ts` 仍保留"后端明确拒绝时不伪造本地推进"的保护 |
| 2 | ~~`CREATE_FOLLOWUP` 审批执行失败（`datetime is not JSON serializable`）~~ **已由后端修复并复核通过**（`approve` 返回 `EXECUTED`） | — | 审批卡片正常显示 `已执行` + diff |
| 3 | ~~CASE-A seed 与文档不一致~~ **已解决**：Lead 复核真库后把 §13.3 改为真库实测值（`dispatched 2026-09-29T17:30Z`、`promised 2026-09-30T17:30Z`、`expected 2026-09-30T22:00Z`、`delay 270`），`fixtures.ts` 已同步该组数值 | 无（fixture 与真接口现在讲同一个故事：延误 4 小时 30 分钟） | 已对齐 |
| 4 | `GET /exceptions?q=...` 的 `q` 被忽略（传 `q=SO20260930021` 与不传时 total 相同） | 异常中心搜索框无效 | 仍按 §10 契约发送 `q`，等后端实现 |
| 5 | `GET /knowledge/docs/{id}` 恒 404 `RESOURCE_NOT_FOUND` | 知识库页看不到分片原文 | `api/system.ts` 降级为空分片 + 空态提示；无后端时用 fixture 分片 |
| 6 | `GET /exceptions/{id}` 的顶层 `order_no / customer_name / vehicle_plate / customer_level` 是空值，真实数据只在嵌套 `order`/`customer`/`vehicle` 里；也没有 `assigned_to_name`、`carrier`、`open_approval_count` | 直接取顶层字段会显示"—" | 前端已改为"嵌套优先、顶层兜底"，用成员列表映射 `assigned_to` → 姓名 |
| 7 | `/auth/register` 返回 `{user, default_workspace_id}`（与 §10 的"→201 user"不同） | 无 | 前端忽略响应体，注册后自动登录 |
| 8 | `POST /orders/{id}/tracking-events` / `resolve` / `close` 等写入后的响应在部分场景下 level 会重算（实测 resolve 响应里 `level` 从 CRITICAL 变 MEDIUM，而 `risk_score` 仍为 4） | 需后端确认等级是否应随 ETA 重算下降 | 前端每次写操作后重新拉取详情，展示后端权威值 |

### 8.2 其他限制

1. **真实模式看不到 AI 步骤"逐条点亮"**：后端 `AI_MODE=replay` 下分析同步完成（202 响应即 `READY`），前端立即停止轮询并按真实 `steps` 渲染 8/8；逐条点亮只在 fixture 兜底模式下可观察（`AI_MODE=live` 或后端异步化后也会出现）。
2. **图表为轻量自绘**：趋势图用 CSS 柱状实现，未引入 ECharts（保持依赖清单与基线一致）。
3. **未做单元测试**：本项目前端未引入 Vitest（基线测试分层聚焦后端 pytest）；类型检查 + 构建 + 双模式（真实后端 / fixture）挂载自测是三道关卡。
4. **`approveNotification`**：当前实现走 `PATCH /notifications/{id}` 保存确认结果，"批准"语义最终由后端的通知审批链承担；若后端新增专用接口，需同步替换。
5. **容器化**：`frontend/Dockerfile`（多阶段 node:24-alpine → nginx:alpine，含 SPA history 回退与 `/api` 反代到 `backend:8000`）已提供，但本机未安装 Docker，**未实际构建验证**；使用前请确认 `.npmrc` 的 `allowBuilds` 会随构建上下文一起拷进镜像（Dockerfile 已 `COPY package.json pnpm-lock.yaml* .npmrc* ./`）。

### 8.3 开发排障：改了 `.vue` 但界面没变 / 点了没反应

本机已实测踩到 **3 次**：Vite dev server 返回**陈旧编译产物**（源码已改，浏览器拿到的模块还是旧模板）。

典型症状：**按钮点了没反应**。实测那一例是——模块里有「新建订单」按钮和 `openCreate`，
但**没有弹窗**（`grep dialog` 命中 0 处），所以点击只把 `createVisible` 置 true，
界面自然什么也不发生。**只有重启 dev server 才能恢复**（`Ctrl+Shift+R` 也不够，因为服务端产物本身就是旧的）。

判断方法（不用看浏览器，直接问服务端要模块）：

```powershell
# 把 OrderListView.vue 换成你刚改的文件，检查新加的标识串是否出现在产物里
(Invoke-WebRequest 'http://127.0.0.1:5173/src/views/OrderListView.vue').Content -match '新建运输订单'
# False → 产物陈旧：重启 dev server（见下）
```

处置：

```powershell
# 1) 结束占用 5173 的进程（taskkill /F /PID <pid>，pid 见 netstat -ano | findstr :5173）
# 2) 清依赖预构建缓存并重启
Remove-Item node_modules\.vite -Recurse -Force
corepack pnpm dev --host 127.0.0.1 --port 5173
```

> 推测原因：工具/编辑器以**原子替换（写临时文件再 rename）**方式保存 `.vue`，Windows 上 watcher 偶尔丢事件，
> 于是该文件的转换结果一直是旧的。所以**每次改前端后请按上面的方法核一次产物**，不要只依赖热更新。


