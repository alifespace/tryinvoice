import Foundation
import XCTest
@testable import TryInvoice

final class ImportValidatorTests: XCTestCase {
    private var fixtures: FixtureDirectory!
    private let validator = ImportValidator()

    override func setUpWithError() throws { fixtures = try FixtureDirectory() }
    override func tearDownWithError() throws { fixtures.remove(); fixtures = nil }

    func testValidImageAndPDFPassWithDeclaredKindAndSize() throws {
        let cases: [(String, String, Data, ValidatedFile.Kind)] = [
            ("receipt.png", "Receipt.PNG", try SyntheticFixtures.pngData(), .image),
            ("a.jpeg", "a.jpeg", try SyntheticFixtures.jpegData(), .image),
            ("batch.pdf", "batch.pdf", SyntheticFixtures.pdfData(), .pdf),
        ]
        for (file, original, data, kind) in cases {
            let url = try fixtures.write(data, named: file)
            XCTAssertEqual(
                try validator.validate(fileAt: url, originalName: original),
                ValidatedFile(
                    kind: kind,
                    ext: (original as NSString).pathExtension.lowercased(),
                    sizeBytes: data.count
                )
            )
        }
    }

    func testUnsupportedExtensionIsRejected() throws {
        let url = try fixtures.write(try SyntheticFixtures.pngData(), named: "receipt.gif")
        expect(.unsupportedType) { _ = try self.validator.validate(fileAt: url, originalName: "receipt.gif") }
    }

    func testEmptyFileIsRejectedAsTooLarge() throws {
        let url = try fixtures.write(Data(), named: "empty.png")
        expect(.fileTooLarge) { _ = try self.validator.validate(fileAt: url, originalName: "empty.png") }
    }

    func testFileOverInjectedLimitIsRejected() throws {
        let url = try fixtures.write(try SyntheticFixtures.pngData(), named: "big.png")
        expect(.fileTooLarge) {
            _ = try ImportValidator(maxBytes: 32).validate(fileAt: url, originalName: "big.png")
        }
    }

    func testExtensionAndMagicMismatchIsCorrupt() throws {
        let url = try fixtures.write(try SyntheticFixtures.pngData(), named: "fake.jpg")
        expect(.corruptFile) { _ = try self.validator.validate(fileAt: url, originalName: "fake.jpg") }
    }

    func testTruncatedPDFIsCorrupt() throws {
        let url = try fixtures.write(Data("%PDF-1.7 synthetic truncated".utf8), named: "broken.pdf")
        expect(.corruptFile) { _ = try self.validator.validate(fileAt: url, originalName: "broken.pdf") }
    }

    func testValidHeicImagePasses() throws {
        let data = try SyntheticFixtures.heicData()
        let url = try fixtures.write(data, named: "photo.heic")
        XCTAssertEqual(
            try validator.validate(fileAt: url, originalName: "photo.HEIC"),
            ValidatedFile(kind: .image, ext: "heic", sizeBytes: data.count)
        )
    }

    func testEncryptedPDFIsCorrupt() throws {
        let url = try fixtures.write(try SyntheticFixtures.encryptedPDFData(), named: "locked.pdf")
        expect(.corruptFile) { _ = try self.validator.validate(fileAt: url, originalName: "locked.pdf") }
    }

    /// 魔数正确但首帧解不出的文件（只保留文件头）必须判损坏，而不是按声明帧数放行。
    func testTruncatedImagesWithValidMagicAreCorrupt() throws {
        let cases: [(String, Data)] = [
            ("broken.png", Data(try SyntheticFixtures.pngData().prefix(12))),
            ("broken.jpg", Data(try SyntheticFixtures.jpegData().prefix(12))),
        ]
        for (name, data) in cases {
            let url = try fixtures.write(data, named: name)
            expect(.corruptFile) { _ = try self.validator.validate(fileAt: url, originalName: name) }
        }
    }

    /// 保留部分扫描数据的 JPEG：ImageIO 可返回非空部分帧，容器状态仍为 complete，
    /// 但首帧实际状态是 unexpectedEOF，必须判损坏。
    func testPartiallyTruncatedJPEGWithScanDataIsCorrupt() throws {
        let data = try SyntheticFixtures.truncatedJPEGData(keepingFraction: 0.25)
        let url = try fixtures.write(data, named: "partial.jpg")
        expect(.corruptFile) { _ = try self.validator.validate(fileAt: url, originalName: "partial.jpg") }
    }

    func testPDFWithZeroPagesIsCorrupt() throws {
        let url = try fixtures.write(makeZeroPagePDF(), named: "zero.pdf")
        expect(.corruptFile) { _ = try self.validator.validate(fileAt: url, originalName: "zero.pdf") }
    }

    func testSymbolicLinkSourceIsFollowedWithoutCrashing() throws {
        let target = try fixtures.write(try SyntheticFixtures.pngData(), named: "target.png")
        let link = fixtures.url.appendingPathComponent("link.png")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        XCTAssertEqual(try validator.validate(fileAt: link, originalName: "link.png").kind, .image)
    }

    /// 结构合法但页数为 0 的 PDF 字节，用于验证“零页不可导入”。
    private func makeZeroPagePDF() -> Data {
        var data = Data("%PDF-1.4\n".utf8)
        let bodies = ["<</Type/Catalog/Pages 2 0 R>>", "<</Type/Pages/Kids[]/Count 0>>"]
        var offsets: [Int] = []
        for (index, body) in bodies.enumerated() {
            offsets.append(data.count)
            data.append(Data("\(index + 1) 0 obj\n\(body)\nendobj\n".utf8))
        }
        let xrefOffset = data.count
        var xref = "xref\n0 \(bodies.count + 1)\n0000000000 65535 f \n"
        offsets.forEach { xref += String(format: "%010d 00000 n \n", $0) }
        let trailer = "trailer\n<</Size \(bodies.count + 1)/Root 1 0 R>>\nstartxref\n\(xrefOffset)\n%%EOF\n"
        data.append(Data((xref + trailer).utf8))
        return data
    }

    private func expect(_ expected: ImportError, _ body: () throws -> Void) {
        XCTAssertThrowsError(try body()) { XCTAssertEqual($0 as? ImportError, expected) }
    }
}
