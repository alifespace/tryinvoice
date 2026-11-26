import Foundation
import SwiftData
import XCTest
@testable import TryInvoice

final class LocalStoreTests: XCTestCase {
    private var root: URL!
    private var vaultRoot: URL!
    private var storeURL: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("LocalStoreTests-\(UUID().uuidString)")
        vaultRoot = root.appendingPathComponent("RawVault")
        storeURL = root.appendingPathComponent("store.sqlite")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    @MainActor
    private func makeStore(
        at url: URL? = nil,
        saveOperation: LocalStore.SaveOperation? = nil
    ) throws -> LocalStore {
        LocalStore(
            container: try LocalStore.makeContainer(storeURL: url ?? storeURL),
            vault: try RawVault(root: vaultRoot), saveOperation: saveOperation
        )
    }

    @MainActor
    private func makeBatch(_ store: LocalStore) throws -> LocalBatch {
        try store.createBatch(
            title: "Synthetic trip", expenseType: "meal",
            templateId: UUID(), batchDate: "2026-11-22"
        )
    }

    @MainActor
    private func makeDocument(
        in store: LocalStore,
        batch: LocalBatch,
        state: UploadState = .pending,
        attempt: Int = 0,
        createdAt: Date = .now
    ) -> LocalDocument {
        let document = LocalDocument(
            relativePath: UUID().uuidString, sha256: String(repeating: "a", count: 64),
            sizeBytes: 9, mimeType: "image/jpeg", originalName: "synthetic.jpg",
            state: state, attempt: attempt, createdAt: createdAt
        )
        document.batch = batch
        store.container.mainContext.insert(document)
        return document
    }

    private func writeFile(_ name: String) throws -> URL {
        let url = root.appendingPathComponent(name)
        try Data("synthetic".utf8).write(to: url)
        return url
    }

    @MainActor
    func testOCRJSONPersistsAndDefaultsToNil() throws {
        do {
            let store = try makeStore()
            let batch = try makeBatch(store)
            let withOCR = try store.addDocument(
                to: batch, fileAt: try writeFile("ocr.jpg"), originalName: "ocr.jpg",
                ocrJSON: "{\"lines\":[]}"
            )
            XCTAssertEqual(withOCR.ocrJSON, "{\"lines\":[]}")
            let plain = try store.addDocument(
                to: batch, fileAt: try writeFile("plain.jpg"), originalName: "plain.jpg"
            )
            XCTAssertNil(plain.ocrJSON)
        }

        let reopened = try makeStore()
        let documents = try reopened.container.mainContext.fetch(FetchDescriptor<LocalDocument>())
        XCTAssertEqual(documents.count, 2)
        XCTAssertEqual(documents.filter { $0.ocrJSON != nil }.compactMap(\.ocrJSON), ["{\"lines\":[]}"])
    }

    @MainActor
    func testOfflineBatchAndDocumentsSurviveContainerRecreation() throws {
        var batchId: UUID!
        var documentIds: Set<UUID> = []
        do {
            let store = try makeStore()
            let batch = try makeBatch(store)
            batchId = batch.id
            let jpg = try store.addDocument(
                to: batch, fileAt: try writeFile("receipt.jpg"), originalName: "receipt.jpg"
            )
            let pdf = try store.addDocument(
                to: batch, fileAt: try writeFile("invoice.pdf"), originalName: "invoice.pdf"
            )
            XCTAssertEqual(jpg.mimeType, "image/jpeg")
            XCTAssertEqual(pdf.mimeType, "application/pdf")
            documentIds = [jpg.id, pdf.id]
        }

        let reopened = try makeStore()
        let restored = try XCTUnwrap(
            try reopened.container.mainContext.fetch(FetchDescriptor<LocalBatch>()).first
        )
        XCTAssertEqual(restored.id, batchId)
        XCTAssertEqual(Set(restored.documents.map(\.id)), documentIds)
        for document in restored.documents {
            XCTAssertEqual(document.state, .pending)
            XCTAssertEqual(
                try reopened.vault.read(document.relativePath), Data("synthetic".utf8)
            )
        }
    }

    @MainActor
    func testUploadCandidatesFilterTerminalStatesAndSortByCreation() throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let baseline = Date(timeIntervalSince1970: 1_000)
        let retryable = makeDocument(
            in: store, batch: batch, state: .retryableFailed, attempt: 1, createdAt: baseline
        )
        let pending = makeDocument(
            in: store, batch: batch, createdAt: baseline.addingTimeInterval(1)
        )
        for (state, offset) in [
            (UploadState.uploaded, 2.0), (.uploading, 3.0), (.permanentFailed, 4.0),
        ] {
            _ = makeDocument(
                in: store, batch: batch, state: state,
                createdAt: baseline.addingTimeInterval(offset)
            )
        }
        try store.container.mainContext.save()

