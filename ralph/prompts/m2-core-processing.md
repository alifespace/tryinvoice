# TryInvoice M2 连续执行提示词

本次是全新的 M2 控制平面循环，在一次 `ralph run` 中依次完成：

1. `M2.1-document-extractor`
2. `M2.2-deterministic-rule-engine-part1`
3. `M2.3-deterministic-rule-engine-part2`

业务需求唯一依据是 `AGENTS.md`、`docs/hld.md`、`docs/lld.md`、`docs/milestones.md` 与上述三份 Task 文档。Task 文档不可变；不得把新需求塞入 M2，也不得提前实现 M3。

首次 Planner 必须创建 `.ralph/agent/m2-progress.json`，固定记录 `milestone=M2`、上述 `task_order`、当前 Task、各 Task/Subtask 状态、代码量基线和真实验收证据。`.ralph/agent/task-progress.json` 与 `.ralph/agent/tasks.jsonl` 是 M1 历史，只读参考；其中遗留的 metrics 顺序或 skill 发现任务不得成为 M2 工作项。

M2.1 的云端兜底应以可替换适配器和 Mock 响应验收，默认不发起付费 OpenAI 调用。PDF 测试必须使用真正的 PDF 文件格式，但只允许仓库安全的合成、公开可再分发或彻底脱敏样本，禁止提交个人票据。M2.2/M2.3 的所有业务计算必须是确定性程序，不得用 LLM 替代。

同一时刻只允许一个 Task、一个 Subtask 为 active。每个 Task 总改动量不超过 1500 行；Subtask 建议不超过 800 行；测试满足 `Test:Feat <= 1.3` 或新增测试少于 450 行之一。所有门禁、代码量和验收结果使用确定性命令实测并写入 M2 进度文件，不得查询或依赖 Metrics。仅当 M2.1、M2.2、M2.3 分别达到原 Task 验收标准后才发布 `LOOP_COMPLETE`。
