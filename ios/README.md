# iOS 持续集成

M3.3 GitHub Actions 验证已通过，见
[验证记录](../docs/acceptance/M3.3-github-actions-validation.md)（run `44915742124`，
commit `b36fd19`）。iOS 工程由固定版本 XcodeGen 2.42.0 根据 `ios/project.yml` 生成；
生成的 `ios/TryInvoice.xcodeproj/` 与 `ios/build/` 不入库。

共享 Scheme `TryInvoice` 包含 App、`TryInvoiceTests` 单元测试 Target 和
`TryInvoiceUITests` UI 测试 Target。单元测试覆盖配置解析、错误用户文案和环境初始化；
UI 测试仅检查启动后的首页内容，不依赖账号或网络。

## 固定版本

| 项目 | 固定值 | 核实依据 |
| --- | --- | --- |
| GitHub runner | `macos-15` | [runner-images macOS 15 清单](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-Readme.md)，核实于 2026-11-21 |
| Xcode / Simulator | Xcode 16.4、iOS 18.5、iPhone 16 | 同一镜像清单列出 `/Applications/Xcode_16.4.app`、iOS 18.5 Simulator 和 iPhone 16 |
| XcodeGen | `2.42.0` | [固定版本发布页](https://github.com/yonaskolb/XcodeGen/releases/tag/2.42.0)；下载 zip 后校验 SHA-256 `a760b4bf97460341316a2c00d8c87b2946c6c2729eb57e9a72abb3ef7bc0ef82` |
| checkout | `v4.2.2` | Workflow 固定到 commit `11bd71901bbe5b1630ceea73d27597364c9af683` |
| upload-artifact | `v4.6.2` | Workflow 固定到 commit `ea165f8d65b6e75b540449e92b4886f43607fa02` |

Workflow 的触发范围为 `ios/**` 和自身配置，也可手动触发；并发运行会取消同一 ref
的旧运行。任务限时 45 分钟，仅授予 `contents: read`。

## 本地生成、检查与运行

安装 XcodeGen 2.42.0 和对应 Xcode 后，可从仓库根目录生成工程并运行模拟器测试：

```sh
xcodegen generate --spec ios/project.yml
xcodebuild test \
  -project ios/TryInvoice.xcodeproj \
  -scheme TryInvoice \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  CODE_SIGNING_ALLOWED=NO
```

CI 使用 `ios/scripts/ci-build-test.sh`，只在 `$RUNNER_TEMP` 下载并校验固定版本的
XcodeGen，生成工程后以 `CODE_SIGNING_ALLOWED=NO` 执行构建与测试。失败测试的附件先尝试
导出，再保留原始测试退出码。

## M4.2 人工门禁（待授权）

M4.2 的 Swift 工程真实编译、模拟器测试与 `.xcresult` 回执仍是人工门禁，当前未授权、
未运行；不得据 M3.3 回执宣称 M4.2 已通过。执行前须取得授权，并登记版本、attempt ID、
完整命令及清理方式；当前没有 attempt ID。

获授权并准备好待验证分支后，先登记 attempt，再执行：

```sh
git push origin <authorized-branch>
gh workflow run ios-ci.yml --ref <authorized-branch>
```

核对该 Actions run 对应的提交、构建与模拟器测试结果，并保存 `.xcresult` 回执。
本地生成物可按下节清理；远端分支或 Actions 产物仅按获批方案处理。

## 产物与清理

Artifact 仅收集 `ios/build/ci/**` 中的 `.xcresult`、日志和失败附件，不上传
DerivedData、签名文件、描述文件或 `.env*`。不配置 Secrets；当前探针及跳过路径不处理
发票或 OCR 内容。未来测试应继续使用合成或彻底脱敏数据。

## 认证配置

应用从 `SUPABASE_URL` 和 `SUPABASE_ANON_KEY` 读取 Supabase Auth 配置。仓库中的
`Debug.xcconfig` 与 `Release.xcconfig` 仅保留空占位；本地或 CI 应通过未提交的配置/平台
Secrets 注入真实值，不要将凭证写入源码、日志或测试产物。缺少配置时登录保持禁用。
认证由固定版本的 `supabase-swift` Auth SDK 管理，会话凭据使用 SDK 在 Apple 平台的默认安全存储。

## M4.3 真实接缝测试与 Secret 名称

CI 的 `LiveSeamTests` 使用独立测试账号验证 Supabase 登录与会话恢复，以及带 Bearer
认证的 API 请求。仅由项目所有者在 GitHub Actions 手工配置以下 Repository Secrets；
测试步骤要求全部存在，Secret 值不得写入仓库、测试断言或日志：

- `TRYINVOICE_TEST_API_BASE_URL`
- `TRYINVOICE_TEST_SUPABASE_URL`
- `TRYINVOICE_TEST_SUPABASE_ANON_KEY`
- `TRYINVOICE_TEST_EMAIL`
- `TRYINVOICE_TEST_PASSWORD`

CI 会在上传诊断制品前扫描 `ios/build/ci`，删除包含任一配置值或其 URL 编码形式的文件；
本地未配置接缝测试环境时，相关 XCTest 会跳过。

若账号删除 API 已返回 204、但本机清除会话及失效覆盖均失败，应用仅在内存中保留“账号数据已删除、
本机退出未完成”状态，并只允许重试本地清理，不会再次发送删除请求。此状态不跨重启持久化；
如果系统仍拒绝删除或覆盖安全存储，重启后可能恢复原登录会话。此时账号业务数据已删除，但
Supabase Auth 用户未删除；用户可再次退出或按后端幂等删除接口重试。

本地清理 CI 生成物：

```sh
rm -rf ios/TryInvoice.xcodeproj ios/build/ci ios/build/Info.plist
```

## API 类型同步

`contracts/openapi.json` 是 API 类型的来源。新增或修改契约后，从仓库根目录运行
`python3 ios/scripts/sync-openapi-types.py` 更新 `APITypes.generated.swift`；提交前运行
`python3 ios/scripts/sync-openapi-types.py --check` 检查生成文件是否与契约一致。生成文件由脚本
维护，不应手工编辑。
