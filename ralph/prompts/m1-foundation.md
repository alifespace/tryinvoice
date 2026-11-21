# TryInvoice M1 Ralph 连续流水执行入口

本次仅启动一个 Ralph loop，按固定顺序连续完成以下三个独立 Task：

1. `M1.1-backend-scaffold-and-db`
2. `M1.2-rule-template-dsl-and-cmi-seed`
3. `M1.3-auth-storage-and-openapi-contract`

三个 Task 的范围、验收标准与不可变边界仍分别来自对应 Task 文档，严禁把它们合并成一个 Task、跨 Task 混写 Spec，或在执行过程中追加需求。

## 权威文档

开始前必须完整读取：

1. `AGENTS.md`
2. `docs/hld.md`
3. `docs/lld.md`
4. `docs/milestones.md`
5. `docs/tasks/M1.1-backend-scaffold-and-db.md`
6. `docs/tasks/M1.2-rule-template-dsl-and-cmi-seed.md`
7. `docs/tasks/M1.3-auth-storage-and-openapi-contract.md`

若文档冲突，必须在 `.ralph/agent/task-progress.json` 中记录具体冲突并发布 `loop.cancel`，不得自行改写需求或变更技术栈。

## Milestone 状态机

Planner 首次响应 `work.start` 时，若进度文件不存在，创建 `.ralph/agent/task-progress.json`，至少记录：

- `milestone`: `M1`
- `task_order`: 上述三个 Task 的固定顺序
- `current_task`: 当前唯一激活的 Task
- 每个 Task 的 `pending`、`active`、`accepted` 或 `blocked` 状态
- 当前和已完成的 Subtask 编号、验收证据及遗留限制

状态流转必须满足：

1. 从 `M1.1` 开始，同一时刻只能有一个 `active` Task。
2. Planner 每次只从当前 Task 文档拆解一个 `Mx.y-Sz` Subtask，并写入 `.ralph/agent/current-spec.md`。
3. Finalizer 完成当前 Subtask 的指标和代码量快照后发布 `task.done`，由 Planner 判断当前 Task 是继续拆分还是进入最终验收。
4. 当前 Task 的全部范围及验收标准均有真实证据后，Planner 才能将其标为 `accepted` 并激活下一个 Task。
5. 即使 M1.3 在设计依赖上只要求 M1.1，本流水线仍按 `M1.1 → M1.2 → M1.3` 严格串行，避免同一工作区交叉修改。
6. 只有 M1.1、M1.2、M1.3 全部为 `accepted` 时，Planner 才能发布 `LOOP_COMPLETE`。
7. 任一 Task 无法满足前置条件、四次重试后仍失败或需要超出既定范围时，标记为 `blocked` 并发布 `loop.cancel`；不得跳过后继续执行其他 Task。

## 实施边界

- 只实施 M1，不提前实现 M2–M5。
- 严禁修改 `AGENTS.md`、`docs/hld.md`、`docs/lld.md`、`docs/milestones.md` 和 `docs/tasks/*.md`。
- 不使用旧版 React/Vite 浏览器原型、旧 `.ralph` 历史或已经删除的样本作为实现依据。
- 不连接或写入真实生产 Supabase；数据库迁移、RLS 与存储验证使用安全、可重复的本地或测试环境。
- 凭证只能从环境变量或未跟踪的本地配置注入，禁止写入仓库、日志、事件和报告。
- 每个 Task 独立遵守总改动量不超过 1,500 行；Subtask 建议不超过 800 行。
- 每个 Task 的测试分别满足 `Test:Feat <= 1.3` 或新增测试少于 450 行之一。

按 `plan → builder → critic → finalizer` 循环工作。Task 之间只通过已验收代码与 `.ralph/agent/task-progress.json` 交接，不新增业务事件。
