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

## 容器与部署

在仓库根目录构建后端镜像：

```bash
docker build -t tryinvoice-backend backend/
```

`.env.example` 仅为配置模板。复制为 `.env` 后，先按运行环境调整变量，再运行：

```bash
docker run --env-file .env -p 8000:8000 tryinvoice-backend
```

生产环境使用 `APP_ENV=production`，并优先通过部署平台的 Secret/环境变量机制注入配置。若使用生产 `--env-file`，必须删除或注释 `LOCAL_DEV_USER_ID=` 这一行，不能将它留空；空字符串会在应用启动时作为 UUID 解析并导致配置错误。本地开发如需在没有 Bearer 凭证时使用开发身份，须将 `LOCAL_DEV_USER_ID` 填为有效 UUID；不启用该身份时也应删除或注释该变量，不要留空。不要将 `.env`、密钥写入镜像或构建参数。镜像不会在启动时自动迁移数据库；配置好 `DATABASE_URL` 后，需手动执行迁移：

```bash
docker run --rm --env-file .env --entrypoint uv tryinvoice-backend run --no-sync python -m app.db.migrate
```

容器健康检查访问 `GET /api/v1/health`，该端点不依赖数据库或凭证。`LOCAL_DEV_USER_ID` 仅在 `APP_ENV=local` 且未提供 Bearer 凭证时启用本地开发身份。
