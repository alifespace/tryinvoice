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
}
