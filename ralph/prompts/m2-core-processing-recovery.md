# TryInvoice M2 拆分恢复提示词

本次启动一条全新的 M2 恢复控制平面循环。旧循环已经通过 `loop.cancel` 正常退出，不属于中断续跑；不得使用旧事件队列或把旧 `spec.ready` 直接交给 Builder。

## 权威任务顺序

以 `AGENTS.md`、`docs/hld.md`、`docs/lld.md`、`docs/milestones.md` 和 `docs/tasks/M2.*.md` 为业务与边界依据。M2 当前顺序为：

1. `M2.1-document-extractor`：已验收，保留证据，不重做。
2. `M2.2-deterministic-rule-engine-part1`：已验收，保留证据，不重做。
3. `M2.3-deterministic-rule-engine-part2`：按最新拆分，只承接已验收的 S1/S2 纯规则能力，保留 709 行验收证据，不继续旧 S3/S4。
4. `M2.4-batch-processing-pipeline`：本次首先执行。
5. `M2.5-transaction-review-api`：M2.4 完成后执行。

只有以上五个 Task 的最新边界全部达到验收状态，才可发布 `LOOP_COMPLETE`。

## 首轮账本迁移

Planner 收到 `work.start` 后，必须先确定性读取 `.ralph/agent/m2-progress.json`、旧 M2 事件与当前工作树，完成对账后再规划 M2.4：

- 原样保留 M2.1、M2.2 的 accepted 状态、行数和 Critic/Finalizer 证据。
- 原样保留 M2.3-S1、M2.3-S2 的 accepted 状态及累计 709 行证据；按最新 Task 边界将 M2.3 记录为已完成的纯规则 Task，并注明原批处理/API 范围已拆往 M2.4/M2.5。
- 保留旧 M2.3-S3 的失败与门禁证据作为审计记录，但不得继续使用其旧 Spec。现存候选为功能 375 行、测试 301 行、共 676 行，复杂度曾实测为 17；这些代码尚未验收，全部计入 M2.4 的候选范围。
- 将 `task_order` 迁移为 M2.1、M2.2、M2.3、M2.4、M2.5；初始化 M2.4 为当前 active Task、M2.5 为 pending，清除旧 current_subtask 指针后再创建新的 `M2.4-S1` Spec。
- M2.4 的代码量基线使用 M2.3-S2 验收终态：功能 3215、测试 2998、锁文件 1244。候选代码相对该基线的全部净增必须计入 M2.4，不得以恢复启动时的脏工作树作为新零基线规避统计。
- 账本迁移只改变控制平面状态，不得修改业务源码，不得伪造历史验收，也不得把旧候选标记为 accepted。

## M2.4 规划要求

Planner 必须先检查现存候选代码、`docs/tasks/M2.4-batch-processing-pipeline.md` 和失败证据，再按 Builder 能力重拆。优先把“提取与规则编排”和“交易/批次持久化同步”分成职责明确、可独立验收的 Subtask；不得为了凑行数制造重复接口或重复测试。

M2.4 计划累计改动量不超过 1,200 行并保留返工余量；项目 Task 硬上限为 2,000 行，Subtask 建议不超过 800 行。必须保留既定行为并把最大圈复杂度降低至 10 以下，不能通过删除增量处理、幂等、事务回滚、租户隔离或 `is_user_verified` 保护来通过门禁。

## M2.5 规划要求

M2.4 经 Critic 与 Finalizer 完成后，Planner 才能激活 `docs/tasks/M2.5-transaction-review-api.md`。M2.5 计划累计改动量不超过 1,000 行并保留返工余量；实现交易查询、人工修订、租户隔离与 OpenAPI 收口，不得夹带导出、后台队列、iOS UI、数据库迁移或新规则能力。

## 门禁与恢复

每个 Subtask 都必须经过 Builder 的七项真实门禁、Critic 独立复验和 Finalizer 确定性收尾。代码量、测试、覆盖率、复杂度与重复检测不得依赖 Metrics。单步受阻时优先修订 Spec、重拆或推进其他独立授权事项；只有整个 M2.4/M2.5 均无可执行事项时才允许 `loop.cancel`。
