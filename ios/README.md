# iOS 持续集成

`.github/workflows/ios-ci.yml` 提供仅限 Simulator、禁止签名的 iOS CI。当前仓库尚无
`ios/project.yml`，因此 CI 先检查 Apple 工具链与目标模拟器，再由构建脚本安全跳过；
加入 XcodeGen 项目规格后，脚本会生成工程、验证共享 Scheme `TryInvoice`，并执行构建和测试。

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

## 本地检查与运行

在 macOS 且已安装对应 Xcode 时，可运行：

```sh
XCODE_APP=/Applications/Xcode_16.4.app \
SIM_DEVICE="iPhone 16" SIM_OS=18.5 \
bash ios/scripts/ci-probe.sh

XCODEGEN_VERSION=2.42.0 SIM_DEVICE="iPhone 16" SIM_OS=18.5 \
bash ios/scripts/ci-build-test.sh
```

无 `ios/project.yml` 时构建脚本输出 `skip: ios/project.yml not found` 并成功退出。
有项目规格时，它只在 `$RUNNER_TEMP` 下载固定版本的 XcodeGen，校验 SHA-256 后生成工程，
使用 `CODE_SIGNING_ALLOWED=NO` 执行 `build-for-testing` 和 `test-without-building`。
失败测试的附件先尝试导出，再保留原始测试退出码。

## 人工门禁（待授权）

远端 `git@github.com:alifespace/tryinvoice.git` 的可见性尚未核实。核实远端、首次 push
或触发真实 GitHub Actions 都是人工门禁：必须先取得用户授权，并在执行前登记 attempt ID、
完整命令与清理方式；本 Subtask 不执行任何 GitHub 写入，也不消耗 macOS CI 分钟。
actionlint 与 XcodeGen 未在开发机安装；不为本地验收安装全局工具。

获授权后，人工可按需执行的命令示例：

```sh
gh repo view alifespace/tryinvoice --json name,isPrivate,url
git push origin <authorized-branch>
gh workflow run ios-ci.yml --ref <authorized-branch>
```

## 产物与清理

Artifact 仅收集 `ios/build/ci/**` 中的 `.xcresult`、日志和失败附件，不上传
DerivedData、签名文件、描述文件或 `.env*`。不配置 Secrets；当前探针及跳过路径不处理
发票或 OCR 内容。未来测试应继续使用合成或彻底脱敏数据。

本地清理 CI 生成物：

```sh
rm -rf ios/build/ci
```
