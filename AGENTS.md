# TryInvoice 项目开发规范（AGENTS.md）

本文件为 `tryinvoice` 项目的根目录协作规范，适用于 **Google Antigravity** 与 **Ralph Orchestrator**。

---

## 1. 技术栈与目录结构

### 技术栈基线
- **iOS 客户端（V1 首发）**：Swift 5.10+ / SwiftUI（iOS 17+）、`VisionKit`（文档扫描）、`Vision`（`VNRecognizeTextRequest` 端侧免费离线 OCR）、`SwiftData`（离线存储）。
- **后端服务**：Python 3.14+（统一使用 **`uv`** 管理）、FastAPI、Pydantic v2、`PyMuPDF`、`openpyxl`。
- **云端基础设施**：Supabase（PostgreSQL 15+ / Auth / RLS）、Cloudflare R2（或 Supabase Storage，S3 兼容预签名直传/下载）。
- **OCR / 提取分层策略**：优先使用 iPhone 原生 `Vision` 端侧 OCR 与 PDF `PyMuPDF` 确定性文本提取；仅在复杂小票或低置信度场景通过 OpenAI / Codex 兼容 API 兜底。
- **OpenAI 云端兜底网关**：项目可使用用户已有的 OpenAI API Credit；需要云端模型兜底时，优先由 **Cloudflare Worker** 作为受控网关发起 OpenAI API 调用，FastAPI 后端调用 Worker，iOS 客户端不得直连 OpenAI。OpenAI API Key 只能存放于 Cloudflare Worker Secret，不得下发客户端、写入仓库或进入日志。Worker 必须具备服务间鉴权、请求限流、超时与重试上限、输入大小限制和敏感字段脱敏；不得记录原始票据、完整 OCR 文本或模型请求正文。该路径仅用于确定性提取失败或低置信度场景，禁止替代确定性规则计算。
- **未来 Web 端（契约预留）**：TypeScript（统一使用 **`pnpm`** 管理）、Next.js + React + shadcn/ui，部署于 Vercel，通过 `contracts/openapi.json` 生成 SDK。

### 约定目录结构
```text
tryinvoice/
├── AGENTS.md                 # 本规范文件
├── docs/
│   ├── hld.md                # 高层设计文档
│   ├── lld.md                # 详细设计文档
│   ├── milestones.md         # 里程碑与任务总览
│   └── tasks/                # 独立 Task 规格文档（Mx.y-<slug>.md）
├── contracts/
│   └── openapi.json          # FastAPI 导出的 OpenAPI 3.1 契约
├── backend/                  # Python 后端（uv 管理）
├── ios/                      # Xcode / SwiftUI iOS 工程
└── ralph/                    # Ralph Orchestrator 编排配置与包装器
```

---

## 2. 本地运行与测试命令

- **后端环境同步与测试**（`backend/` 建成后）：
  - `cd backend && uv sync`
  - `cd backend && uv run pytest`
  - `cd backend && uv run ruff check .`
- **iOS 构建与测试**（`ios/` 建成后）：
  - `xcodebuild test -scheme TryInvoice -destination 'platform=iOS Simulator,name=iPhone 16'`

---

## 3. 核心开发红线与约束

1. **全中文文档**：所有项目文档（`AGENTS.md`、`docs/` 下的 HLD/LLD/Milestones/Tasks）必须使用中文编写；代码标识符与文件名使用英文或标准命名。
2. **复用优先、确定性优先**：优先采用成熟框架、官方脚手架与标准组件；所有去重、配对、均分、小费剔除、分组、重命名与 Excel 公式生成必须由确定性程序完成，禁止用 LLM 替代确定性计算。
3. **禁止擅自变更技术栈或跨 Task 扩充**：严格按 `docs/hld.md`、`docs/lld.md` 与当前 Task 文档实施；严禁向已定义好的 Task 追加新需求。
4. **三级编号规范**：里程碑 `M1`..`Mn` → 任务 `M1.1`..`Mx.y` → 子任务 `M1.1-S1`..`Mx.y-Sz`。
5. **体积与测试红线**：
   - 单个 Task 总改动量控制 **≤ 2,000 行**；Subtask 建议 **≤ 800 行**。
   - 只保留“核心纯逻辑单测 + 关键路径集成/UI 测试”，禁止三层重复测试；新增测试代码满足 **`Test:Feat <= 1.3`** 或 **新增测试 `< 450` 行**（满足其一即可）。
