import XCTest
@testable import TryInvoice

final class AppEnvironmentTests: XCTestCase {
    func testConfigurationAndErrorInitializersAreMutuallyExclusive() {
        guard case let .success(configuration) =
            AppConfiguration.load(from: ["API_BASE_URL": "https://api.example.test"])
        else {
            XCTFail("Expected synthetic configuration to be valid")
            return
        }

        let configuredEnvironment = AppEnvironment(configuration: configuration)
        XCTAssertNotNil(configuredEnvironment.configuration)
        XCTAssertNil(configuredEnvironment.error)

        let failedEnvironment = AppEnvironment(error: .network)
        XCTAssertNil(failedEnvironment.configuration)
        XCTAssertEqual(failedEnvironment.error, .network)
    }

    func testLiveEnvironmentReportsMissingBaseURLInTestBundle() {
        let environment = AppEnvironment.live(bundle: Bundle(for: AppEnvironmentTests.self))

        XCTAssertNil(environment.configuration)
        XCTAssertEqual(environment.error, .configurationMissing("API_BASE_URL"))
    }

    #if DEBUG
    @MainActor
    func testDebugAuthStubCanSignInWithoutSupabaseConfiguration() async {
        let configuration = AppConfiguration(
            apiBaseURL: URL(string: "https://api.example.test"),
            supabaseURL: nil,
            supabaseAnonKey: nil
        )
        let productionEnvironment = AppEnvironment(configuration: configuration)
        XCTAssertEqual(
            productionEnvironment.authConfigurationError,
            .configurationMissing("SUPABASE_URL")
        )
        let environment = AppEnvironment(
            uiTestConfiguration: configuration,
            authService: UITestAuthService(signedIn: false),
            deleteAccount: {}
        )
        let viewModel = AuthViewModel(
            authService: environment.authService,
            deleteAccount: environment.deleteAccount,
            configurationError: environment.authConfigurationError
        )

        XCTAssertTrue(viewModel.isLoginAvailable)
        await viewModel.restore()
        await viewModel.signIn(email: "user@example.test", password: "synthetic-password")
        XCTAssertEqual(viewModel.state, .signedIn("user@example.test"))
    }
    #endif
}
