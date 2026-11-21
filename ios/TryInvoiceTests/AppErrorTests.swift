import XCTest
@testable import TryInvoice

final class AppErrorTests: XCTestCase {
    func testErrorsHaveDistinctNonemptyUserMessages() {
        let errors: [AppError] = [
            .configurationMissing("API_BASE_URL"),
            .configurationInvalid("API_BASE_URL"),
            .network,
            .unauthorized,
            .unknown,
        ]
        let messages = errors.map(\.userMessage)

        XCTAssertTrue(messages.allSatisfy { !$0.trimmingCharacters(in: .whitespaces).isEmpty })
        XCTAssertEqual(Set(messages).count, errors.count)
    }

    func testMissingConfigurationMessageDoesNotExposeAssociatedValue() {
        let message = AppError.configurationMissing("secret-key-name").userMessage

        XCTAssertFalse(message.contains("secret-key-name"))
    }
}