6. **安全与凭证红线**：严禁将 API Key、JWT、Supabase Secret、R2 密钥、用户银行卡完整卡号/证件号/税号写入日志或提交至 Git；后端凭证通过 `.env`（已加入 `.gitignore`）注入，Cloudflare Worker 凭证通过平台 Secret 注入，日志中敏感字段必须脱敏。
7. **OpenAI 调用边界**：OpenAI API 仅作为 Tier 2 云端兜底，不得参与去重、配对、均分、小费剔除、分组、重命名、金额计算或 Excel 公式生成；调用必须经过 Cloudflare Worker 网关并记录不含原文和凭证的最小审计元数据（请求 ID、模型、耗时、Token 用量、结果状态）。未经当前 Task 明确授权，不得提前创建 Worker、配置生产密钥或产生真实 API 费用。
8. **Ralph Orchestrator 规范**：所有 `ralph/hats/*.yml`、`ralph/runs/*.yml`、`ralph/<backend>.mjs` 包装器、4-Hat 拓扑（`plan` → `builder` → `critic` → `finalizer`）、`--stage` 显式传参、`build.done` 7 项门禁汇报与结构化脱敏日志，严格遵守全局 `ralph-orca` skill 规范。Metrics 仅作为异步、非侵入式观测平面，不得成为任务启动、验收、收尾或恢复的依赖。

---

## 4. Ralph Orchestrator 项目执行规范

### 4.1 编排目录、运行粒度与当前模型

- `ralph/` 是本项目唯一编排中心：Hat 配置放在 `ralph/hats/`，运行配置放在 `ralph/runs/`，Prompt 放在 `ralph/prompts/`，后端包装器使用 `ralph/<backend>.mjs`。项目根目录禁止出现 `ralph.yml` 或 `ralph.*.yml`，避免误启动历史配置。
- 一次 `ralph run` 默认必须连续完成一个完整 Milestone 下的全部 Task；不得把常规 Milestone 拆成多条按 Task 启动的命令。只有独立返工 Task 或专项补丁可以单独启动。
- 新建或更新的运行配置默认使用 `max_iterations: 300`、`max_runtime_seconds: 86400`；已验收完成的运行配置应移入 `ralph/runs/archive/`。
- 本项目当前 4-Hat 模型配置以 `ralph/hats/code-assist-control.yml` 为准：
  - `plan`：`./ralph/claude.mjs`，`sonnet`，`high`，显式 `--stage plan`。
  - `builder`：`./ralph/opencode.mjs`，`openai/gpt-6-luna`，`high`，显式 `--stage builder`。
  - `critic`：`./ralph/opencode.mjs`，`openai/gpt-6.2-sol`，`medium`，显式 `--stage critic`。
  - `finalizer`：`./ralph/opencode.mjs`，`openai/gpt-6.2-sol`，`medium`，显式 `--stage finalizer`。
- 各 Hat 的 `--stage` 必须由 YAML 显式传入；包装器必须解析并剥离该参数，再用于阶段归因，禁止通过 Prompt 文本猜测当前阶段。

### 4.2 4-Hat 职责与标准事件流

- `planner` 只能负责规划：读取当前 Task 文档、代码现状和权威进度账本，结合 Builder 能力，把当前一个 Task 拆成职责清楚、可独立验证的 Subtask，并写出允许/禁止改动路径、接口契约、不做范围和验收标准。Planner 禁止编写业务实现代码。
- Task 规划建议不超过 2,000 行，Subtask 建议不超过 800 行；`.ralph/` 与 `logs/` 过程日志不计入业务代码量。拆分必须基于职责和可独立验收性，禁止仅为凑行数制造接口、重复测试或多轮交接。
- `builder` 只能按当前 Subtask Spec 实现，禁止跨 Task 扩充、改变 HLD 技术栈或提前实现后续范围。
- `critic` 只能按当前 Spec 验收，禁止临时加码。同一 Subtask 连续 6 次真实复核失败后，必须发布 `task.replan` 返回 Planner，禁止继续盲目重试。
- `finalizer` 使用确定性命令核验交付物、改动范围和行数，更新 `.ralph/agent/*-progress.json`。每个 Subtask 完成后发布 `task.done` 返回 Planner；只有整个 Milestone 全部 Task 均已验收，才可发布 `LOOP_COMPLETE`。
- 标准事件固定为：
  - `plan`：`work.start`、`task.done`、`task.replan` → `spec.ready`、恢复专用 `review.accepted`、`LOOP_COMPLETE`、`loop.cancel`。
  - `builder`：`spec.ready`、`review.rejected` → `build.done`、`task.replan`、`loop.cancel`。Builder 自身发现 Spec 边界、前置条件、白名单、代码量或复杂度门禁无法同时满足时，必须携带实测证据发布 `task.replan` 交回 Planner，禁止直接 `loop.cancel`。
  - `critic`：`build.done` → `review.accepted`、`review.rejected`、`task.replan`、`loop.cancel`。
  - `finalizer`：`review.accepted` → `task.done`、`LOOP_COMPLETE`、`loop.cancel`。
- 入口事件只能使用 `work.start`，不得使用 Ralph 保留词 `task.start` 或 `task.resume`；禁止发明未定义的业务事件。恢复场景中的 `review.accepted` 仅能用于“Critic 已验收、只缺 Finalizer 收尾”，不得绕过 Critic。

### 4.3 `build.done` 七项门禁

