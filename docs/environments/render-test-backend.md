# Render 测试后端运行手册

## 1. 冻结候选

- 声明文件：仓库根目录 `render.yaml`。
- 服务类型：Docker Web Service，区域 Oregon，方案 Free（0 美元、512 MB）；Free 实例会休眠并可能冷启动，仅用于测试。
- 仓库来源：Public Git Repository `alifespace/tryinvoice`。
- 创建前必须在 Render 确认选择 Free，并再次核对最终费用为 0 美元；不得默认接受付费方案。
- 本文件只记录候选步骤，不代表已经创建服务或授权执行。

## 2. Secret 名称与来源

以下名称仅供平台环境变量配置，不在仓库记录任何值：

| 名称 | 来源位置 |
| --- | --- |
| `DATABASE_URL` | Supabase 项目数据库连接设置；密码重置后使用新连接串 |
| `SUPABASE_URL` | Supabase 项目 API 设置 |
| `SUPABASE_JWT_SECRET` | Supabase 项目 JWT 设置 |
| `STORAGE_ENDPOINT_URL` | Supabase 项目 Storage/S3 设置 |
| `STORAGE_BUCKET` | 经授权创建的 TryInvoice 专用私有 bucket 名称 |
| `STORAGE_ACCESS_KEY_ID` | Supabase 项目 Storage/S3 设置 |
| `STORAGE_SECRET_ACCESS_KEY` | Supabase 项目 Storage/S3 设置 |

## 3. 待授权人工步骤

严格按顺序执行；任何一步开始前均须确认授权和热账本检查点：

1. 在 `.ralph/agent/m4-progress.json` 登记 attempt ID、版本、待执行命令与参数、清理方式。
2. 由项目所有者在 Supabase 完成数据库密码重置最终确认；将新凭证直接安全注入目标平台，不得复制到聊天或仓库。
3. 创建 TryInvoice 专用测试用户及私有 Storage bucket。不得复用或改动 `floor-plans`、`project-renders`、旧 Auth 用户及旧数据。
4. 在 Render 从 `render.yaml` 创建 Free Web Service，创建前复核费用，并通过 Dashboard 注入本手册所列变量。
5. 在受控本地环境或一次性容器中配置 `DATABASE_URL`（仅写入本地未跟踪 `.env`），再执行：

   ```bash
   uv run --no-sync python -m app.db.migrate
   uv run --no-sync python -m app.rules.seed
   ```

6. 授权后，在受控环境安全注入 `SMOKE_ACCESS_TOKEN`，运行合成数据冒烟：

   ```bash
   uv run --no-sync python -m app.ops.smoke --base-url https://<Render 服务域名>
   ```

   令牌仅从环境变量读取，不得作为命令行参数或写入命令历史；未注入令牌时退出码为 2。

## 4. Attempt 前置检查

执行前逐项写入热账本并复核：

| 项目 | 执行前记录 |
| --- | --- |
| Attempt ID 与版本 | 唯一 ID、候选版本及授权人 |
| 完整命令与参数 | 包括目标 Supabase 项目、Render 服务和迁移命令；不记录凭证值 |
| 费用确认 | Render 方案为 Free，最终费用为 0 美元 |
| 清理方式 | 服务停用/删除、专用测试用户和自建对象的处理方式 |
| 检查点 | 执行前状态、执行结果及失败后的对账方式 |

## 5. 回滚、清理与日志

- Render 异常或不再需要时，先挂起或删除 TryInvoice 测试服务；检查是否仍有资源计费。
- 数据库密码如已暴露或不再使用，应轮换并同步更新受控 Secret。
- 仅可清理本次 TryInvoice 创建的测试用户、bucket 和对象路径前缀；删除前核对归属。旧表、旧用户、旧 bucket（包括 `floor-plans` 与 `project-renders`）一律保留。
- Free 实例可能休眠，首次请求会冷启动；不可将其作为生产服务或可用性承诺。
- 检查部署和应用日志，不得包含凭证、JWT、原始票据、完整 OCR 文本或预签名 URL；对日志与制品执行凭证模式扫描。
