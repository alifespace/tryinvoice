import SwiftUI

private struct ErrorBannerModifier: ViewModifier {
    let message: String?
    let identifier: String?

    func body(content: Content) -> some View {
        content.overlay(alignment: .top) {
            if let message {
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.white)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(.red)
                    .accessibilityAddTraits(.isStaticText)
                    .accessibilityIdentifier(identifier ?? "error.banner")
            }
        }
    }
}

extension View {
    func errorBanner(_ error: AppError?) -> some View {
        modifier(ErrorBannerModifier(message: error?.userMessage, identifier: nil))
    }

    /// 以纯文本展示保存失败等非 `AppError` 提示，并可指定 UI 测试标识。
    func errorBanner(message: String?, identifier: String) -> some View {
        modifier(ErrorBannerModifier(message: message, identifier: identifier))
    }
}