- Builder 必须实际执行或通过确定性命令核查 `tests`、`lint`、`typecheck`、`audit`、`coverage`、`complexity`、`duplication`；全部通过后才能发布 `build.done`。
- `build.done` payload 开头必须严格使用：`tests: pass, lint: pass, typecheck: pass, audit: pass, coverage: pass, complexity: <纯数字>, duplication: pass.`。
- `complexity` 必须填写实测最大圈复杂度纯数字且不超过 10；没有改动函数时填 `0`。禁止填写 `pass`、`<= 10` 或虚构数据。
- 所有测试数量、覆盖率、复杂度、代码行数、路径、状态与验收结论必须来自真实执行结果。不得为了通过事件解析而猜测、预填或伪造。
- 用户交互和 UI 关键路径优先使用 Headless 自动化；只有 Headless 无法覆盖的主观视觉或明确人工门禁才转人工处理。

### 4.4 异常恢复与状态权威边界

- 单个 Subtask 受阻不等于整个循环受阻。Planner 应依次判断：能否修正 Spec 后重新派发；能否重拆；能否暂时标记该项阻塞并推进同一 Milestone 内其他已授权且独立的工作。只有同一阻塞项在 Planner 侧累计重规划 9 次仍未通过，且整轮没有任何可执行事项时，才允许 `loop.cancel`。
- 无副作用失败可在当前预算内修正后重试；已经发生外部写入或结果未知时，必须先对账，禁止盲目重试。
- 涉及稀缺资源、真实外部操作或不可逆动作时，必须“先审后发”：Builder 先提交冻结候选、完整命令、参数、失败诊断与清理方式，Critic 复核，Finalizer 写入检查点后，再由 Planner 派发真实操作。
- 发起真实或有副作用操作前，必须在 `.ralph/agent/*-progress.json` 写入版本、attempt ID 和检查点；执行后记录是否启动、结果及清理状态。无交付不计入 Critic 连续拒绝次数，真实复核失败才累计，结果未知则进入对账流程。
- 每轮默认只注入当前 Spec、最新授权、验收门槛、最近检查点和必要证据。历史 scratchpad 或 memory 不得覆盖当前进度账本、最新授权及真实执行回执。
- 任何 Hat 不得自证只有在自身进程退出后才发生的副作用；这类验证必须由下一阶段或外部调度器完成。

### 4.5 Metrics、日志与凭证

- Metrics 是完全解耦、异步、非侵入式的观测平面。Ralph 不得查询、等待或验证 Metrics，不得因 Metrics 缺失、未安装、写入失败或数据库故障而阻塞任务、门禁、收尾或恢复。
- 代码量、测试、覆盖率、复杂度等业务门禁必须由 Ralph 使用 `git diff`、测试与静态检查命令独立完成，不依赖 Metrics 数据库。
- 包装器与编排层只负责输出结构化、脱敏、可关联日志，尽量包含 `stage`、`model`、`task`、`subtask`、`duration_ms`、`tokens`、`exit_code`、`status`、`event`。
- 日志严禁包含 API Key、Token、密码、Secret、原始票据、完整 OCR 文本或其他敏感业务原文。
- OpenAI Platform 凭证由 OpenCode Provider 或环境变量管理，不得写入 Ralph YAML、Prompt、日志或项目文件。需要覆写 OpenCode Provider 协议时使用 `OPENCODE_CONFIG_CONTENT`，不得误用只影响 CLI 偏好的 `OPENCODE_CLI_CONFIG_CONTENT`。

### 4.6 启动、预检与首轮核查

- 首次运行使用单行命令：`ralph run -c ralph/runs/<slug>.yml -H ralph/hats/<hat>.yml --exclusive --idle-timeout 0`。
- 中断续跑使用单行命令：`ralph run -c ralph/runs/<slug>.yml -H ralph/hats/<hat>.yml --continue --exclusive --idle-timeout 0`。
- 每次向用户给出启动或续跑命令时，必须同时展示：
  1. 4 个 Hat 的实际包装器、模型、推理强度和 `--stage`。
  2. 目标 Milestone 每个 Task 的核心范围、功能代码与测试代码预估、估算依据，以及是否在 2,000 行建议内。估算必须结合模块、接口、状态流转、测试体量和既有同类代码，禁止先写业务代码再倒推或无依据拍数。
- 新增或修改包装器、切换模型、改变协议或推理参数后，启动 Ralph 前必须直接调用对应包装器完成一次最小工具调用冒烟测试，确认鉴权、模型、参数和协议正常。
- Ralph 配置不会对运行中的进程热生效；修改 Hat 或 Run YAML 后必须重启或使用 `--continue` 重新加载。运行状态以日志及 `.ralph/agent/` 进度落盘为准，不以 TUI 是否仍显示为准。
- 新管线完成第一个 Subtask 后必须核查：四个 stage 是否分别正确归因；结构化日志是否包含必要关联字段且无敏感信息；权威进度账本是否正确更新；七项门禁能否在完全不依赖 Metrics 的情况下独立完成。
