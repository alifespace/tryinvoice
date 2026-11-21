# 智能报销助手 — 里程碑与任务拆解规划

> 本规划遵循全局规范：项目按 **里程碑（`M1`~`M5`）→ 任务（`Mx.y`）→ 子任务（`Mx.y-Sz`，执行阶段由 Ralph Planner 拆解）** 三级组织；每个 Task 预估代码改动量控制在 **≤ 1,500 行** 以内。

---

## 里程碑总览

| 里程碑 | 目标与可验收交付物 | 包含任务 |
| :--- | :--- | :--- |
| **M1：基础底座、契约与数据模型** | 完成仓库初始化、Supabase 数据库 Schema（含 `user_id` + RLS）、FastAPI 骨架与 OpenAPI 3.1 契约，预置 CMI 默认规则模板 | `M1.1`（保留已验收骨架）、`M1.4`、`M1.2`、`M1.3` |
| **M2：确定性规则引擎与单据识别管线** | 实现规则模板 DSL 校验、PDF/VLM 要素提取器、确定性规则引擎（去重/配对/均分/小费/机场缺漏检查） | `M2.1`、`M2.2`、`M2.3` |
| **M3：标准化导出引擎、完整后端闭环与 iOS 云端 CI 底座** | 实现 `openpyxl` 三级分组 Excel 生成器、规范重命名与 ZIP 打包导出、异步任务队列与集成测试，并建立无需本机完整 Xcode 的 GitHub Actions macOS 构建环境 | `M3.1`、`M3.2`、`M3.3` |
| **M4：iOS 客户端核心应用（采集→复核→导出）** | 完成 SwiftUI iOS App：离线拍照/导入、后台直传、批次与交易复核、异常高亮处理、模板查看/派生、ZIP 分享导出 | `M4.1`、`M4.2`、`M4.3`、`M4.4` |
| **M5：Share Extension、稳定性打磨与 App Store 上架** | 完成系统分享扩展（微信/PDF 一键导入）、真实票据端到端验收、隐私清单与账号删除合规、TestFlight 上架 | `M5.1`、`M5.2` |

*(注：未来 Web 端可在 M5 完成后作为 `M6` 里程碑直接基于 `M1` 定义的 OpenAPI 契约启动，无需改动后端核心逻辑。)*

---

## M1：基础底座、契约与数据模型

> **执行调整记录（2026-11-21）**：原 `M1.1` 在 Ralph 执行中发现“后端骨架 + 数据库迁移/RLS”累计改动量紧贴 1,500 行上限，不利于独立验收和后续维护。依据 Task 不可变原则，原 `docs/tasks/M1.1-backend-scaffold-and-db.md` 保持不变并保留审计记录；已经由 Critic/Finalizer 验收的 `M1.1-S1` 后端骨架作为稳定基线，尚未验收的数据库迁移/RLS 范围由新 Task `M1.4-db-migration-and-rls` 承接。调整后的 Ralph 顺序为 `M1.4 → M1.2 → M1.3`；只有三者均独立验收，且 `M1.1-S1` 的既有证据完整保留，M1 才算完成。

### `M1.1-backend-scaffold-and-db`：后端工程初始化与多租户数据库迁移
- **目标**：搭建基于 `uv` + Python 3.14+ + FastAPI 的后端项目骨架，并在 Supabase PostgreSQL 中建立支持未来多用户的数据库 Schema 与 RLS 策略。
- **前置依赖**：无。
- **范围与约束**：
  - 初始化 Monorepo 目录结构（`backend/`、`ios/`、`contracts/`、`docs/`）及项目根目录 `AGENTS.md`。
  - 编写数据库迁移脚本，创建 `user_profiles`、`rule_templates`、`reimbursement_batches`、`raw_documents`、`transactions`、`transaction_documents`、`jobs` 7 张表。
  - 所有业务表强制包含 `user_id` 列与 RLS 策略；严禁硬编码密钥，全部通过 `.env` 注入。
- **验收标准**：
  - `uv run pytest` 可运行基础健康检查测试。
  - 迁移脚本可在本地/Supabase PostgreSQL 幂等执行，RLS 隔离单测通过（不同 `user_id` 无法互访数据）。

