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
   - 单个 Task 总改动量控制 **≤ 1,500 行**；Subtask 建议 **≤ 800 行**。
   - 只保留“核心纯逻辑单测 + 关键路径集成/UI 测试”，禁止三层重复测试；新增测试代码满足 **`Test:Feat <= 1.3`** 或 **新增测试 `< 450` 行**（满足其一即可）。
6. **安全与凭证红线**：严禁将 API Key、JWT、Supabase Secret、R2 密钥、用户银行卡完整卡号/证件号/税号写入日志或提交至 Git；后端凭证通过 `.env`（已加入 `.gitignore`）注入，Cloudflare Worker 凭证通过平台 Secret 注入，日志中敏感字段必须脱敏。
7. **OpenAI 调用边界**：OpenAI API 仅作为 Tier 2 云端兜底，不得参与去重、配对、均分、小费剔除、分组、重命名、金额计算或 Excel 公式生成；调用必须经过 Cloudflare Worker 网关并记录不含原文和凭证的最小审计元数据（请求 ID、模型、耗时、Token 用量、结果状态）。未经当前 Task 明确授权，不得提前创建 Worker、配置生产密钥或产生真实 API 费用。
8. **Ralph Orchestrator 规范**：所有 `ralph/hats/*.yml`、`ralph/runs/*.yml`、`ralph/<backend>.mjs` 包装器、4-Hat 拓扑（`plan` → `builder` → `critic` → `finalizer`）、`--stage` 显式传参、`build.done` 7 项门禁汇报与结构化脱敏日志，严格遵守全局 `ralph-orca` skill 规范。Metrics 仅作为异步、非侵入式观测平面，不得成为任务启动、验收、收尾或恢复的依赖。
