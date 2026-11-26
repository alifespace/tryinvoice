import Foundation
import SwiftData
import XCTest
@testable import TryInvoice

final class ImportPipelineTests: XCTestCase {
    private var root: URL!
    private var storeCount = 0

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ImportPipelineTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    @MainActor
    private func makeStore(saveOperation: LocalStore.SaveOperation? = nil) throws -> LocalStore {
        storeCount += 1
        return LocalStore(
            container: try LocalStore.makeContainer(storeURL: nil),
            vault: try RawVault(root: root.appendingPathComponent("vault-\(storeCount)")),
            saveOperation: saveOperation
        )
    }

    @MainActor
    private func makeBatch(_ store: LocalStore) throws -> LocalBatch {
        try store.createBatch(
            title: "Synthetic trip", expenseType: "meal",
            templateId: UUID(), batchDate: "2026-11-22"
        )
    }

    private func write(_ data: Data, _ name: String) throws -> ImportItem {
        let directory = root.appendingPathComponent("in-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(name)
        try data.write(to: url)
        return ImportItem(fileURL: url, originalName: name)
    }

    @MainActor
    func testImportAllStoresFiveImagesAndPDFWithOCRJSON() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let engine = FakeOCREngine()
        let pipeline = ImportPipeline(store: store, engine: engine)
        var items = try (0..<5).map {
            try write(try SyntheticFixtures.pngData(variant: $0), "receipt-\($0).png")
        }
        items.append(try write(SyntheticFixtures.pdfData(), "invoice.pdf"))

        let report = await pipeline.importAll(items, into: batch)

        XCTAssertEqual(report.failures.count, 0)
        XCTAssertEqual(report.imported.count, 6)
        XCTAssertEqual(store.uploadCandidates().count, 6)
        for document in report.imported {
            XCTAssertEqual(document.state, .pending)
            XCTAssertTrue(try store.vault.exists(document.relativePath))
            let raw = try XCTUnwrap(document.ocrJSON?.data(using: .utf8))
            guard case let .object(fields) = try JSONDecoder().decode(JSONValue.self, from: raw) else {
                return XCTFail("ocrJSON 必须是对象")
            }
            XCTAssertEqual(fields["engine"], .string("vision"))
            guard case .array(let lines)? = fields["lines"] else { return XCTFail("缺少 lines") }
            XCTAssertEqual(lines.count, 1)
        }
        XCTAssertEqual(engine.recognizedKinds, [ValidatedFile.Kind](repeating: .image, count: 5) + [.pdf])
    }

    @MainActor
    func testRealVisionEngineImportsSyntheticPNG() async throws {
        let store = try makeStore()
        let pipeline = ImportPipeline(store: store, engine: VisionOCREngine())
        let document = try await pipeline.importFile(
            try write(try SyntheticFixtures.pngData(), "vision.png"), into: try makeBatch(store)
        )
        XCTAssertEqual(document.state, .pending)
        XCTAssertNotNil(document.ocrJSON)
        XCTAssertTrue(try store.vault.exists(document.relativePath))
    }

    @MainActor
    func testFailuresLeaveNoRecordsAndRestOfBatchSucceeds() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let engine = FakeOCREngine(failingNames: ["ocr-fail.png"])
        let goodData = try SyntheticFixtures.pngData(variant: 1)
        let ocrData = try SyntheticFixtures.pngData(variant: 2)
        let limit = max(goodData.count, ocrData.count) + 64
        let pipeline = ImportPipeline(
            store: store, validator: ImportValidator(maxBytes: limit), engine: engine
        )
        let items = [
            try write(Data(repeating: 0xAB, count: limit + 1), "large.png"),
            try write(Data("%PDF-1.7 garbage".utf8), "broken.pdf"),
            try write(ocrData, "ocr-fail.png"),
            try write(goodData, "good.png"),
            try write(goodData, "duplicate.png"),
        ]

        let report = await pipeline.importAll(items, into: batch)

