# TryInvoice 后端

本地环境要求 Python 3.14+ 与 uv。

```bash
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check .
uv run mypy app
```

本地凭证放在未跟踪的 `.env` 中；健康检查不依赖数据库或凭证。Supabase 新版 ES256 Access Token 默认通过 `SUPABASE_URL` 推导 JWKS 与 issuer，并校验 `authenticated` audience；可用 `SUPABASE_JWKS_URL`、`SUPABASE_JWT_ISSUER`、`SUPABASE_JWT_AUDIENCE` 覆盖。JWKS 缓存和请求超时可分别用 `SUPABASE_JWKS_CACHE_SECONDS` 与 `SUPABASE_JWKS_TIMEOUT_SECONDS` 配置。旧 HS256 仅在迁移期间显式设置 `SUPABASE_ALLOW_LEGACY_HS256=true` 后启用，并使用 `SUPABASE_JWT_SECRET`；默认关闭。
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

生产环境使用 `APP_ENV=production`，并优先通过部署平台的 Secret/环境变量机制注入配置。若使用生产 `--env-file`，必须删除或注释 `LOCAL_DEV_USER_ID=` 这一行，不能将它留空；空字符串会在应用启动时作为 UUID 解析并导致配置错误。本地开发如需在没有 Bearer 凭证时使用开发身份，须将 `LOCAL_DEV_USER_ID` 填为有效 UUID；不启用该身份时也应删除或注释该变量，不要留空。不要将 `.env`、密钥写入镜像或构建参数。容器每次启动会先依次运行数据库迁移与 CMI 默认模板 seed，再启动 API；两者均可重复运行。Render 部署时应将 Docker Command 留空，并且不配置 Pre-Deploy Command；容器入口会完成启动初始化。

容器健康检查访问 `GET /api/v1/health`，该端点不依赖数据库或凭证。`LOCAL_DEV_USER_ID` 仅在 `APP_ENV=local` 且未提供 Bearer 凭证时启用本地开发身份。

测试环境的 Render 候选配置、凭证名称与人工门禁见[运行手册](../docs/environments/render-test-backend.md)。