### `M1.4-db-migration-and-rls`：数据库迁移与多租户 RLS 独立验收
- **目标**：以已验收的 `M1.1-S1` 后端骨架为基线，独立完成并验收 PostgreSQL/Supabase 数据库迁移、幂等运行器与多租户 RLS 隔离。
- **前置依赖**：`M1.1-S1` 后端骨架已由 Critic/Finalizer 验收并完成代码量基线记录。
- **范围与约束**：
  - 承接原 `M1.1` 中尚未验收的数据库范围，不修改或重复实现已验收的健康检查、配置与质量工具链。
  - 创建 `user_profiles`、`rule_templates`、`reimbursement_batches`、`raw_documents`、`transactions`、`transaction_documents`、`jobs` 7 张表及约定索引。
  - 为业务表启用 RLS，按 `auth.uid()` 与 `user_id` 实现租户隔离；系统默认规则模板只允许普通用户读取，不允许修改。
  - 提供按文件独立事务执行、失败不回滚既往成功迁移、可重复运行的迁移运行器；测试仅使用安全的临时 PostgreSQL 或测试环境，不写入生产 Supabase。
- **验收标准**：
  - 迁移首次执行、重复执行、单文件直接重放与“后一迁移失败不回滚前一迁移”均有真实 PostgreSQL 测试证明。
  - 7 张表、列、索引和 RLS 策略与 `docs/lld.md` 一致；不同 `user_id` 无法读取或修改对方数据。
  - 后端测试、lint、类型检查、安全审计、覆盖率、复杂度与重复代码 7 项门禁全部通过；本 Task 代码量以 `M1.1-S1` 已记录基线为起点独立核算并满足项目红线。

### `M1.2-rule-template-dsl-and-cmi-seed`：规则模板 DSL 与 CMI 默认模板内置
- **目标**：实现规则模板 JSON Schema（Pydantic v2 模型）及校验器，并将 `CMI 报销规则` 作为系统内置默认模板写入数据库与版本控制。
- **前置依赖**：`M1.1-backend-scaffold-and-db`。
- **范围与约束**：
  - 定义 `RuleTemplateDSL`（包含 `expense_types`、`categories`、`region_hierarchy`、`compliance_rules`、`naming_and_export`）。
  - 编写 Seed 脚本写入 `cmi_default_v1`（`is_system_default = true`, `user_id = NULL`）。
  - 实现模板 CRUD 接口（`GET /templates`、`POST /templates`、`POST /templates/{id}/clone`、`PATCH /templates/{id}`），禁止修改系统默认模板。
- **验收标准**：
  - CMI 默认模板完整涵盖 `EAT`/`DRINK`/`TRANS`/`Others`/`UNKNOWN`、美洲范围内与大陆/HK/新加坡/其他范围外、小费剔除、多人均分、双向闭环与命名格式。
  - 模板克隆与校验单测全部通过。

### `M1.3-auth-storage-and-openapi-contract`：JWT 鉴权、对象存储预签名与 OpenAPI 契约导出
- **目标**：打通 Supabase Auth JWT 鉴权中间件、对象存储（R2/Supabase Storage）预签名上传/下载接口，并配置 OpenAPI 3.1 契约自动导出。
- **前置依赖**：`M1.1-backend-scaffold-and-db`。
- **范围与约束**：
  - 实现 `get_current_user` 依赖注入（解析 JWT `sub` 作为 `user_id`），支持 V1 本地开发/单用户配置。
  - 实现 `POST /batches`、`GET /batches`、`DELETE /batches/{id}`、`POST /batches/{id}/documents/presign`（含 `sha256_hash` 幂等秒传检测）与 `confirm`。
  - 增加 `DELETE /account` 接口框架（级联清理 DB 与存储）。
  - 编写脚本将 `openapi.json` 导出至 `contracts/openapi.json` 供 iOS 与未来 Web 端使用。
- **验收标准**：
  - 无效 JWT 请求返回 `401`；有效 JWT 可完成批次创建与预签名 URL 签发。
  - `contracts/openapi.json` 成功生成且通过 OpenAPI 3.1 校验。

---

## M2：确定性规则引擎与单据识别管线

