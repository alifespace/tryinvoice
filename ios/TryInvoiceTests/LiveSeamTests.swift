import Foundation
import XCTest
@testable import TryInvoice

final class LiveSeamTests: XCTestCase {
    private var configuration: SeamConfiguration?
    private var authService: SupabaseAuthService?

    override func setUp() async throws {
        try await super.setUp()

        guard let configuration = try loadConfiguration() else {
            return
        }
        self.configuration = configuration
        let service = SupabaseAuthService(
            supabaseURL: configuration.supabaseURL,
            anonKey: configuration.supabaseAnonKey
        )
        authService = service
        do {
            try await service.clearLocalSession()
        } catch {
            authService = nil
            XCTFail("Could not prepare the local authentication session")
        }
    }

    override func tearDown() async throws {
        if let authService {
            // Revoke the server session when possible, then always attempt local cleanup.
            try? await authService.signOut()
            try? await authService.clearLocalSession()
        }
        authService = nil
        configuration = nil
        try await super.tearDown()
    }

    func testLiveSignInReturnsSessionWithEmail() async throws {
        guard let authService else { return }

        let session = try await authService.signIn(
            email: try XCTUnwrap(configuration?.email),
            password: try XCTUnwrap(configuration?.password)
        )

        XCTAssertFalse(session.userEmail.isEmpty, "The authenticated session should contain an email")
    }

    func testLiveSessionRestoresInANewServiceInstance() async throws {
        guard let authService, let configuration else { return }
        let signedInSession = try await authService.signIn(
            email: configuration.email,
            password: configuration.password
        )
        XCTAssertFalse(
            signedInSession.userEmail.isEmpty,
            "The authenticated session should contain an email"
        )

        let restoredService = SupabaseAuthService(
            supabaseURL: configuration.supabaseURL,
            anonKey: configuration.supabaseAnonKey
        )
        let restoredSession = await restoredService.restoreSession()

        XCTAssertNotNil(restoredSession, "A new service instance should restore the session")
        XCTAssertFalse(
            restoredSession?.userEmail.isEmpty ?? true,
            "The restored session should contain an email"
        )
    }

    func testLiveBearerRequestListsBatchesAfterHealthWarmup() async throws {
        guard let authService, let configuration else { return }
        try await signIn(using: authService, configuration: configuration)
        let client = makeAPIClient(
            configuration: configuration,
            tokenProvider: authService
        )

        try await warmAPI(client)
        let page = try await client.listBatches(limit: 1, offset: 0)

        XCTAssertEqual(page.limit, 1)
        XCTAssertEqual(page.offset, 0)
    }

    func testLiveUnauthorizedRequestRefreshesOnceAndThenFailsSafely() async throws {
        guard let authService, let configuration else { return }
        try await signIn(using: authService, configuration: configuration)
        let refreshingClient = makeAPIClient(
            configuration: configuration,
            tokenProvider: RefreshAfterUnauthorizedProvider(service: authService)
        )

        try await warmAPI(refreshingClient)
        _ = try await refreshingClient.listBatches(limit: 1, offset: 0)

        let invalidClient = makeAPIClient(
            configuration: configuration,
            tokenProvider: AlwaysInvalidTokenProvider()
        )
        do {
            _ = try await invalidClient.listBatches(limit: 1, offset: 0)
            XCTFail("Expected an unauthorized response after one retry")
        } catch let error as AppError {
            XCTAssertEqual(error, .unauthorized)
        } catch {
            XCTFail("The invalid-token request should map to an unauthorized error")
        }
    }

    private func loadConfiguration() throws -> SeamConfiguration? {
        let environment = ProcessInfo.processInfo.environment
        let names = [
            "TRYINVOICE_SEAM_API_BASE_URL",
            "TRYINVOICE_SEAM_SUPABASE_URL",
            "TRYINVOICE_SEAM_SUPABASE_ANON_KEY",
            "TRYINVOICE_SEAM_EMAIL",
            "TRYINVOICE_SEAM_PASSWORD",
        ]
        let values = names.map { environment[$0] ?? environment["TEST_RUNNER_\($0)"] }
        guard values.allSatisfy({
            guard let value = $0 else { return false }
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }),
              let apiBaseURLValue = values[0],
              let supabaseURLValue = values[1],
              let anonKey = values[2],
              let email = values[3],
              let password = values[4],
              let apiBaseURL = Self.safeHTTPSURL(
                apiBaseURLValue.trimmingCharacters(in: .whitespacesAndNewlines)
              ),
              let supabaseURL = Self.safeHTTPSURL(
                supabaseURLValue.trimmingCharacters(in: .whitespacesAndNewlines)
              )
        else {
            let required = environment["TRYINVOICE_SEAM_REQUIRED"]
                ?? environment["TEST_RUNNER_TRYINVOICE_SEAM_REQUIRED"]
            if required == "1" {
                XCTFail("Required live seam configuration is incomplete or invalid")
                return nil
            }
            throw XCTSkip("Live seam configuration is not present")
        }

        return SeamConfiguration(
            apiBaseURL: apiBaseURL,
            supabaseURL: supabaseURL,
            supabaseAnonKey: anonKey,
            email: email,
            password: password
        )
    }

    private static func safeHTTPSURL(_ value: String) -> URL? {
        guard
            let components = URLComponents(string: value),
            components.scheme?.lowercased() == "https",
            let host = components.host,
            !host.isEmpty,
            components.user == nil,
            components.password == nil,
            components.query == nil,
            components.fragment == nil
        else {
            return nil
        }
        return components.url
    }

    private func signIn(
        using service: SupabaseAuthService,
        configuration: SeamConfiguration
    ) async throws {
        let session = try await service.signIn(
            email: configuration.email,
            password: configuration.password
        )
        XCTAssertFalse(session.userEmail.isEmpty, "The authenticated session should contain an email")
    }

    private func makeAPIClient(
        configuration: SeamConfiguration,
        tokenProvider: any AccessTokenProvider
    ) -> APIClient {
        APIClient(
            baseURL: configuration.apiBaseURL,
            tokenProvider: tokenProvider,
            timeout: 90
        )
    }

    private func warmAPI(_ client: APIClient) async throws {
        for attempt in 0...3 {
            do {
                _ = try await client.health()
                return
            } catch {
                guard attempt < 3 else { throw error }
                try await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }
}

private struct SeamConfiguration {
    let apiBaseURL: URL
    let supabaseURL: URL
    let supabaseAnonKey: String
    let email: String
    let password: String
}

private struct RefreshAfterUnauthorizedProvider: AccessTokenProvider {
    let service: SupabaseAuthService

    func accessToken() async throws -> String {
        "synthetic-invalid-access-token"
    }

    func refreshedAccessToken() async throws -> String {
        try await service.refreshedAccessToken()
    }
}

private struct AlwaysInvalidTokenProvider: AccessTokenProvider {
    func accessToken() async throws -> String {
        "synthetic-invalid-access-token"
    }

    func refreshedAccessToken() async throws -> String {
        "synthetic-invalid-access-token"
    }
}
