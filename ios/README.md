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

本地清理 CI 生成物：

```sh
rm -rf ios/TryInvoice.xcodeproj ios/build/ci ios/build/Info.plist
```