### `M2.1-document-extractor`：分层单据要素提取器（端侧 OCR 文本 / PDF / OpenAI-Codex API 兜底）
- **目标**：构建分层且可插拔的单据要素提取层：优先消费 iPhone `Vision` 端侧 OCR 文本与 `PyMuPDF` PDF 文本，复杂/低置信度场景通过 OpenAI / Codex API 兜底提取客观交易字段。
- **前置依赖**：`M1.3-auth-storage-and-openapi-contract`。
- **范围与约束**：
  - 定义 `DocumentExtractorProtocol` 接口及 `ExtractedDocument` Pydantic 输出结构（商户、日期时间、币种、原单金额、小费、人数/份数、卡号后四位、单号、是否含 Paid、机场代码、文档角色等）。
  - **Tier 1（零成本优先）**：PDF 使用 `PyMuPDF` 确定性提取；图片优先基于 `client_ocr_json` 执行确定性正则/版面解析。
  - **Tier 2（云端兜底）**：当 `client_ocr_json` 缺失、置信度低或结构化规则无法解析时，调用 OpenAI / Codex 兼容 API（文本或视觉接口）补全结构化字段。
  - 严禁在日志中打印完整银行卡号、证件号或税号。
- **验收标准**：
  - 使用 Mock VLM 响应与真实 PDF 样本（如 Uber/Grab PDF）单测通过，能准确区分 `invoice`、`payment_receipt`、`trip_application`、`irrelevant`。

### `M2.2-deterministic-rule-engine-part1`：确定性规则引擎（去重、配对与八达通/交通判定）
- **目标**：实现核心确定性处理逻辑的前半段：无关文件过滤、同凭证重拍去重、Invoice 与付款 Receipt 双向闭环配对及特殊交通判定。
- **前置依赖**：`M1.2-rule-template-dsl-and-cmi-seed`、`M2.1-document-extractor`。
- **范围与约束**：
  - 实现重拍去重算法（基于商户、日期时间、金额、流水号相似度 + `quality_score` 择优）。
  - 实现 Invoice + Receipt 配对算法（金额只计入一次，关联两张凭证，标为 `paired`；缺付款标 `missing_payment`；缺 Invoice 且非免除交通标 `missing_invoice`）。
  - 实现香港八达通与信用卡直刷公共交通的规则判定。
- **验收标准**：
  - 针对重拍图片、Invoice+Receipt 双图、八达通餐饮 vs 地铁、缺发票等边界用例的纯逻辑单测 100% 通过。

### `M2.3-deterministic-rule-engine-part2`：确定性规则引擎（均分、小费、地域分组与机场缺漏检查）
- **目标**：实现规则引擎后半段及批处理入口：多人餐费均分、网约车小费剔除、三级地域分组、±1 天时区容差及往返机场 `TRANS` 缺漏检测。
- **前置依赖**：`M2.2-deterministic-rule-engine-part1`。
- **范围与约束**：
  - 按 `RuleTemplateDSL` 执行小费剔除（记录原额、小费、净车费与 `TIP_EXCLUDED` 标记）与多人餐费均分（`claim_amount` 取均分后金额）。
  - 实现 `category` → `region_scope` → `region_detail`（按币种）映射。
  - 结合 `trip_application` 提取的起止时间（±1 天容差）与目的地机场，检查 `TRANS_To<IATA>` / `TRANS_From<IATA>` 成对完整性，生成 `missing_alerts`。
  - 暴露 `POST /batches/{id}/process` 与 `GET /batches/{id}/transactions`、`PATCH /transactions/{tx_id}`，保护 `is_user_verified=true` 记录不被覆盖。
- **验收标准**：
  - 增量补充上传后再次 `process` 不重复处理旧文件、不覆盖人工修正；所有合规计算单测通过。

---

## M3：标准化导出引擎、完整后端闭环与 iOS 云端 CI 底座

### `M3.1-excel-and-zip-exporter`：三级分组 Excel 生成器与规范命名 ZIP 打包
- **目标**：实现确定性导出引擎，生成符合 CMI 规范的 `.xlsx` 汇总表、标准化命名凭证副本及 `.processed_manifest.json`，打包为 ZIP 上传至对象存储。
- **前置依赖**：`M2.3-deterministic-rule-engine-part2`。
- **范围与约束**：
  - 使用 `openpyxl` 生成 Excel：包含全部 12 个标准列、三级分组结构、按币种 `SUM` 小计/合计公式、异常行高亮底色与批注、疑似漏交清单区块。
  - 按模板规则重命名凭证副本（含 `_invoice`/`_receipt` 后缀与 `TRANS_To/From<IATA>` 格式，金额使用 `<元-分>`）。
  - 打包为 ZIP 上传至对象存储 `exports/`，实现 `POST /batches/{id}/export` 与 `GET /jobs/{job_id}`。
