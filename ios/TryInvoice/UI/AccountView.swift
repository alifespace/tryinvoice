import SwiftUI

struct AccountView: View {
    @ObservedObject var viewModel: AuthViewModel
    @State private var isDeleteConfirmationPresented = false

    let email: String

    var body: some View {
        VStack(spacing: 16) {
            Text(email)
                .accessibilityIdentifier("account.email")
            if case .deletingAccount = viewModel.state {
                ProgressView("正在删除账号…")
            } else if case .accountDeletedCleanupPending = viewModel.state {
                Text("账号数据已删除，本机退出未完成")
                    .accessibilityIdentifier("account.cleanup.pending")
                Button("重试本机退出") {
                    Task { await viewModel.retryLocalCleanup() }
                }
                .accessibilityIdentifier("account.cleanup.retry")
            } else {
                Button("退出登录") {
                    Task { await viewModel.signOut() }
                }
                Button("删除账号", role: .destructive) {
                    isDeleteConfirmationPresented = true
                }
                .accessibilityIdentifier("account.delete")
            }
            if let error = viewModel.displayError {
                Text(error.userMessage)
                    .font(.callout)
                    .foregroundStyle(.red)
            }
        }
        .padding()
        .confirmationDialog(
            "确定删除账号？此操作无法撤销。",
            isPresented: $isDeleteConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("确认删除", role: .destructive) {
                Task { await viewModel.deleteAccount() }
            }
            .accessibilityIdentifier("account.delete.confirm")
            Button("取消", role: .cancel) {}
        }
        .navigationTitle("账号")
    }
}
