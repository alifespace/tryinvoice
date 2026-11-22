import Foundation

/// 上传队列的本地状态。状态机是纯逻辑，不产生网络、磁盘或持久化副作用。
enum UploadState: String, Codable, CaseIterable, Sendable {
    case pending
    case uploading
    case uploaded
    case instant
    case retryableFailed
    case permanentFailed

    /// 已入库、秒传或永久失败后不再迁移。
    var isTerminal: Bool {
        switch self {
        case .uploaded, .instant, .permanentFailed:
            return true
        case .pending, .uploading, .retryableFailed:
            return false
        }
    }

    /// 仅可重试失败允许重新入队；其余状态不是重试候选。
    var isRetryable: Bool {
        self == .retryableFailed
    }

    /// 幂等状态迁移。非法组合与终态一律返回当前状态，不抛错、不崩溃。
    func next(_ event: UploadEvent, attempt: Int, maxAttempts: Int = 5) -> UploadState {
        guard !isTerminal else { return self }

        switch (self, event) {
        case (.pending, .start), (.retryableFailed, .start):
            return .uploading
        case (.uploading, .presignedExisting):
            return .instant
        case (.uploading, .confirmed):
            return .uploaded
        case (.uploading, .failed(.permanent)):
            return .permanentFailed
        case (.uploading, .failed(.retryable)):
            return attempt + 1 < maxAttempts ? .retryableFailed : .permanentFailed
        case (.uploading, .launchRecovery):
            // 崩溃或强杀后残留的 uploading 必须在启动时回收，避免永久卡死。
            return .pending
        default:
            return self
        }
    }
}

enum UploadEvent: Equatable, Sendable {
    case start
    case presignedExisting
    case confirmed
    case failed(UploadFailure)
    case launchRecovery
}

enum UploadFailure: Error, Equatable, Sendable {
    case retryable
    case permanent

    /// 仅网络、服务端与授权问题值得重试；校验、配置与未知错误重试无意义。
    static func classify(_ error: AppError) -> UploadFailure {
        switch error {
        case .network, .server, .unauthorized:
            return .retryable
        case .validation, .unknown, .configurationMissing, .configurationInvalid:
            return .permanent
        }
    }
}
