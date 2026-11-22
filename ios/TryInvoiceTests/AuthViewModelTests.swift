import Foundation
import XCTest
@testable import TryInvoice

@MainActor
final class AuthViewModelTests: XCTestCase {
    func testSignInTrimsEmailAndDoesNotStorePassword() async {
        let service = StubAuthService()
        let viewModel = makeViewModel(service: service)

        await viewModel.signIn(email: "  user@example.test \n", password: "synthetic-password")

        XCTAssertEqual(viewModel.state, .signedIn("user@example.test"))
        XCTAssertFalse(Mirror(reflecting: viewModel).children.contains {
            ($0.value as? String) == "synthetic-password"
        })
        let calls = await service.snapshot()
        XCTAssertEqual(calls.signInCount, 1)
        XCTAssertEqual(calls.email, "user@example.test")
    }

    func testInvalidCredentialsAndNetworkErrorsAreMapped() async {
        let credentialService = StubAuthService(signInError: .unauthorized)
        let credentialModel = makeViewModel(service: credentialService)
        await credentialModel.signIn(email: "user@example.test", password: "bad")
        XCTAssertEqual(credentialModel.state, .failed(.unauthorized))

        let networkService = StubAuthService(signInError: .network)
        let networkModel = makeViewModel(service: networkService)
        await networkModel.signIn(email: "user@example.test", password: "synthetic-password")
        XCTAssertEqual(networkModel.state, .failed(.network))
    }

    func testSDKErrorMappingClassifiesNetworkCredentialsAndUnknown() {
        XCTAssertEqual(
            SupabaseAuthService.mapError(URLError(.notConnectedToInternet)),
            .network
        )
        XCTAssertEqual(
            SupabaseAuthService.mapError(NSError(domain: "Supabase.Auth", code: 401)),
            .unauthorized
        )
        XCTAssertEqual(
            SupabaseAuthService.mapError(NSError(domain: "Synthetic.Error", code: 1)),
            .unknown
        )
    }

    func testInvalidInputDoesNotCallAuthService() async {
        let service = StubAuthService()
        let viewModel = makeViewModel(service: service)

        await viewModel.signIn(email: "not-an-email", password: "")

        XCTAssertEqual(viewModel.state, .failed(.validation))
        let calls = await service.snapshot()
        XCTAssertEqual(calls.signInCount, 0)
    }

    func testRestoreWithAndWithoutSession() async {
        let signedInModel = makeViewModel(
            service: StubAuthService(session: AuthSession(userEmail: "user@example.test"))
        )
        await signedInModel.restore()
        XCTAssertEqual(signedInModel.state, .signedIn("user@example.test"))

        let signedOutModel = makeViewModel(service: StubAuthService())
        await signedOutModel.restore()
        XCTAssertEqual(signedOutModel.state, .signedOut)
    }

    func testSignOutClearsSessionState() async {
        let service = StubAuthService(session: AuthSession(userEmail: "user@example.test"))
        let viewModel = makeViewModel(service: service)
        await viewModel.restore()

        await viewModel.signOut()

        XCTAssertEqual(viewModel.state, .signedOut)
        let calls = await service.snapshot()
        XCTAssertEqual(calls.signOutCount, 1)
    }

    func testDeleteSuccessClearsLocalSessionAndFailureKeepsSignedIn() async {
        let successService = StubAuthService(
            session: AuthSession(userEmail: "user@example.test"),
            signOutError: .network
        )
        let successModel = makeViewModel(service: successService, deleteError: nil)
        await successModel.restore()
        await successModel.deleteAccount()
        XCTAssertEqual(successModel.state, .signedOut)
        let successCalls = await successService.snapshot()
        XCTAssertEqual(successCalls.signOutCount, 0)
        XCTAssertEqual(successCalls.clearLocalSessionCount, 1)
        let restoredSession = await successService.restoreSession()
        XCTAssertNil(restoredSession)

        let failureService = StubAuthService(session: AuthSession(userEmail: "user@example.test"))
        let failureModel = makeViewModel(service: failureService, deleteError: .server)
        await failureModel.restore()
        await failureModel.deleteAccount()
        XCTAssertEqual(failureModel.state, .signedIn("user@example.test"))
        XCTAssertEqual(failureModel.displayError, .server)
        let failureCalls = await failureService.snapshot()
        XCTAssertEqual(failureCalls.signOutCount, 0)
        XCTAssertEqual(failureCalls.clearLocalSessionCount, 0)
    }

    func testDeleteSuccessWithLocalCleanupDoubleFailureOnlyRetriesCleanup() async {
        let service = StubAuthService(
            session: AuthSession(userEmail: "user@example.test"),
            clearLocalSessionErrorsRemaining: 3
        )
        let deleteRequest = StubDeleteAccountRequest()
        let viewModel = AuthViewModel(
            authService: service,
            deleteAccount: { try await deleteRequest.call() },
            configurationError: nil
        )
        await viewModel.restore()

        await viewModel.deleteAccount()

        XCTAssertEqual(viewModel.state, .accountDeletedCleanupPending)
        XCTAssertEqual(viewModel.displayError, .unknown)
        var deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)

