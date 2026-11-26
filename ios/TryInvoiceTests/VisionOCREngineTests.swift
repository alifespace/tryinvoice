import Foundation
import XCTest
@testable import TryInvoice

final class VisionOCREngineTests: XCTestCase {
    private var fixtures: FixtureDirectory!

    override func setUpWithError() throws { fixtures = try FixtureDirectory() }
    override func tearDownWithError() throws { fixtures.remove(); fixtures = nil }

    func testImageRecognitionReturnsNormalizedStructure() async throws {
        let url = try fixtures.write(try SyntheticFixtures.pngData(), named: "scan.png")
        let document = try await VisionOCREngine().recognize(fileAt: url, kind: .image)
        XCTAssertEqual(document.pageCount, 1)
        XCTAssertFalse(document.truncated)
        assertNormalized(document)
    }

    func testPDFRecognitionReportsPageCountAndTruncation() async throws {
        let url = try fixtures.write(SyntheticFixtures.pdfData(pages: 3), named: "batch.pdf")
        let document = try await VisionOCREngine(maxPages: 1).recognize(fileAt: url, kind: .pdf)
        XCTAssertEqual(document.pageCount, 3)
        XCTAssertTrue(document.truncated)
        XCTAssertTrue(document.lines.allSatisfy { $0.page == 0 })
        assertNormalized(document)
    }

    func testUnreadableImageThrowsOCRFailed() async throws {
        let url = try fixtures.write(Data([0xFF, 0xD8, 0xFF, 0x00, 0x11]), named: "broken.jpg")
        do {
            _ = try await VisionOCREngine().recognize(fileAt: url, kind: .image)
            XCTFail("expected ocrFailed")
        } catch let error as ImportError {
            XCTAssertEqual(error, .ocrFailed)
        }
    }

    func testSinglePagePDFWithDefaultEngineIsComplete() async throws {
        let url = try fixtures.write(SyntheticFixtures.pdfData(pages: 1), named: "one.pdf")
        let document = try await VisionOCREngine().recognize(fileAt: url, kind: .pdf)
        XCTAssertEqual(document.pageCount, 1)
        XCTAssertFalse(document.truncated)
        XCTAssertTrue(document.lines.allSatisfy { $0.page == 0 })
        assertNormalized(document)
    }

    /// 注入页数不能突破 Spec 的 10 页硬上限。
    func testInjectedMaxPagesIsCappedAtTen() {
        XCTAssertEqual(VisionOCREngine.clampedMaxPages(50), 10)
        XCTAssertEqual(VisionOCREngine.clampedMaxPages(10), 10)
        XCTAssertEqual(VisionOCREngine.clampedMaxPages(2), 2)
        XCTAssertEqual(VisionOCREngine.clampedMaxPages(0), 1)
        XCTAssertEqual(VisionOCREngine.clampedMaxPages(-1), 1)
    }

    private func assertNormalized(
        _ document: OCRDocument,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        for item in document.lines {
            let trimmed = item.text.trimmingCharacters(in: .whitespacesAndNewlines)
            XCTAssertFalse(trimmed.isEmpty, file: file, line: line)
            XCTAssertTrue((0...1).contains(item.confidence), file: file, line: line)
            for value in [item.box.x, item.box.y, item.box.width, item.box.height] {
                XCTAssertTrue((0...1).contains(value), file: file, line: line)
            }
        }
        XCTAssertEqual(document.lines, OCRDocument.normalize(document.lines), file: file, line: line)
    }
}
