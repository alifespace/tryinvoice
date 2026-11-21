# TryInvoice 后端

本地环境要求 Python 3.14+ 与 uv。

```bash
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy app
```

本地凭证放在未跟踪的 `.env` 中；健康检查不依赖数据库或凭证。
数据库迁移需在 `.env` 设置 `DATABASE_URL` 后执行 `uv run python -m app.db.migrate`；迁移按文件名顺序记录，重复运行会跳过已应用文件。
CMI 默认模板种子可在 `.env` 设置 `DATABASE_URL` 后执行 `uv run python -m app.rules.seed`；脚本会幂等插入或修复系统模板。

## 已知限制
批次删除先清理对象存储、再删除数据库批次；若与同一批次的并发 `documents/confirm` 交错，可能遗留无数据库记录的孤儿对象。V1 接受该限制，不做处理，后续由对账任务清理。

契约导出与校验：`uv run python -m app.contract`、`uv run python -m app.contract --check`。
账号删除仅清理业务数据与存储对象；`auth.users` 记录需通过 Supabase Admin API 删除，留待后续集成。