        await viewModel.deleteAccount()
        await viewModel.signOut()
        XCTAssertEqual(viewModel.state, .accountDeletedCleanupPending)
        deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)

        await viewModel.retryLocalCleanup()
        XCTAssertEqual(viewModel.state, .accountDeletedCleanupPending)
        XCTAssertEqual(viewModel.displayError, .unknown)
        await viewModel.restore()
        XCTAssertEqual(viewModel.state, .accountDeletedCleanupPending)
        let restoreCalls = await service.snapshot()
        XCTAssertEqual(restoreCalls.restoreSessionCount, 1)
        deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)

        await viewModel.deleteAccount()
        deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)

        await service.allowLocalSessionClear()
        await viewModel.retryLocalCleanup()
        XCTAssertEqual(viewModel.state, .signedOut)
        XCTAssertNil(viewModel.accountEmail)
        deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)
        let calls = await service.snapshot()
        XCTAssertEqual(calls.clearLocalSessionCount, 4)
    }

    func testDeleteInFlightBlocksConcurrentDeleteSignOutAndCleanupRetry() async {
        let service = StubAuthService(
            session: AuthSession(userEmail: "user@example.test"),
            pausesLocalSessionClear: true
        )
        let deleteRequest = StubDeleteAccountRequest()
        let viewModel = AuthViewModel(
            authService: service,
            deleteAccount: { try await deleteRequest.call() },
            configurationError: nil
        )
        await viewModel.restore()

        let deletion = Task { await viewModel.deleteAccount() }
        await service.waitForLocalSessionClear()

        XCTAssertEqual(viewModel.state, .accountDeletedCleanupPending)
        await viewModel.deleteAccount()
        await viewModel.signOut()
        await viewModel.retryLocalCleanup()

        var deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)
        var calls = await service.snapshot()
        XCTAssertEqual(calls.signOutCount, 0)
        XCTAssertEqual(calls.clearLocalSessionCount, 1)

        await service.resumeLocalSessionClear()
        await deletion.value

        XCTAssertEqual(viewModel.state, .signedOut)
        deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)
        calls = await service.snapshot()
        XCTAssertEqual(calls.signOutCount, 0)
        XCTAssertEqual(calls.clearLocalSessionCount, 1)
    }

    func testDeleteRequestFailureKeepsSignedInAndCanBeRetried() async {
        let service = StubAuthService(session: AuthSession(userEmail: "user@example.test"))
        let deleteRequest = StubDeleteAccountRequest(error: .server)
        let viewModel = AuthViewModel(
            authService: service,
            deleteAccount: { try await deleteRequest.call() },
            configurationError: nil
        )
        await viewModel.restore()

        await viewModel.deleteAccount()

        XCTAssertEqual(viewModel.state, .signedIn("user@example.test"))
        XCTAssertEqual(viewModel.displayError, .server)
        let deleteCount = await deleteRequest.callCount()
        XCTAssertEqual(deleteCount, 1)
        let calls = await service.snapshot()
        XCTAssertEqual(calls.clearLocalSessionCount, 0)
    }

    func testDeleteSuccessWithRemovalFailureCannotRestoreOldSession() async throws {
        let storage = InMemoryAuthLocalStorage(failRemovals: true)
        try storage.seedSession(email: "user@example.test")
        let service = makeSupabaseService(storage: storage)
        let viewModel = AuthViewModel(
            authService: service, deleteAccount: {}, configurationError: nil
        )
        await viewModel.restore()
        XCTAssertEqual(viewModel.state, .signedIn("user@example.test"))

        await viewModel.deleteAccount()

        XCTAssertEqual(viewModel.state, .signedOut)
        XCTAssertNil(viewModel.displayError)
        let restoredSession = await service.restoreSession()
        XCTAssertNil(restoredSession)
    }

    func testSupabaseLocalSessionClearRemovesSessionFromSDKStorage() async throws {
        let storage = InMemoryAuthLocalStorage()
        try storage.seedSession(email: "user@example.test")
        let service = makeSupabaseService(storage: storage)

        try await service.clearLocalSession()

        XCTAssertFalse(try storage.hasStoredSession())
        let sessionAfterRemoval = await service.restoreSession()
        XCTAssertNil(sessionAfterRemoval)
    }

    func testSupabaseLocalSessionClearPropagatesWhenRemovalAndFallbackFail() async throws {
        let storage = InMemoryAuthLocalStorage(failRemovals: true)
        try storage.seedSession(email: "user@example.test")
        storage.failFutureStores()
        let service = makeSupabaseService(storage: storage)

        do {
            try await service.clearLocalSession()
            XCTFail("Expected synthetic storage removal to fail")
        } catch {
            XCTAssertEqual(SupabaseAuthService.mapError(error), .unknown)
        }

        XCTAssertTrue(try storage.hasStoredSession())
        let sessionAfterFailedRemoval = await service.restoreSession()
        XCTAssertEqual(
            sessionAfterFailedRemoval,
            AuthSession(userEmail: "user@example.test")
        )
    }

    func testMissingConfigurationDisablesLoginAndReportsSafeError() async {
        let viewModel = AuthViewModel(
            authService: nil,
            deleteAccount: nil,
            configurationError: .configurationMissing("SUPABASE_ANON_KEY")
        )

        await viewModel.restore()

        XCTAssertFalse(viewModel.isLoginAvailable)
        XCTAssertEqual(viewModel.state, .failed(.configurationMissing("SUPABASE_ANON_KEY")))
        XCTAssertFalse(viewModel.displayError?.userMessage.contains("SUPABASE_ANON_KEY") ?? true)
    }

    private func makeViewModel(
        service: StubAuthService,
        deleteError: AppError? = nil
    ) -> AuthViewModel {
        AuthViewModel(
            authService: service,
            deleteAccount: {
                if let deleteError {
                    throw deleteError
                }
            },
            configurationError: nil
        )
    }

    private func makeSupabaseService(
        storage: InMemoryAuthLocalStorage
    ) -> SupabaseAuthService {
        SupabaseAuthService(
            supabaseURL: URL(string: "http://127.0.0.1")!,
            anonKey: "synthetic-anon-key",
            localStorage: storage
        )
    }
}

