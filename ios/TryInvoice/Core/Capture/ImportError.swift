import Foundation

/// 导入与端侧 OCR 的统一错误。错误不携带路径、文件名或内容，可直接展示给用户。
enum ImportError: Error, Equatable {
    case unsupportedType
    case fileTooLarge
    case corruptFile
    case duplicate
    case ocrFailed
    case permissionDenied
    case cancelled

    var userMessage: String {
        switch self {
        case .unsupportedType:
            return "仅支持 JPG、PNG、HEIC 图片或 PDF 文件。"
        case .fileTooLarge:
            return "文件为空或超过大小上限，请重新选择。"
        case .corruptFile:
            return "文件已损坏或无法读取，请重新导出后再试。"
        case .duplicate:
            return "该文件已导入过，无需重复添加。"
        case .ocrFailed:
            return "文字识别失败，可重试或改用更清晰的图片。"
        case .permissionDenied:
            return "未获得相册或相机权限，请在系统设置中开启。"
        case .cancelled:
            return "已取消导入。"
        }
    }

    /// 所有导入错误都允许用户修正后重试，不产生不可恢复状态。
    var isRecoverable: Bool {
        true
    }
}