        XCTAssertEqual(report.imported.map(\.originalName), ["good.png"])
        XCTAssertEqual(report.failures.map { $0.index }, [0, 1, 2, 4])
        XCTAssertEqual(
            report.failures.map { $0.error },
            [.fileTooLarge, .corruptFile, .ocrFailed, .duplicate]
        )
        XCTAssertEqual(engine.recognizedNames, ["ocr-fail.png", "good.png"])
        let stored = try store.container.mainContext.fetch(FetchDescriptor<LocalDocument>())
        XCTAssertEqual(stored.count, 1)
        let vaultDirectory = try store.vault.url(forRelativePath: batch.id.uuidString)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: vaultDirectory.path).count, 1)
        // 失败项的导入占用已释放：同一内容重试仍能进入 OCR 并落库。
        let retried = try await pipeline.importFile(items[2], into: batch)
        XCTAssertEqual(retried.originalName, "ocr-fail.png")
        XCTAssertEqual(store.uploadCandidates().count, 2)
    }

    @MainActor
    func testSaveFailureLeavesNoDocument() async throws {
        var saveCount = 0
        let store = try makeStore(saveOperation: { context in
            saveCount += 1
            guard saveCount > 1 else { return try context.save() }
            throw URLError(.cannotWriteToFile)
        })
        let pipeline = ImportPipeline(store: store, engine: FakeOCREngine())
        let item = try write(try SyntheticFixtures.pngData(), "receipt.png")

        do {
            _ = try await pipeline.importFile(item, into: try makeBatch(store))
            XCTFail("保存失败必须抛出 ImportError")
        } catch let error as ImportError {
            XCTAssertEqual(error, .corruptFile)
        }
        XCTAssertTrue(try store.container.mainContext.fetch(FetchDescriptor<LocalDocument>()).isEmpty)
    }

    @MainActor
    func testCancelledTaskStopsRemainingItems() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let engine = FakeOCREngine(cancelOnCall: 1)
        let pipeline = ImportPipeline(store: store, engine: engine)
        let items = try (0..<3).map {
            try write(try SyntheticFixtures.pngData(variant: $0), "cancel-\($0).png")
        }
        let report = await Task { @MainActor in
            await pipeline.importAll(items, into: batch)
        }.value

        XCTAssertEqual(report.imported.count, 1)
        XCTAssertEqual(report.failures.map { $0.index }, [1, 2])
        XCTAssertEqual(report.failures.map { $0.error }, [.cancelled, .cancelled])
        XCTAssertEqual(engine.recognizedNames.count, 1)
        XCTAssertEqual(try store.container.mainContext.fetch(FetchDescriptor<LocalDocument>()).count, 1)
    }

    /// 同一批次、相同字节的两个导入重叠执行：第二个必须在 OCR 前被拒为重复。
    @MainActor
    func testOverlappingDuplicateImportsRunOCROnceAndStoreOneDocument() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let engine = FakeOCREngine()
        let pipeline = ImportPipeline(store: store, engine: engine)
        let data = try SyntheticFixtures.pngData()
        var entered: CheckedContinuation<Void, Never>?
        var gates: [CheckedContinuation<Void, Never>] = []
        engine.beforeStoreOCR = {
            entered?.resume()
            entered = nil
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                gates.append(continuation)
                if gates.count > 1 {   // 去重回归时两个请求都停在 OCR，全部放行以暴露失败而非挂死
                    gates.forEach { $0.resume() }
                    gates = []
                }
            }
        }

        let firstTask = Task { @MainActor in
            try await pipeline.importFile(try self.write(data, "first.png"), into: batch)
        }
        await withCheckedContinuation { entered = $0 }
        do {
            _ = try await pipeline.importFile(try write(data, "second.png"), into: batch)
            XCTFail("同批同哈希的重叠导入必须在 OCR 前报重复")
        } catch { XCTAssertEqual(error as? ImportError, .duplicate) }
        XCTAssertEqual(engine.recognizedNames, ["first.png"])

        gates.forEach { $0.resume() }
        _ = try await firstTask.value
        XCTAssertEqual(store.uploadCandidates().count, 1)

        // 落库后同内容再次导入仍由批次记录拒重，且不进入 OCR。
        do {
            _ = try await pipeline.importFile(try write(data, "third.png"), into: batch)
            XCTFail("同批已落库内容必须继续拒重")
        } catch { XCTAssertEqual(error as? ImportError, .duplicate) }
        XCTAssertEqual(engine.recognizedNames, ["first.png"])
        XCTAssertEqual(store.uploadCandidates().count, 1)
    }
}

private final class FakeOCREngine: OCREngine {
    private var failingNames: Set<String>
    private let cancelOnCall: Int?
    private(set) var recognizedNames: [String] = []
    private(set) var recognizedKinds: [ValidatedFile.Kind] = []
    /// 测试注入的挂起钩子：在识别结果返回前执行，用于制造跨 `await` 的重叠导入。
    var beforeStoreOCR: (() async -> Void)?

    init(failingNames: Set<String> = [], cancelOnCall: Int? = nil) {
        self.failingNames = failingNames
        self.cancelOnCall = cancelOnCall
    }

    func recognize(fileAt url: URL, kind: ValidatedFile.Kind) async throws -> OCRDocument {
        recognizedNames.append(url.lastPathComponent)
        recognizedKinds.append(kind)
        if let cancelOnCall, recognizedNames.count == cancelOnCall {
            withUnsafeCurrentTask { $0?.cancel() }
        }
        if let beforeStoreOCR { await beforeStoreOCR() }
        if failingNames.remove(url.lastPathComponent) != nil { throw ImportError.ocrFailed }
        return OCRDocument(
            lines: [OCRLine(
                text: SyntheticFixtures.totalLine, confidence: 0.9,
                box: OCRBox(x: 0.1, y: 0.2, width: 0.5, height: 0.1), page: 0
            )],
            pageCount: 1, truncated: false
        )
    }
}
