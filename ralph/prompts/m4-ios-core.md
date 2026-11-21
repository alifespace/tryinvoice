# TryInvoice M4 连续执行入口

M1-M3 已完成；M3.3 真实 GitHub Actions 回执见 `docs/acceptance/M3.3-github-actions-validation.md`。
启动 M4 控制平面，使用同一条 Ralph 管线按依赖推进：

1. `M4.1-test-backend-environment`
2. `M4.2-ios-project-scaffold-and-ci`
3. `M4.3-ios-auth-network-and-account`
4. `M4.4-ios-offline-store-and-upload-queue`
5. `M4.5-ios-capture-import-and-ocr`
6. `M4.6-ios-review-and-templates`
7. `M4.7-ios-export-and-share`

Planner 首轮须确认 M3 的 `LOOP_COMPLETE`、提交基线和当前工作区文档变更，创建建议不超过 5KB 的
`.ralph/agent/m4-progress.json`，只保存 Task/Subtask 状态、唯一 active 指针、最近检查点、失败/重规划
计数、人工门禁状态与冷归档引用；创建 `.ralph/agent/archive/m4/` 保存详细证据。首次激活每个 Task
时才按需读取对应 Task 文档、`docs/environments/test-environment.md` 与相关设计章节，编译建议不超过
5KB 的 `.ralph/agent/m4-current-spec.md`。下游 Hat 只读取当前 Spec、热指针、白名单源码与证据引用。

M4.1 与 M4.2 的无副作用实现可先后独立推进：M4.1 先形成 Render Free 声明式配置、迁移/种子命令、
合成冒烟脚本、Secret 名称清单与回滚说明；M4.2 建立 XcodeGen SwiftUI 工程及本地可确定性验证的
静态/纯逻辑测试。以下事项均未因启动本管线而获得授权：重置 Supabase 密码，创建 Auth 用户或 Storage
bucket，执行远端迁移，创建 Render 服务，推送 GitHub，触发 Actions，产生真实 OpenAI 费用，使用真机
权限或对外分享。任何 Hat 不得自行实施这些操作。

当一个 Subtask 的本地候选已通过 Critic、但 Task 验收还依赖真实外部回执时，Finalizer 将其标记为
`prepared`/`waiting_manual` 并冷归档，Planner 优先推进其他不依赖该回执的工作。所有可执行工作完成、
只剩人工门禁时，Planner 在热账本写明冻结候选、完整待执行步骤、attempt 前置与清理方式后发布
`loop.cancel` 安全停机。取得真实回执后，用同一 run 配置加 `--continue` 恢复；Planner 对账后只在
确已通过 Critic 的恢复场景发布 `review.accepted` 唤醒 Finalizer，禁止跳过未完成验收。

Swift 工程的真实编译、模拟器测试与 `.xcresult` 必须来自已授权 GitHub Actions；未运行不得填 pass。
端侧 OCR 只使用 Vision；金额、去重、配对、均分、小费、分组、命名、Excel 与 ZIP 仍由确定性程序
负责，禁止用 LLM 代替。测试样本必须合成或彻底脱敏，凭证与业务原文不得进入事件或归档。

每个可验收 Subtask 均须经过 Builder 七项门禁、Critic 独立复验和 Finalizer 冷归档。只有
M4.1-M4.7 全部 accepted，Planner 才可发布 `LOOP_COMPLETE`。