- **验收标准**：
  - 导出的 `.xlsx` 在 Excel/Numbers 中公式计算无 `#REF!`/`#VALUE!` 错误，分组与文件命名 100% 符合 `报销/AGENTS.md` 规范。

### `M3.2-backend-e2e-and-deployment-config`：后端异步任务稳定性与云端部署配置
- **目标**：完善后台异步任务执行（防并发重复触发、失败重试与状态更新）及后端容器化/部署配置。
- **前置依赖**：`M3.1-excel-and-zip-exporter`。
- **范围与约束**：
  - 添加 `Dockerfile`、环境变量模板（`.env.example`）与健康检查端点。
  - 编写完整后端 API 集成测试：创建批次 → 上传多张模拟单据（含重拍、双凭证、小费、缺机场）→ 处理 → 人工微调 → 导出 ZIP → 校验 ZIP 内容。
- **验收标准**：
  - 后端全套单测与集成测试通过，无敏感信息泄露至日志。

### `M3.3-ios-cloud-ci-foundation`：iOS 云端构建与模拟器测试基础设施
- **目标**：在本机仅安装 Xcode Command Line Tools、无法安装完整 Xcode 的约束下，建立私有 GitHub 仓库与 GitHub Actions macOS/Xcode CI 底座，为 M4–M5 的 SwiftUI 工程提供确定性工程生成、云端编译、模拟器测试和验收制品。
- **前置依赖**：`M3.2-backend-e2e-and-deployment-config`。
- **范围与约束**：
  - 配置私有 GitHub 远端与 `.github/workflows/ios-ci.yml`；若尚未获得创建远端仓库的用户授权，只提交可审查配置并将远端创建记录为人工门禁，严禁擅自创建公开仓库。
  - 采用成熟的 XcodeGen 管理可复现 Xcode 工程；CI 使用明确固定的 GitHub macOS runner、Xcode 版本和镜像实际提供的 iOS Simulator，禁止依赖漂移的 `latest` 隐式版本。
  - 在 M4.1 工程尚未出现时运行 Xcode/Simulator/XcodeGen 工具链探针；检测到 `ios/project.yml` 与共享 Scheme 后，自动执行工程生成、`xcodebuild build-for-testing` 和 `xcodebuild test-without-building`。
  - 上传 `.xcresult`、构建日志和失败截图作为 CI Artifact；不得上传 DerivedData、签名文件、API Key、测试账号密码、原始票据或其他敏感数据。
  - 本 Task 只建立无签名的 Simulator CI，不配置 App Store 证书、Provisioning Profile、TestFlight 或生产发布；真实相机、相册、Share Extension 和系统分享验收保留至 M4/M5 的真机测试。
- **验收标准**：
  - GitHub Actions macOS job 实际通过工具链探针并输出 Xcode、Swift、SDK 与可用 Simulator 版本；Workflow 语法检查通过。
  - 当 iOS 工程存在时，CI 能从 `ios/project.yml` 全新生成工程并在固定 Simulator 上完成构建与测试，不依赖本机完整 Xcode。
  - 失败运行可下载 `.xcresult`、日志与截图定位问题，仓库及 Artifact 中不含任何凭证或用户敏感资料。

---

## M4：iOS 客户端核心应用（采集→复核→导出）

### `M4.1-ios-scaffold-auth-and-offline-store`：iOS 工程初始化、极简登录与离线存储底座
- **目标**：创建 Swift/SwiftUI iOS 工程，完成基于 OpenAPI 契约的网络层、V1 极简登录页（Supabase 单账号）以及本地 `SwiftData` + 沙盒图片仓。
- **前置依赖**：`M1.3-auth-storage-and-openapi-contract`。
- **范围与约束**：
  - 初始化 Xcode 工程（iOS 17+），集成 `CoreNetwork`（Bearer JWT + 预签名直传/下载）与 `LocalStore`（`SwiftData`）。
  - 实现极简登录页面（邮箱/密码登录，不提供公开注册/团队切换 UI）与后台上传队列（按 `sha256` 幂等上传）。
  - 实现账号信息页与“删除账号及所有数据”入口（调用 `DELETE /account`）。
- **验收标准**：
  - App 可在 iOS 模拟器/真机启动，完成登录后离线创建批次并重启后数据不丢失，联网后自动状态同步。

