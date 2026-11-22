import SwiftUI

struct RootView: View {
    let environment: AppEnvironment
    @StateObject private var viewModel: AuthViewModel

    init(environment: AppEnvironment) {
        self.environment = environment
        _viewModel = StateObject(
            wrappedValue: AuthViewModel(
                authService: environment.authService,
                deleteAccount: environment.deleteAccount,
                configurationError: environment.authConfigurationError
            )
        )
    }

    var body: some View {
        NavigationStack {
            Group {
                switch viewModel.state {
                case .restoring:
                    ProgressView("正在恢复登录状态…")
                case .signedOut, .signingIn, .failed(_):
                    LoginView(viewModel: viewModel)
                case .deletingAccount:
                    ProgressView("正在删除账号…")
                case let .signedIn(email):
                    VStack(spacing: 12) {
                        Text("TryInvoice")
                            .font(.largeTitle)
                        Text("发票管理即将开始")
                            .font(.body)
                        NavigationLink("账号") {
                            AccountView(viewModel: viewModel, email: email)
                        }
                        .accessibilityIdentifier("home.account")
                        if let error = viewModel.displayError {
                            Text(error.userMessage)
                                .font(.callout)
                                .foregroundStyle(.red)
                        }
                    }
                case .accountDeletedCleanupPending:
                    AccountView(
                        viewModel: viewModel,
                        email: viewModel.accountEmail ?? ""
                    )
                }
            }
            .navigationTitle(viewModel.navigationTitle)
        }
        .task { await viewModel.restore() }
    }
}
