import Foundation
import SwiftData
import XCTest
@testable import TryInvoice

final class CaptureViewModelTests: XCTestCase {
    private var root: URL!
    private var store: LocalStore!
    private var viewModel: CaptureViewModel!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("CaptureViewModelTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    @MainActor
    private func makeViewModel() throws {
        let localStore = LocalStore(
            container: try LocalStore.makeContainer(storeURL: nil),
            vault: try RawVault(root: root.appendingPathComponent("vault"))
        )
        store = localStore
        viewModel = CaptureViewModel(
            pipeline: ImportPipeline(store: localStore, engine: StubOCREngine()),
            store: localStore
        )
    }

    @MainActor
    private func batchCount() throws -> Int {
        try store.container.mainContext.fetchCount(FetchDescriptor<LocalBatch>())
    }

    @MainActor
    func testSuccessImportCleansTemporaryDirectoryAndUsesOneBatch() async throws {
        try makeViewModel()
        let photos = try (0..<5).map { try SyntheticFixtures.jpegData(variant: $0) }
        let source = CaptureSources.fromPhotos(photos + [SyntheticFixtures.pdfData()], names: [])
        let directory = try XCTUnwrap(source.directories.first)
        await viewModel.importItems(source.items)

        guard case let .finished(summary) = viewModel.state else { return XCTFail("expected finished") }
        XCTAssertEqual(summary.imported, 6)
        XCTAssertTrue(summary.failures.isEmpty)
        XCTAssertTrue(try XCTUnwrap(viewModel.feedback).contains("6"))
        XCTAssertEqual(try batchCount(), 1)
        XCTAssertEqual(store.uploadCandidates().count, 6)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }

    @MainActor
    func testFailuresAreCountedByTypeAndDraftBatchIsReused() async throws {
        try makeViewModel()
        let duplicate = try SyntheticFixtures.jpegData()
        let source = CaptureSources.fromPhotos([duplicate, duplicate], names: ["a", "b"])
        let broken = try SyntheticFixtures.truncatedJPEGData(keepingFraction: 0.25)
        let brokenURL = source.directories[0].appendingPathComponent("broken.jpg")
        try broken.write(to: brokenURL)
        let items = source.items + [ImportItem(fileURL: brokenURL, originalName: "broken.jpg")]
        await viewModel.importItems(items)

        guard case let .finished(first) = viewModel.state else { return XCTFail("expected finished") }
        XCTAssertEqual(first.imported, 1)
        XCTAssertEqual(first.failures.filter { $0 == .duplicate }.count, 1)
        XCTAssertEqual(first.failures.filter { $0 == .corruptFile }.count, 1)
        let feedback = try XCTUnwrap(viewModel.feedback)
        XCTAssertTrue(feedback.contains("该文件已导入过") && feedback.contains("文件已损坏"))

        let more = CaptureSources.fromPhotos([try SyntheticFixtures.jpegData(variant: 3)], names: [])
        await viewModel.importItems(more.items)
        guard case let .finished(second) = viewModel.state else { return XCTFail("expected finished") }
        XCTAssertEqual(second.imported, 1)
        XCTAssertEqual(try batchCount(), 1)
    }

    @MainActor
    func testFeedbackReportsReplaceScanNoticeWithoutRecords() async throws {
        try makeViewModel()
        viewModel.reportPermissionDenied()
        XCTAssertEqual(viewModel.state, .finished(.init(imported: 0, failures: [.permissionDenied])))
        XCTAssertEqual(try batchCount(), 0)
        viewModel.reportScanUnsupported()
        XCTAssertEqual(viewModel.state, .idle)
        XCTAssertEqual(viewModel.feedback, "此设备不支持扫描。")
        await viewModel.importItems(CaptureSources.fromPhotos([try SyntheticFixtures.jpegData(variant: 21)], names: []).items)
        XCTAssertEqual(viewModel.feedback, "成功导入 1 份。")
        XCTAssertEqual(try batchCount(), 1)
        viewModel.reportReadFailure()
        XCTAssertTrue(try XCTUnwrap(viewModel.feedback).contains("损坏"))
        viewModel.reportCancelled()
        XCTAssertTrue(try XCTUnwrap(viewModel.feedback).contains("取消"))
        XCTAssertEqual(try batchCount(), 1)
        XCTAssertEqual(store.uploadCandidates().count, 1)
    }

    @MainActor
    func testForeignPrefixedDirectorySurvivesAndOwnedIsCleaned() async throws {
        try makeViewModel()
        let foreign = FileManager.default.temporaryDirectory.appendingPathComponent("\(CaptureSources.directoryPrefix)f-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: foreign, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: foreign) }
        let file = foreign.appendingPathComponent("f.jpg")
        try SyntheticFixtures.jpegData(variant: 11).write(to: file)
        let owned = CaptureSources.fromPhotos([try SyntheticFixtures.jpegData(variant: 12)], names: [])
        await viewModel.importItems(owned.items + [ImportItem(fileURL: file, originalName: "f.jpg")])
        XCTAssertTrue(FileManager.default.fileExists(atPath: foreign.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: try XCTUnwrap(owned.directories.first).path))
    }

    /// loader 用 CheckedContinuation 挂起，期间重入其它入口必须被忽略且清理其临时目录。
    @MainActor
    func testConcurrentEntriesIgnoredWhilePhotosLoad() async throws {
        try makeViewModel()
        let model = try XCTUnwrap(viewModel)
        let ignored = CaptureSources.fromPhotos([try SyntheticFixtures.jpegData(variant: 32)], names: [])
        let ignoredItems = ignored.items
        let loader: () async -> Data? = {
            await withCheckedContinuation { continuation in
                Task { @MainActor in
                    XCTAssertEqual(model.state, .importing)
                    model.reportCancelled(); model.reportReadFailure()
                    model.reportPermissionDenied(); model.reportScanUnsupported()
                    XCTAssertEqual(model.state, .importing); XCTAssertNil(model.feedback)
                    XCTAssertEqual((try? self.batchCount()) ?? -1, 0)
                    XCTAssertTrue(self.store.uploadCandidates().isEmpty)
                    await model.importItems(ignoredItems); await model.importFileURLs([])
                    XCTAssertEqual(model.state, .importing)
                    continuation.resume()
                }
            }
            return try? SyntheticFixtures.jpegData(variant: 31)
        }
        await model.importPhotos(loaders: [loader])
        XCTAssertFalse(FileManager.default.fileExists(atPath: try XCTUnwrap(ignored.directories.first).path))
        XCTAssertEqual(store.uploadCandidates().count, 1)
        await model.importItems(CaptureSources.fromPhotos([try SyntheticFixtures.jpegData(variant: 33)], names: []).items)
        XCTAssertEqual(model.feedback, "成功导入 1 份。")
    }
}

private struct StubOCREngine: OCREngine {
    func recognize(fileAt url: URL, kind: ValidatedFile.Kind) async throws -> OCRDocument {
        OCRDocument(lines: [OCRLine(
            text: SyntheticFixtures.totalLine, confidence: 0.9,
            box: OCRBox(x: 0, y: 0, width: 1, height: 0.1), page: 0
        )], pageCount: 1, truncated: false)
    }
}
