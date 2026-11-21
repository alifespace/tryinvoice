import XCTest
@testable import TryInvoice

final class AppConfigurationTests: XCTestCase {
    func testMissingAndBlankValuesAreReportedAsMissing() {
        XCTAssertEqual(
            AppConfiguration.load(from: [:]),
            .failure(.configurationMissing("API_BASE_URL"))
        )
        for value in ["", " \n\t"] {
            XCTAssertEqual(
                AppConfiguration.load(from: ["API_BASE_URL": value]),
                .failure(.configurationMissing("API_BASE_URL"))
            )
        }
    }

    func testNonStringValueIsInvalid() {
        XCTAssertEqual(
            AppConfiguration.load(from: ["API_BASE_URL": 42]),
            .failure(.configurationInvalid("API_BASE_URL"))
        )
    }

    func testInsecureAndHostlessURLsAreInvalid() {
        for value in [
            "http://example.com",
            "ftp://api.example.test",
            "https:///api",
            "https://",
        ] {
            XCTAssertEqual(
                AppConfiguration.load(from: ["API_BASE_URL": value]),
                .failure(.configurationInvalid("API_BASE_URL")),
                value
            )
        }
    }

    func testSecureAndLocalDevelopmentURLsAreAcceptedUnchanged() {
        for value in [
            "https://api.example.test",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ] {
            guard case let .success(configuration) =
                AppConfiguration.load(from: ["API_BASE_URL": value])
            else {
                XCTFail("Expected URL to be accepted: \(value)")
                continue
            }
            XCTAssertEqual(configuration.apiBaseURL?.absoluteString, value)
        }
    }
}
