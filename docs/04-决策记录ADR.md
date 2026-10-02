# 04 · 决策记录 ADR（15 条 + 被否方案）

> **唯一真相**：[`00-项目基线-MVP.md`](00-项目基线-MVP.md) **附录 A**（A1–A15 完整版，含"理由/代价/被否方案"）、**附录 C**（C1 补齐 / C2 与原稿的修改 / C3 删减）。
> 本文是**索引与一句话摘要**，评审批注写在这里；正文不复制附录 A 的表格。
> 约定：新增决策追加编号 A16、A17…，一条决策一个"代价"，**必须写被否方案**（否则面试会被追问"为什么不选 X"）。

## 1. 一览（一句话版，点编号回附录 A 看完整论证）

| 编号 | 决策 | 一句话理由 | 代价 |
|---|---|---|---|
| A1 | 同步 SQLAlchemy 2.0 | 单人友好，瓶颈不在 DB | 无 async 吞吐优势 |
| A2 | 无 MQ：事件同步触发 + 可推进时钟 | 事件单一、行为可复现可测 | 不是"生产级异步" |
| A3 | 风险等级由规则表决定，LLM 只解释与建议 | 可解释、可测、可审计 | 规则需人工维护 |
| A4 | 知识检索用 MySQL ngram 全文，不用向量库 | 语料小、零额外服务 | 语义召回弱于 embedding |
| A5 | AI 全只读 + approval + 后端执行器 | 解决原稿"写操作需人工确认"与写工具的矛盾 | 多一张表与一次交互 |
| A6 | 不用 LangGraph，手写有界 tool 循环（≤14 步/90s） | 单场景线性流程，步序可断言 | 复杂编排能力有限 |
| A7 | 前端轮询（1.5s）而非 SSE/WS | 5 行代码、与同步栈一致、断线易恢复 | 有 1.5s 延迟 |
| A8 | 固定基准日 + ReplayClock | Demo 与测试完全确定性 | 多一层时钟抽象（约 30 行） |
| A9 | 存 UTC，展示 Asia/Shanghai | 避免时区类 bug | 前端多一层转换 |
| A10 | 枚举用 VARCHAR + StrEnum，不用 MySQL ENUM | 加减枚举值不改表 | 失去 DB 层约束 |
| A11 | 异常 CLOSED 终态，不提供 reopen | 状态机少一环少一半 bug | 极端场景需新建异常 |
| A12 | 错误体 `{error:{code,message,details}}` | 更 RESTful，拦截器统一处理 | 与部分国内团队习惯不同 |
| A13 | 默认 `AI_MODE=replay` | 现场确定性优先 | 需维护 fixtures |
| A14 | SLA 日历 24×7 不考虑节假日 | 演示范围内不需要，且规则可解释 | 与真实业务有差距 |
| A15 | 单仓 monorepo + uv | 依赖锁定与安装体验好 | 非大团队标准 |

**被否方案集中看**：async SQLAlchemy、Celery/APScheduler/RabbitMQ、纯 LLM 定级、Qdrant+embedding、AI 直接调写工具、LangGraph、SSE/WebSocket、直接 `now()`、全存本地时间、MySQL ENUM、reopen、统一包封 200+code、默认 live、工作日历表、多仓 + Poetry。

## 2. 与原稿的差异（附录 C2 摘要）

| 原稿问题 | 本基线怎么改 | 不这么改会怎样 |
|---|---|---|
| `create_followup_task` 是 AI Tool，又要"人工确认" | 移出 Tool 列表：AI 只输出建议 → approval → 后端执行（A5） | 自相矛盾，审计链断裂 |
| Step 1 用绝对时间又要求每次可复现 | 固定基准日 + ReplayClock + `tick`（A8） | 两次演示结果不同，无法 diff |
| 风险等级"必须规则参与"但没给规则 | 给出完整评分表 + `risk_factors`（§8.6） | 无法实现、无法测试 |
| SLA 只有"最大允许延迟" | 规则匹配 + 承诺时刻计算 + 违约判定 + 日历口径（§8.3） | 业务流程处处依赖却无定义 |
| "订单完成后异常自动关闭" + `POST /close` | 明确 RESOLVED/CLOSED 语义 + 自动/人工两条路径 + 不可 reopen（A11） | 状态语义歧义 |
| 集成测试"pytest 全部通过" | 分层测试 + 覆盖率阈值 + FakeLLM + 两次 diff（§14） | "通过"不可验证 |
| 三张设计表还是待办 | §11.2 一次给全 | 第一阶段交付物缺失 |

**明确不做（附录 C3 / 基线 §3.2）**：向量库、ECharts 大屏、Excel 导出、i18n、SSO/OAuth、refresh token、软删除回收站、异常 reopen、邮件邀请、微服务/K8s/MQ、生产级性能优化、移动端适配、E2E 全量覆盖。

## 3. 新增/修改决策的写法（模板，复制即用）

```markdown
### A16 · <决策标题>

- **背景**：<什么冲突/约束触发了这个决策>
- **决策**：<我们做什么，一句话>
- **理由**：<为什么（可验证的收益）>
- **代价**：<放弃了什么、需要额外维护什么>
- **被否方案**：<方案名 + 为什么否决>
- **影响面**：<模块/接口/文档章节；是否需要迁移>
```

写完后同步：基线附录 A、本文第 1 节表格、必要时 README「有意省略清单」。

## 4. 可执行校验命令

```powershell
# 决策落到代码的"锚点"检查（每条都能跑）
cd backend

# A3：等级由规则算，AI 不改 level —— risk 规则单测（分支 100%）
uv run pytest tests/unit -q -k "risk"

# A5：AI 无写工具（AI 层不得 import Repository/Session）
Get-ChildItem app\ai -Recurse -Filter *.py | Select-String -Pattern "from app\.repositories|import Session" | ForEach-Object { "VIOLATION $($_.Path):$($_.LineNumber)" }

# A6：有界循环步数上限（≤14）与超时
uv run pytest tests/ai -q -k "step or timeout"

# A8/A13：确定性 —— 同一 Demo 两次运行结果一致
pwsh -File ..\scripts\acceptance.ps1        # 归一化比对，不一致 exit 1

# A9：接口一律 UTC（找非 UTC 时间字符串）
uv run python -c "import json;from app.main import app;spec=json.dumps(app.openapi(),ensure_ascii=False);print('+08:00' in spec, 'Asia/Shanghai' in spec)"

# A10：确认没有用 DB ENUM（应为 0 行）
Get-ChildItem app\models -Filter *.py | Select-String -Pattern "\bEnum\(" | ForEach-Object { $_.Line }

# A12：错误体形状
uv run pytest tests/api -q -k "error"
```

## 5. 评审提示（面试常见追问）

- "为什么不用向量库？" → 语料 5 篇 / 约 40 chunk，ngram 全文足够且可解释；接口保留可换（A4）。
- "为什么不用 LangGraph？" → 单场景线性流程，手写循环 14 步上限更透明、步序可断言（A6）。
- "为什么同步栈？" → 演示规模下瓶颈不在 DB；异步会显著抬高学习与排错成本（A1）。
- "LLM 会不会瞎编 ETA？" → 不会：等级由规则算，草稿事实逐字段比对，越界即拦截降级（§11.1 第 2/3 条 + S3/S4）。
