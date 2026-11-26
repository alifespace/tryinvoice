import Foundation
import XCTest
@testable import TryInvoice

final class OCRResultTests: XCTestCase {
    private func line(
        _ text: String,
        _ confidence: Double = 0.9,
        x: Double = 0,
        y: Double = 0,
        page: Int = 0
    ) -> OCRLine {
        OCRLine(
            text: text,
            confidence: confidence,
            box: OCRBox(x: x, y: y, width: 0.2, height: 0.1),
            page: page
        )
    }
    func testNormalizationTrimsDropsBlankAndClampsConfidence() {
        let document = OCRDocument(
            lines: [line("   "), line(" TOTAL 12.50 ", 1.4), line("TAX", -0.2), line("nan", .nan)],
            pageCount: 1,
            truncated: false
        )
        XCTAssertEqual(document.lines.map(\.text), ["TOTAL 12.50", "TAX", "nan"])
        XCTAssertEqual(document.lines.map(\.confidence), [1, 0, 0])
    }
    func testSortOrdersByPageThenYThenXAndKeepsEqualKeysStable() {
        let document = OCRDocument(
            lines: [
                line("c", x: 0.5, y: 0.2, page: 1),
                line("b", x: 0.1, y: 0.7),
                line("a2", x: 0.9, y: 0.1),
                line("a1", x: 0.2, y: 0.1),
                line("second"),
                line("first"),
            ],
            pageCount: 2,
            truncated: false
        )
        XCTAssertEqual(document.lines.map(\.text), ["second", "first", "a1", "a2", "b", "c"])
    }
    func testVisionBoxFlipsToTopLeftOriginAndClamps() {
        let flipped = OCRBox.fromVisionBoundingBox(x: 0.1, y: 0.7, width: 0.2, height: 0.3)
        XCTAssertEqual(flipped.x, 0.1, accuracy: 1e-9)
        XCTAssertEqual(flipped.y, 0, accuracy: 1e-9)
        XCTAssertEqual(flipped.width, 0.2, accuracy: 1e-9)
        XCTAssertEqual(flipped.height, 0.3, accuracy: 1e-9)
        XCTAssertEqual(
            OCRBox.fromVisionBoundingBox(x: 0, y: 0, width: 0.5, height: 0.2).y,
            0.8,
            accuracy: 1e-9
        )
        let clamped = OCRBox.fromVisionBoundingBox(x: -1, y: 2, width: 3, height: .nan)
        XCTAssertEqual(clamped.x, 0)
        XCTAssertEqual(clamped.y, 0)
        XCTAssertEqual(clamped.width, 1)
        XCTAssertEqual(clamped.height, 0)
    }
    func testJSONMatchesBackendLineContract() throws {
        let document = OCRDocument(
            lines: [line("TOTAL 12.50", 0.875, x: 0.25, y: 0.5)],
            pageCount: 2,
            truncated: true
        )
        let data = try JSONEncoder().encode(document.asJSONValue())
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["engine"] as? String, "vision")
        XCTAssertEqual(object["level"] as? String, "accurate")
        XCTAssertEqual(object["pages"] as? Int, 2)
        XCTAssertEqual(object["truncated"] as? Bool, true)
        let lines = try XCTUnwrap(object["lines"] as? [[String: Any]])
        XCTAssertEqual(lines.count, 1)
        XCTAssertEqual(lines[0]["text"] as? String, "TOTAL 12.50")
        XCTAssertEqual((lines[0]["confidence"] as? NSNumber)?.doubleValue, 0.875)
        XCTAssertEqual(lines[0]["page"] as? Int, 0)
        let box = try XCTUnwrap(lines[0]["box"] as? [String: Any])
        XCTAssertEqual(Set(box.keys), ["x", "y", "w", "h"])
        XCTAssertEqual((box["x"] as? NSNumber)?.doubleValue, 0.25)
        XCTAssertEqual((box["y"] as? NSNumber)?.doubleValue, 0.5)
    }
    func testEmptyDocumentKeepsEmptyLines() {
        let document = OCRDocument(lines: [line(" ")], pageCount: 0, truncated: false)
        XCTAssertTrue(document.lines.isEmpty)
        XCTAssertEqual(document.pageCount, 0)
        XCTAssertEqual(
            document.asJSONValue(),
            OCRDocument(lines: [], pageCount: 0, truncated: false).asJSONValue()
        )
    }
    /// 直接构造的框（非 Vision 来源）也必须满足 0…1，且结果可被 JSON 编码。
    func testDirectlyConstructedBoxClampsOutOfRangeAndNonFiniteValues() throws {
        let document = OCRDocument(
            lines: [
                OCRLine(
                    text: "TOTAL",
                    confidence: .infinity,
                    box: OCRBox(x: .nan, y: -1, width: 2, height: .infinity),
                    page: -3
                ),
            ],
            pageCount: 1,
            truncated: false
        )
        let item = try XCTUnwrap(document.lines.first)
        XCTAssertEqual([item.box.x, item.box.y, item.box.width, item.box.height], [0, 0, 1, 0])
        XCTAssertEqual(item.confidence, 0)
        XCTAssertEqual(item.page, 0)
        let data = try JSONEncoder().encode(document.asJSONValue())
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual((object["lines"] as? [[String: Any]])?.count, 1)
    }
}
