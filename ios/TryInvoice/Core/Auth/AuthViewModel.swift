import Combine
import Foundation

enum AuthViewState: Equatable {
    case restoring
    case signedOut
    case signingIn
    case deletingAccount
    case signedIn(String)
    case accountDeletedCleanupPending
    case failed(AppError)
}

@MainActor
final class AuthViewModel: ObservableObject {
    private enum AccountOperation {
        case idle
        case signingOut
        case deletingAccount
        case clearingDeletedAccountSession
    }

    @Published private(set) var state: AuthViewState = .restoring
    @Published private(set) var actionError: AppError?
    @Published private(set) var accountEmail: String?

    private let authService: (any AuthService)?
    private let deleteAccountRequest: (() async throws -> Void)?
    private let configurationError: AppError?
    private var accountOperation = AccountOperation.idle
    private var hasStartedRestore = false

    init(
        authService: (any AuthService)?,
        deleteAccount: (() async throws -> Void)?,
        configurationError: AppError?
    ) {
        self.authService = authService
        self.deleteAccountRequest = deleteAccount
        self.configurationError = configurationError
    }

    var displayError: AppError? {
        if case let .failed(error) = state {
            return error
        }
        return actionError
    }

    var isLoginAvailable: Bool {
        authService != nil && configurationError == nil
    }

    var navigationTitle: String {
        if case .signedIn = state {
            "首页"
        } else if case .accountDeletedCleanupPending = state {
            "账号"
        } else if case .deletingAccount = state {
            "账号"
        } else {
            "登录"
        }
    }

    func restore() async {
        guard !hasStartedRestore, case .restoring = state else {
            return
        }
        hasStartedRestore = true
        actionError = nil
        guard let authService else {
            state = .failed(configurationError ?? .configurationMissing("SUPABASE_URL"))
            return
        }
        state = .restoring
        if let session = await authService.restoreSession() {
            accountEmail = session.userEmail
            state = .signedIn(session.userEmail)
        } else {
            accountEmail = nil
            state = .signedOut
        }
    }

    func signIn(email: String, password: String) async {
        actionError = nil
        let normalizedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedEmail.isEmpty, normalizedEmail.contains("@"), !password.isEmpty else {
            state = .failed(.validation)
            return
        }
        guard let authService, configurationError == nil else {
            state = .failed(configurationError ?? .configurationMissing("SUPABASE_URL"))
            return
        }

        state = .signingIn
        do {
            let session = try await authService.signIn(
                email: normalizedEmail,
                password: password
            )
            accountEmail = session.userEmail
            state = .signedIn(session.userEmail)
        } catch {
            state = .failed(SupabaseAuthService.mapError(error))
        }
    }

    func signOut() async {
        if case .accountDeletedCleanupPending = state {
            await retryLocalCleanup()
            return
        }
        guard accountOperation == .idle else {
            return
        }
        actionError = nil
        guard let authService else {
            actionError = configurationError ?? .configurationMissing("SUPABASE_URL")
            return
        }
        accountOperation = .signingOut
        do {
            try await authService.signOut()
            accountEmail = nil
            state = .signedOut
        } catch {
            actionError = SupabaseAuthService.mapError(error)
        }
        accountOperation = .idle
    }

    func deleteAccount() async {
        if case .accountDeletedCleanupPending = state {
            return
        }
        guard accountOperation == .idle, case let .signedIn(email) = state else {
            return
        }
        actionError = nil
        guard let deleteAccountRequest else {
            actionError = .configurationMissing("API_BASE_URL")
            return
        }
        guard let authService else {
            actionError = configurationError ?? .configurationMissing("SUPABASE_URL")
            return
        }

        accountOperation = .deletingAccount
        state = .deletingAccount
        do {
            try await deleteAccountRequest()
        } catch {
            accountOperation = .idle
            state = .signedIn(email)
            actionError = SupabaseAuthService.mapError(error)
            return
        }

        // Lock out normal account actions as soon as the server confirms deletion.
        state = .accountDeletedCleanupPending
        accountOperation = .clearingDeletedAccountSession
        do {
            try await authService.clearLocalSession()
            accountEmail = nil
            state = .signedOut
            actionError = nil
        } catch {
            actionError = .unknown
        }
        accountOperation = .idle
    }

    func retryLocalCleanup() async {
        guard
            accountOperation == .idle,
            case .accountDeletedCleanupPending = state
        else {
            return
        }
        actionError = nil
        guard let authService else {
            actionError = .unknown
            return
        }
        accountOperation = .clearingDeletedAccountSession
        do {
            try await authService.clearLocalSession()
            accountEmail = nil
            state = .signedOut
        } catch {
            actionError = .unknown
        }
        accountOperation = .idle
    }
}
