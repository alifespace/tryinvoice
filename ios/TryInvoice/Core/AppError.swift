import Foundation

enum AppError: Error, Equatable {
    case configurationMissing(String)
    case configurationInvalid(String)
    case network
    case unauthorized
    case unknown

    var userMessage: String {
        switch self {
        case .configurationMissing:
            "应用配置缺失，请联系支持人员。"
        case .configurationInvalid:
            "应用配置无效，请联系支持人员。"
        case .network:
            "网络暂时不可用，请稍后重试。"
        case .unauthorized:
            "登录状态已失效，请重新登录。"
        case .unknown:
            "发生未知错误，请稍后重试。"
        }
    }
}
