import Foundation
import XCTest
@testable import TryInvoice

final class UploadStateTests: XCTestCase {
    func testLegalTransitions() {
        XCTAssertEqual(UploadState.pending.next(.start, attempt: 0), .uploading)
        XCTAssertEqual(UploadState.retryableFailed.next(.start, attempt: 1), .uploading)
        XCTAssertEqual(UploadState.uploading.next(.presignedExisting, attempt: 0), .instant)
        XCTAssertEqual(UploadState.uploading.next(.confirmed, attempt: 0), .uploaded)
        XCTAssertEqual(UploadState.uploading.next(.failed(.permanent), attempt: 0), .permanentFailed)
        XCTAssertEqual(UploadState.uploading.next(.failed(.retryable), attempt: 0), .retryableFailed)
    }

    func testIllegalCombinationsReturnCurrentState() {
        let legal: Set<String> = [
            "pending:start",
            "retryableFailed:start",
            "uploading:presignedExisting",
            "uploading:confirmed",
            "uploading:failed",
            "uploading:launchRecovery",
        ]
        let events: [(name: String, event: UploadEvent)] = [
            ("start", .start),
            ("presignedExisting", .presignedExisting),
            ("confirmed", .confirmed),
            ("failed", .failed(.retryable)),
            ("launchRecovery", .launchRecovery),
        ]

        for state in UploadState.allCases {
            for item in events {
                let moved = state.next(item.event, attempt: 0)
                if legal.contains("\(state.rawValue):\(item.name)") {
                    XCTAssertNotEqual(moved, state, "\(state.rawValue) should react to \(item.name)")
                } else {
                    XCTAssertEqual(moved, state, "\(state.rawValue) must stay put on \(item.name)")
                }
            }
        }
    }

    func testTerminalStatesNeverMigrate() {
        for state in [UploadState.uploaded, .instant, .permanentFailed] {
            XCTAssertTrue(state.isTerminal)
            for event in [
                UploadEvent.start,
                .presignedExisting,
                .confirmed,
                .failed(.retryable),
                .failed(.permanent),
                .launchRecovery,
            ] {
                XCTAssertEqual(state.next(event, attempt: 0), state)
            }
        }
        XCTAssertFalse(UploadState.pending.isTerminal)
        XCTAssertFalse(UploadState.uploading.isTerminal)
        XCTAssertFalse(UploadState.retryableFailed.isTerminal)
    }

    func testRetryableFlagOnlyMarksRetryableFailure() {
        XCTAssertEqual(UploadState.allCases.filter(\.isRetryable), [.retryableFailed])
    }

    func testRetryExhaustionBecomesPermanentAfterFiveAttempts() {
        var state = UploadState.pending
        var attempt = 0
        while !state.isTerminal, attempt <= 10 {
            state = state.next(.start, attempt: attempt)
            state = state.next(.failed(.retryable), attempt: attempt)
            attempt += 1
        }

        XCTAssertEqual(state, .permanentFailed)
        XCTAssertEqual(attempt, 5)
    }

    func testCustomMaxAttemptsIsHonoured() {
        XCTAssertEqual(
            UploadState.uploading.next(.failed(.retryable), attempt: 1, maxAttempts: 3),
            .retryableFailed
        )
        XCTAssertEqual(
            UploadState.uploading.next(.failed(.retryable), attempt: 2, maxAttempts: 3),
            .permanentFailed
        )
    }

    func testLaunchRecoveryResetsStaleUploadingAndAllowsRestart() {
        let recovered = UploadState.uploading.next(.launchRecovery, attempt: 3)

        XCTAssertEqual(recovered, .pending)
        XCTAssertEqual(recovered.next(.start, attempt: 0), .uploading)
    }

    func testFailureClassificationTable() {
        let table: [(AppError, UploadFailure)] = [
            (.network, .retryable),
            (.server, .retryable),
            (.unauthorized, .retryable),
            (.validation, .permanent),
            (.unknown, .permanent),
            (.configurationMissing("synthetic"), .permanent),
            (.configurationInvalid("synthetic"), .permanent),
        ]

        for (error, expected) in table {
            XCTAssertEqual(UploadFailure.classify(error), expected)
        }
    }

    func testStatesEncodeWithStableRawValues() throws {
        XCTAssertEqual(
            UploadState.allCases.map(\.rawValue),
            ["pending", "uploading", "uploaded", "instant", "retryableFailed", "permanentFailed"]
        )
        let data = try JSONEncoder().encode(UploadState.retryableFailed)
        XCTAssertEqual(String(decoding: data, as: UTF8.self), "\"retryableFailed\"")
        XCTAssertEqual(try JSONDecoder().decode(UploadState.self, from: data), .retryableFailed)
    }
}