### `M4.2-ios-capture-and-import`：拍照文档扫描、端侧 Vision OCR 与批次管理 UI
- **目标**：实现核心采集与端侧免费 OCR 能力，用户可直接在 App 内拍照（文档扫描切边）、从相册多选或导入 PDF，彻底摆脱向电脑导照片。
- **前置依赖**：`M4.1-ios-scaffold-auth-and-offline-store`。
- **范围与约束**：
  - 集成 `VNDocumentCameraViewController` 支持连续拍照、自动边缘裁剪与透视矫正。
  - 调用 iPhone 原生 `Vision` 框架（`VNRecognizeTextRequest`，`.accurate` 模式）在端侧离线提取文本行与坐标框，随 `confirm` 接口一并上传。
  - 集成 `PhotosPicker` 与 `UIDocumentPickerViewController`（支持图片与 PDF）。
  - 原始文件落盘本地只读沙盒并展示上传状态（待上传/上传中/已入库/秒传）。
- **验收标准**：
  - 实测连拍 5 张小票 + 导入 1 个 PDF，均成功保存本地并自动直传至云端对象存储。

### `M4.3-ios-review-and-templates`：交易复核明细、异常高亮与规则模板管理 UI
- **目标**：实现批次处理结果展示、异常告警高亮提示、人工微调交互，以及规则模板的查看、复制与自定义编辑。
- **前置依赖**：`M2.3-deterministic-rule-engine-part2`、`M4.2-ios-capture-and-import`。
- **范围与约束**：
  - 批次详情页按三级分组展示交易，顶部展示 `missing_alerts`（如缺失机场交通）与待确认计数。
  - 交易详情支持并排/切换查看关联的 `Invoice` 与 `Receipt` 原图，支持修改分类、均分人数、剔除小费金额与备注。
  - 模板管理页展示 CMI 默认模板（只读标示），支持一键“复制并自定义”修改模板名称与合规开关。
- **验收标准**：
  - 人工修改交易分类或均分人数后，列表金额与分组小计实时更新并同步服务端。

### `M4.4-ios-export-and-share`：一键生成导出包与系统 Share Sheet 分享
- **目标**：完成导出闭环，用户在 iOS 端一键生成并下载标准化 ZIP 报销包（或单独预览 Excel），通过系统 Share Sheet 分享或保存。
- **前置依赖**：`M3.1-excel-and-zip-exporter`、`M4.3-ios-review-and-templates`。
- **范围与约束**：
  - 触发 `POST /batches/{id}/export`，轮询任务进度并下载 ZIP 至本地缓存。
  - 支持 `QuickLook` 预览 ZIP 内文件或 `UIActivityViewController` 一键转发至微信、邮件、Files 或 Google Drive。
- **验收标准**：
  - 端到端在真机上完成“拍照 → 自动识别 → 复核 → 导出 ZIP 并分享到 Files/微信”全流程。

---

## M5：Share Extension、稳定性打磨与 App Store 上架

### `M5.1-ios-share-extension`：系统 Share Extension（从外部 App 一键导入账单）
- **目标**：新增 iOS Share Extension，支持用户在 Uber、Grab、微信、邮件或 Files 中将 PDF 账单或截图直接分享进本 App 指定批次。
- **前置依赖**：`M4.2-ios-capture-and-import`。
- **范围与约束**：
  - 配置 App Group 共享容器，Share Extension 接收图片/PDF 后写入共享队列，主 App 唤醒后自动入库并上传。
- **验收标准**：
  - 在 Files 或邮件 App 中点击 PDF 账单的“分享 → 报销助手”，可选择目标批次并完成入库。

### `M5.2-app-store-readiness-and-e2e`：App Store 审核合规、隐私清单与真实样本验收
- **目标**：完成 App Store 上架前的所有合规检查与真实历史报销数据回归验收。
- **前置依赖**：`M3.2-backend-e2e-and-deployment-config`、`M4.4-ios-export-and-share`、`M5.1-ios-share-extension`。
- **范围与约束**：
  - 补全 `PrivacyInfo.xcprivacy`、相机/相册权限文案（Info.plist）、账号删除流程自测。
  - 使用脱敏后的真实出差/餐费凭证集运行全链路回归测试，对比手工/旧脚本输出核验金额与文件命名一致性。
  - 整理 TestFlight 打包配置与 App Store 提审元数据清单。
- **验收标准**：
  - Archive 构建与 TestFlight 上传成功；真实样本对账金额零误差、App Store 审核合规项自查 100% 通过。
