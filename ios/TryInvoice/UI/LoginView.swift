import SwiftUI

struct LoginView: View {
    @ObservedObject var viewModel: AuthViewModel
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        VStack(spacing: 16) {
            TextField("邮箱", text: $email)
                .textContentType(.emailAddress)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("login.email")
            SecureField("密码", text: $password)
                .textContentType(.password)
                .accessibilityIdentifier("login.password")
            Button("登录") {
                Task {
                    await viewModel.signIn(email: email, password: password)
                    if case .signedIn = viewModel.state {
                        password = ""
                    }
                }
            }
            .disabled(!viewModel.isLoginAvailable || viewModel.state == .signingIn)
            .accessibilityIdentifier("login.submit")
            if let error = viewModel.displayError {
                Text(error.userMessage)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("login.error")
            }
        }
        .padding()
    }
}