private actor StubAuthService: AuthService {
    private var session: AuthSession?
    private var signInError: AppError?
    private var signOutError: AppError?
    private var restoreSessionCount = 0
    private var signInCount = 0
    private var signOutCount = 0
    private var clearLocalSessionCount = 0
    private var clearLocalSessionErrorsRemaining: Int
    private let pausesLocalSessionClear: Bool
    private var localSessionClearStarted = false
    private var localSessionClearStartWaiters: [CheckedContinuation<Void, Never>] = []
    private var localSessionClearContinuation: CheckedContinuation<Void, Error>?
    private var email: String?

    init(
        session: AuthSession? = nil,
        signInError: AppError? = nil,
        signOutError: AppError? = nil,
        clearLocalSessionErrorsRemaining: Int = 0,
        pausesLocalSessionClear: Bool = false
    ) {
        self.session = session
        self.signInError = signInError
        self.signOutError = signOutError
        self.clearLocalSessionErrorsRemaining = clearLocalSessionErrorsRemaining
        self.pausesLocalSessionClear = pausesLocalSessionClear
    }

    func restoreSession() async -> AuthSession? {
        restoreSessionCount += 1
        return session
    }

    func signIn(email: String, password: String) async throws -> AuthSession {
        signInCount += 1
        self.email = email
        if let signInError {
            throw signInError
        }
        let result = AuthSession(userEmail: email)
        session = result
        return result
    }

    func signOut() async throws {
        signOutCount += 1
        if let signOutError {
            throw signOutError
        }
        session = nil
    }

    func clearLocalSession() async throws {
        clearLocalSessionCount += 1
        if pausesLocalSessionClear {
            localSessionClearStarted = true
            localSessionClearStartWaiters.forEach { $0.resume() }
            localSessionClearStartWaiters.removeAll()
            try await withCheckedThrowingContinuation {
                (continuation: CheckedContinuation<Void, Error>) in
                localSessionClearContinuation = continuation
            }
        }
        guard clearLocalSessionErrorsRemaining == 0 else {
            clearLocalSessionErrorsRemaining -= 1
            throw AppError.unknown
        }
        session = nil
    }

    func waitForLocalSessionClear() async {
        guard !localSessionClearStarted else {
            return
        }
        await withCheckedContinuation {
            (continuation: CheckedContinuation<Void, Never>) in
            localSessionClearStartWaiters.append(continuation)
        }
    }

    func resumeLocalSessionClear() {
        let continuation = localSessionClearContinuation
        localSessionClearContinuation = nil
        continuation?.resume()
    }

    func allowLocalSessionClear() {
        clearLocalSessionErrorsRemaining = 0
    }

    func snapshot() -> (
        restoreSessionCount: Int,
        signInCount: Int,
        signOutCount: Int,
        clearLocalSessionCount: Int,
        email: String?
    ) {
        (restoreSessionCount, signInCount, signOutCount, clearLocalSessionCount, email)
    }
}

private actor StubDeleteAccountRequest {
    private let error: AppError?
    private var calls = 0

    init(error: AppError? = nil) {
        self.error = error
    }

    func call() async throws {
        calls += 1
        if let error {
            throw error
        }
    }

    func callCount() -> Int { calls }
}
