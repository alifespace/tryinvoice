# TryInvoice M3 连续执行入口

M2.1-M2.5 已全部 accepted。启动新的 M3 控制平面，在一次 Ralph loop 中依次完成：

1. `M3.1-excel-and-zip-exporter`
2. `M3.2-backend-e2e-and-deployment-config`
3. `M3.3-ios-cloud-ci-foundation`

Planner 首轮须确认 M2 的 `LOOP_COMPLETE` 与当前代码基线，创建建议不超过 5 KB 的
`.ralph/agent/m3-progress.json`，仅保存任务状态、唯一 active 指针、最近检查点、失败/重规划计数与
冷归档引用；创建 `.ralph/agent/archive/m3/` 保存详细验收证据。首次激活 M3.1 时读取其 Task 文档和
相关设计章节，编译建议不超过 5 KB 的 `.ralph/agent/m3-current-spec.md` 后发布 `spec.ready`。

切换 M3.2、M3.3 时再按需读取对应 Task 文档与相关设计章节；Builder、Critic、Finalizer 默认只读取
当前 Spec、热指针、最近检查点、白名单源码与必要证据，禁止每轮全量重读 AGENTS/HLD/LLD/Milestones。
Scratchpad 每轮覆写为建议不超过 4 KB 的滚动摘要，完整历史只在争议或对账时按冷归档引用读取。

M3.1 的 Excel、命名、清单与 ZIP 必须由确定性程序生成；测试使用合成或彻底脱敏样本。M3.2 的异步
稳定性、容器配置与端到端测试不得泄露凭证。M3.3 优先提交可审查的私有仓库 CI 配置；涉及真实 GitHub
写入或 macOS 分钟前，必须先记录 attempt ID、完整命令与清理方式并确认当前授权，禁止创建公开仓库。

每个 Subtask 均须经过 Builder 七项门禁、Critic 独立复验和 Finalizer 冷归档收尾。只有 M3.1-M3.3
全部 accepted，Planner 才可发布 `LOOP_COMPLETE`。
