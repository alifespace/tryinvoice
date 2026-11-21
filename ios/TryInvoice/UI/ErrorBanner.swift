import SwiftUI

private struct ErrorBannerModifier: ViewModifier {
    let error: AppError?

    func body(content: Content) -> some View {
        content.overlay(alignment: .top) {
            if let error {
                Text(error.userMessage)
                    .font(.callout)
                    .foregroundStyle(.white)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(.red)
                    .accessibilityAddTraits(.isStaticText)
            }
        }
    }
}

extension View {
    func errorBanner(_ error: AppError?) -> some View {
        modifier(ErrorBannerModifier(error: error))
    }
}