        XCTAssertEqual(store.uploadCandidates().map(\.id), [retryable.id, pending.id])
    }

    @MainActor
    func testRecoverOnLaunchOnlyResetsInterruptedUploads() throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let uploading = makeDocument(in: store, batch: batch, state: .uploading)
        let uploaded = makeDocument(in: store, batch: batch, state: .uploaded)
        let pending = makeDocument(in: store, batch: batch)
        try store.container.mainContext.save()

        XCTAssertEqual(try store.recoverOnLaunch(), 1)
        XCTAssertEqual(uploading.state, .pending)
        XCTAssertEqual(uploaded.state, .uploaded)
        XCTAssertEqual(pending.state, .pending)
    }

    @MainActor
    func testApplyRetriesThenStopsAtPermanentFailure() throws {
        let store = try makeStore()
        let document = makeDocument(in: store, batch: try makeBatch(store))
        try store.container.mainContext.save()

        try store.apply(.start, to: document)
        XCTAssertEqual(document.state, .uploading)
        try store.apply(.failed(.retryable), to: document, maxAttempts: 2)
        XCTAssertEqual(document.state, .retryableFailed)
        XCTAssertEqual(document.attempt, 1)
        try store.apply(.start, to: document)
        try store.apply(.failed(.retryable), to: document, maxAttempts: 2)
        XCTAssertEqual(document.state, .permanentFailed)
        XCTAssertEqual(document.attempt, 2)
        try store.apply(.start, to: document)
        XCTAssertEqual(document.state, .permanentFailed)
    }

    @MainActor
    func testBackfillWritesOnlyOnceAndRejectsConflictingIds() throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let document = makeDocument(in: store, batch: batch)
        try store.container.mainContext.save()
        let serverId = UUID()
        let documentId = UUID()

        try store.backfill(serverId: serverId, for: batch)
        try store.backfill(serverId: serverId, for: batch)
        try store.backfill(serverDocumentId: documentId, for: document)
        try store.backfill(serverDocumentId: documentId, for: document)
        XCTAssertEqual(batch.serverId, serverId)
        XCTAssertEqual(document.serverDocumentId, documentId)
        XCTAssertThrowsError(try store.backfill(serverId: UUID(), for: batch)) {
            XCTAssertEqual($0 as? LocalStoreError, .conflictingServerId)
        }
        XCTAssertThrowsError(try store.backfill(serverDocumentId: UUID(), for: document)) {
            XCTAssertEqual($0 as? LocalStoreError, .conflictingServerId)
        }
    }

    @MainActor
    func testSaveFailureRollsBackMetadataAndKeepsStoredFile() throws {
        var saveCount = 0
        let store = try makeStore(saveOperation: { context in
            saveCount += 1
            guard saveCount > 1 else { return try context.save() }
            throw URLError(.cannotWriteToFile)
        })
        let batch = try makeBatch(store)

        XCTAssertThrowsError(
            try store.addDocument(
                to: batch, fileAt: try writeFile("receipt.jpg"),
                originalName: "receipt.jpg"
            )
        ) { XCTAssertEqual($0 as? URLError, URLError(.cannotWriteToFile)) }
        XCTAssertTrue(try store.container.mainContext.fetch(FetchDescriptor<LocalDocument>()).isEmpty)
        XCTAssertEqual(
            try FileManager.default.contentsOfDirectory(
                atPath: vaultRoot.appendingPathComponent(batch.id.uuidString).path
            ).count,
            1
        )
    }

    /// 复核页只读接口：仅返回已回填服务端 ID 的批次，并按创建时间倒序；文档按服务端 ID 查路径。
    @MainActor
    func testSyncedBatchesAndDocumentPathLookupsAreReadOnly() throws {
        let store = try makeStore()
        let older = LocalBatch(
            serverId: UUID(uuidString: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            title: "Synthetic older", expenseType: "meal", templateId: UUID(),
            batchDate: "2026-11-20", createdAt: Date(timeIntervalSince1970: 100)
        )
        let newer = LocalBatch(
            serverId: UUID(uuidString: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            title: "Synthetic newer", expenseType: "meal", templateId: UUID(),
            batchDate: "2026-11-21", createdAt: Date(timeIntervalSince1970: 200)
        )
        let localOnly = LocalBatch(
            title: "Synthetic local", expenseType: "meal", templateId: UUID(),
            batchDate: "2026-11-22", createdAt: Date(timeIntervalSince1970: 300)
        )
        for batch in [older, newer, localOnly] {
            store.container.mainContext.insert(batch)
        }

        let document = LocalDocument(
            serverDocumentId: UUID(uuidString: "cccccccc-cccc-cccc-cccc-cccccccccccc"),
            relativePath: "batch/doc.jpg", sha256: String(repeating: "a", count: 64),
            sizeBytes: 9, mimeType: "image/jpeg", originalName: "synthetic.jpg"
        )
        document.batch = newer
        store.container.mainContext.insert(document)
        try store.container.mainContext.save()

        XCTAssertEqual(store.syncedBatches().map(\.title), ["Synthetic newer", "Synthetic older"])
        XCTAssertEqual(
            store.relativePath(forServerDocumentId: UUID(uuidString: "cccccccc-cccc-cccc-cccc-cccccccccccc")!),
            "batch/doc.jpg"
        )
        XCTAssertNil(store.relativePath(forServerDocumentId: UUID()))
    }
}
