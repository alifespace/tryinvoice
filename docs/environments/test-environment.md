# TryInvoice 测试环境状态

## 1. Supabase

- **项目名称**：`demo01-staging`
- **项目引用**：`rmdiistbnhecafdtysva`
- **区域**：美国西部（Oregon，`us-west-2`）
- **用途确认**：项目所有者于 2026-11-21 确认，该环境当前没有其他项目继续使用，可作为 TryInvoice 的非生产测试环境。
- **现有数据边界**：项目中保留了旧项目的表、两个 Auth 用户和两个 Storage bucket；在没有单独删除授权前一律保留，不复用、覆盖或清理。
- **TryInvoice 隔离要求**：
  - 通过现有迁移脚本创建 TryInvoice 自有表、RLS 与默认模板，不修改旧表。
  - 新建独立的私有 Storage bucket，不复用 `floor-plans` 或 `project-renders`。
  - 新建 TryInvoice 专用 Auth 测试用户，不复用两个密码未知的旧用户。
  - 合成测试数据必须使用专用用户 ID 和独立对象路径前缀。

## 2. 凭证状态

- Supabase Dashboard 不提供现有 Auth 用户密码或数据库密码的明文查看能力。
- 数据库连接串当前只显示密码占位符；重置数据库密码不会影响已知在用项目，但仍属于高风险凭证变更，必须在 Render 部署候选完成并登记 attempt ID 后由项目所有者执行最终确认。
- 新数据库密码生成后应立即写入 Render Secret，并按需要写入本地未跟踪 `.env`；禁止进入聊天、Git、日志、截图或 CI Artifact。
- TryInvoice 测试用户密码应在创建时直接保存至受控 Secret/密码管理器，不得写入项目文件。

## 3. Render

- Render 账号已通过 GitHub 登录验证。
- 公开仓库 `alifespace/tryinvoice` 可通过 **Public Git Repository** 方式读取，无需授予 Render GitHub App 仓库权限。
- 尚未创建 Web Service，尚未注入任何 Supabase 凭证。
- Render 新建服务页面默认选择每月 7 美元实例；测试部署必须显式选择 **Free（0 美元、512 MB）**，并在最终创建前再次核对费用。
- 免费实例允许休眠和冷启动，只用于测试，不作为生产环境。

## 4. 后续人工门禁

1. Ralph 完成 Render 声明式配置、迁移命令、合成冒烟脚本和回滚说明。
2. Critic 复核实际部署参数、Free 方案、Secret 名称和清理方式。
3. 登记真实部署 attempt ID。
4. 项目所有者完成数据库密码重置的最终确认，并将新密码安全注入 Render。
5. 经项目所有者授权后创建 Render Free Web Service，执行迁移与合成数据冒烟测试。
