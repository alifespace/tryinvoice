# TryInvoice M1 拆分后连续流水执行入口

本次启动一个新的 Ralph loop，承接旧 M1 流水的已验收成果，并按固定顺序连续完成以下三个独立 Task：

1. `M1.4-db-migration-and-rls`
2. `M1.2-rule-template-dsl-and-cmi-seed`
3. `M1.3-auth-storage-and-openapi-contract`

原 `M1.1-backend-scaffold-and-db` 文档保持不变作为审计记录。旧流水中只有 `M1.1-S1` 已通过 Critic/Finalizer，必须保留其证据与代码量基线；未通过 Critic 的 `M1.1-S2` 不得视为完成，其工作区实现只能作为 `M1.4` 的候选实现重新检查和验收。

## 权威文档

开始前必须完整读取：

1. `AGENTS.md`
2. `docs/hld.md`
3. `docs/lld.md`
4. `docs/milestones.md`
5. `docs/tasks/M1.1-backend-scaffold-and-db.md`
6. `docs/tasks/M1.4-db-migration-and-rls.md`
7. `docs/tasks/M1.2-rule-template-dsl-and-cmi-seed.md`
8. `docs/tasks/M1.3-auth-storage-and-openapi-contract.md`
9. `.ralph/agent/task-progress.json`（若存在）

若文档冲突，必须在 `.ralph/agent/task-progress.json` 中记录具体冲突并发布 `loop.cancel`，不得自行改写需求或变更技术栈。

## 旧进度迁移

Planner 首次响应 `work.start` 时必须先迁移旧进度，不得直接触发旧 `M1.1-S2` 的 Critic/Finalizer：

1. 保留 `M1.1-S1` 的 `accepted` 证据、命令、指标与代码量快照，不重复实现或重新计入 `M1.4`。
2. 将原 `M1.1` 记录为 `superseded`，并记录 `superseded_by: M1.4-db-migration-and-rls`；严禁把原 Task 或未验收的 `M1.1-S2` 标为 `accepted`。
3. 将 `task_order` 更新为 `M1.4 → M1.2 → M1.3`，把 `M1.4` 设为唯一 `active` Task，其他两个设为 `pending`。
4. 废止旧 `.ralph/agent/current-spec.md` 中的 `M1.1-S2` 身份，按 `M1.4` Task 文档重新创建 `M1.4-S1` Spec；可复用工作区候选实现，但必须发布 `spec.ready` 交给 Builder，禁止跳过 Builder 或 Critic。
5. `M1.4` 代码量使用已记录的 `M1.1-S1` 快照作为基线，分别报告功能、测试、生成锁文件与合计，不得把 `M1.1-S1` 再次计入。

## Milestone 状态机

1. 同一时刻只能有一个 `active` Task，严格执行 `M1.4 → M1.2 → M1.3`。
2. Planner 每次只为当前 Task 拆解一个 `Mx.y-Sz` Subtask，并写入 `.ralph/agent/current-spec.md`。
3. Finalizer 完成当前 Subtask 的指标和代码量快照后发布 `task.done`，由 Planner 判断继续当前 Task 或激活下一 Task。
4. 当前 Task 的全部范围及验收标准均有真实证据后，Planner 才能将其标为 `accepted`。
5. 只有 `M1.4`、`M1.2`、`M1.3` 全部为 `accepted`，且 `M1.1-S1` 的历史验收证据仍完整时，Planner 才能发布 `LOOP_COMPLETE`。
6. 任一 Task 无法满足前置条件、同一 Subtask 四次复审失败或需要超出既定范围时，按 `ralph-orca` 规则重规划或发布 `loop.cancel`，不得跳过后继续。

## 实施边界

- 只实施 M1，不提前实现 M2–M5。
- 严禁修改 `AGENTS.md`、`docs/hld.md`、`docs/lld.md`、`docs/milestones.md` 和 `docs/tasks/*.md`。
- 不使用旧版 React/Vite 浏览器原型、已删除样本或未验收事件作为需求依据。
- 不连接或写入真实生产 Supabase；数据库与存储验证使用安全、可重复的本地或测试环境。
- 凭证只能从环境变量或未跟踪配置注入，禁止写入仓库、日志、事件和报告。
- 每个 Task 独立遵守总改动量不超过 1,500 行；Subtask 建议不超过 800 行。
- 每个 Task 的测试分别满足 `Test:Feat <= 1.3` 或新增测试少于 450 行之一。

按 `plan → builder → critic → finalizer` 循环工作。Task 之间只通过已验收代码与 `.ralph/agent/task-progress.json` 交接，不新增业务事件。
