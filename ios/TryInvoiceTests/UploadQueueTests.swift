import Foundation
import XCTest
@testable import TryInvoice

final class UploadQueueTests: XCTestCase {
    private var root: URL!
    private var storeCount = 0

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("UploadQueueTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    @MainActor
    func testRunWaitsForConnectivityThenUploadsSerially() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let first = try addDocument(store, batch, "first.jpg", "first-bytes")
        let second = try addDocument(store, batch, "second.pdf", "second-bytes")
        let firstId = UUID()
        let secondId = UUID()
        let api = FakeUploadAPI(replies: [
            .ready(documentId: firstId.uuidString, uploadURL: Self.objectURL),
            .ready(documentId: secondId.uuidString, uploadURL: Self.objectURL),
        ])
        let monitor = FakeConnectivityMonitor()
        let queue = UploadQueue(store: store, api: api)
        let running = Task { await queue.run(connectivity: monitor) }

        monitor.send(false)
        await settle()
        let offlineCalls = await api.calls
        XCTAssertEqual(offlineCalls, [])
        XCTAssertEqual(first.state, .pending)

        monitor.send(true)
        try await waitUntil { first.state == .uploaded && second.state == .uploaded }
        monitor.finish()
        await running.value

        let calls = await api.calls
        let uploadedBytes = await api.uploadedBytes
        XCTAssertEqual(calls, ["createBatch", "presign", "upload", "confirm", "presign", "upload", "confirm"])
        XCTAssertEqual(batch.serverId, FakeUploadAPI.batchId)
        XCTAssertEqual(first.serverDocumentId, firstId)
        XCTAssertEqual(second.serverDocumentId, secondId)
        XCTAssertEqual(uploadedBytes, [Data("first-bytes".utf8), Data("second-bytes".utf8)])
        XCTAssertEqual(try store.vault.read(first.relativePath), Data("first-bytes".utf8))
        XCTAssertEqual(try store.vault.read(second.relativePath), Data("second-bytes".utf8))
        let stored = try store.vault.url(forRelativePath: first.relativePath)
        XCTAssertEqual(first.sha256, try FileHasher.sha256(of: stored))
    }

    @MainActor
    func testOfflineSignalStopsNewStepsAndResumesOnReconnect() async throws {
        let store = try makeStore()
        let document = try addDocument(store, try makeBatch(store), "pause.jpg", "pause")
        let gate = PresignGate()
        let serverDocumentId = UUID()
        let api = FakeUploadAPI(replies: [
            .gated(documentId: serverDocumentId.uuidString, uploadURL: Self.objectURL, gate: gate),
            .ready(documentId: serverDocumentId.uuidString, uploadURL: Self.objectURL),
        ])
        let monitor = FakeConnectivityMonitor()
        let queue = UploadQueue(store: store, api: api)
        let running = Task { await queue.run(connectivity: monitor) }

        monitor.send(true)
        try await waitUntil { await api.calls.contains("presign") }
        monitor.send(false)
        try await waitUntil { !queue.isOnline }
        await gate.open()
        try await waitUntil { document.state == .pending }

        let offlineCalls = await api.calls
        XCTAssertEqual(offlineCalls, ["createBatch", "presign"])
        XCTAssertEqual(document.state, .pending)

        monitor.send(true)
        try await waitUntil { document.state == .uploaded }
        monitor.finish()
        await running.value
        let calls = await api.calls
        XCTAssertEqual(calls, ["createBatch", "presign", "presign", "upload", "confirm"])
        XCTAssertEqual(document.serverDocumentId, serverDocumentId)
    }

    @MainActor
    func testStoppingRunBlocksNewRequestsUntilNextRun() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let first = try addDocument(store, batch, "cancel-first.jpg", "cancel-first")
        let second = try addDocument(store, batch, "cancel-second.jpg", "cancel-second")
        let gate = PresignGate()
        let firstId = UUID()
        let secondId = UUID()
        let api = FakeUploadAPI(replies: [
            .gated(documentId: firstId.uuidString, uploadURL: Self.objectURL, gate: gate),
            .ready(documentId: firstId.uuidString, uploadURL: Self.objectURL),
            .ready(documentId: secondId.uuidString, uploadURL: Self.objectURL),
        ])
        let monitor = FakeConnectivityMonitor()
        let queue = UploadQueue(store: store, api: api)
        let running = Task { await queue.run(connectivity: monitor) }

        monitor.send(true)
        try await waitUntil { await api.calls.contains("presign") }
        running.cancel()
        try await waitUntil { !queue.isRunActive }

        // 旧 presign 仍挂起时启动新运行：新监测流尚未发出任何信号，
        // 因此旧 drain 不得被重新放行，新运行也不得回收其上传中状态。
        let resumed = FakeConnectivityMonitor()
        let rerun = Task { await queue.run(connectivity: resumed) }
        try await waitUntil { queue.isRunActive }
        let overlapCalls = await api.calls
        XCTAssertEqual(overlapCalls, ["createBatch", "presign"])
        let overlapBytes = await api.uploadedBytes
        XCTAssertEqual(overlapBytes, [])

        await gate.open()
        try await waitUntil { first.state == .pending }
        await running.value

        // 门闩：取消后已发出的 presign 自然结束，但不再发 PUT/confirm，也不碰下一份文档。
        let stoppedCalls = await api.calls
        XCTAssertEqual(stoppedCalls, ["createBatch", "presign"])
        XCTAssertEqual(first.state, .pending)
        XCTAssertEqual(second.state, .pending)
        let stoppedBytes = await api.uploadedBytes
        XCTAssertEqual(stoppedBytes, [])

        // 新运行收到首个 true 后继续剩余工作，不重复已完成步骤。
        resumed.send(true)
        try await waitUntil { first.state == .uploaded && second.state == .uploaded }
        resumed.finish()
        await rerun.value
        let calls = await api.calls
        XCTAssertEqual(
            calls,
            ["createBatch", "presign", "presign", "upload", "confirm", "presign", "upload", "confirm"]
        )
        XCTAssertEqual(first.serverDocumentId, firstId)
        XCTAssertEqual(second.serverDocumentId, secondId)
    }

    @MainActor
    func testInstantCopySkipsUploadAndConfirm() async throws {
        let store = try makeStore()
        let document = try addDocument(store, try makeBatch(store), "instant.jpg", "instant")
        let serverDocumentId = UUID()
        let api = FakeUploadAPI(replies: [.instant(documentId: serverDocumentId.uuidString)])

        await UploadQueue(store: store, api: api).drain()

        let calls = await api.calls
        XCTAssertEqual(calls, ["createBatch", "presign"])
        XCTAssertEqual(document.state, .instant)
        XCTAssertEqual(document.serverDocumentId, serverDocumentId)
    }

    @MainActor
    func testNetworkFailureRetriesThenSucceeds() async throws {
        let store = try makeStore()
        let batch = try makeBatch(store)
        let document = try addDocument(store, batch, "retry.jpg", "retry")
        let serverDocumentId = UUID()
        let replies: [FakeUploadAPI.Reply] = [
            .failure(.network),
            .ready(documentId: serverDocumentId.uuidString, uploadURL: Self.objectURL),
        ]
        let queue = UploadQueue(store: store, api: FakeUploadAPI(replies: replies))

        await queue.drain()
        XCTAssertEqual(document.state, .retryableFailed)
        XCTAssertEqual(document.attempt, 1)

        await queue.drain()
        XCTAssertEqual(document.state, .uploaded)
        XCTAssertEqual(document.serverDocumentId, serverDocumentId)
        XCTAssertEqual(batch.serverId, FakeUploadAPI.batchId)
    }

    @MainActor
    func testRetriesReachAttemptLimitThenBecomePermanent() async throws {
        let store = try makeStore()
        let document = try addDocument(store, try makeBatch(store), "limit.jpg", "limit")
        let api = FakeUploadAPI(replies: [.alwaysFailure(.network)])
        let queue = UploadQueue(store: store, api: api)

        for attempt in 1...4 {
            await queue.drain()
            XCTAssertEqual(document.state, .retryableFailed)
            XCTAssertEqual(document.attempt, attempt)
        }
        await queue.drain()
        XCTAssertEqual(document.state, .permanentFailed)

        let callsAtLimit = await api.calls
        XCTAssertEqual(callsAtLimit.filter { $0 == "presign" }.count, 5)
        await queue.drain()
        let callsAfterLimit = await api.calls
        XCTAssertEqual(callsAfterLimit, callsAtLimit)
    }

    @MainActor
    func testPermanentFailuresAreNeverRetried() async throws {
        let replies: [FakeUploadAPI.Reply] = [
            .failure(.validation),
            .missingUploadURL(documentId: UUID().uuidString),
            .ready(documentId: "not-a-uuid", uploadURL: Self.objectURL),
        ]
        for reply in replies {
            let store = try makeStore()
            let document = try addDocument(store, try makeBatch(store), "permanent.jpg", "permanent")
            let api = FakeUploadAPI(replies: [reply])
            let queue = UploadQueue(store: store, api: api)

            await queue.drain()
            XCTAssertEqual(document.state, .permanentFailed)
            XCTAssertEqual(document.attempt, 0)

            let callsAtFailure = await api.calls
            await queue.drain()
            let callsAfterRetry = await api.calls
            XCTAssertEqual(callsAfterRetry, callsAtFailure)
            XCTAssertEqual(document.state, .permanentFailed)
        }
    }

    @MainActor
    func testRunRecoversInterruptedUploadBeforeDraining() async throws {
        let store = try makeStore()
        let document = try addDocument(store, try makeBatch(store), "recover.jpg", "recover")
        document.state = .uploading
        try store.container.mainContext.save()
        let serverDocumentId = UUID()
        let api = FakeUploadAPI(replies: [
            .ready(documentId: serverDocumentId.uuidString, uploadURL: Self.objectURL),
        ])
        let monitor = FakeConnectivityMonitor()
        let queue = UploadQueue(store: store, api: api)
        let running = Task { await queue.run(connectivity: monitor) }

        await settle()
        XCTAssertEqual(document.state, .pending)

        monitor.send(true)
        try await waitUntil { document.state == .uploaded }
        monitor.finish()
        await running.value
        XCTAssertEqual(document.serverDocumentId, serverDocumentId)
    }

    @MainActor
    func testConcurrentDrainsUploadEachDocumentOnce() async throws {
        let store = try makeStore()
        let document = try addDocument(store, try makeBatch(store), "once.jpg", "once")
        let api = FakeUploadAPI(replies: [
            .ready(documentId: UUID().uuidString, uploadURL: Self.objectURL),
        ])
        let queue = UploadQueue(store: store, api: api)

        async let firstDrain: Void = queue.drain()
        async let secondDrain: Void = queue.drain()
        _ = await (firstDrain, secondDrain)

        let calls = await api.calls
        XCTAssertEqual(calls.filter { $0 == "presign" }.count, 1)
        XCTAssertEqual(calls.filter { $0 == "confirm" }.count, 1)
        XCTAssertEqual(document.state, .uploaded)
    }

    /// 重入的直接 drain 必须立即返回：首轮收尾前不得替失败文档重发 presign 消耗尝试次数。
    @MainActor
    func testConcurrentDrainDoesNotRetryAfterFirstFailure() async throws {
        let store = try makeStore()
        let document = try addDocument(store, try makeBatch(store), "reentry.jpg", "reentry")
        let gate = PresignGate()
        let serverDocumentId = UUID()
        let api = FakeUploadAPI(replies: [
            .gatedFailure(gate: gate, error: .network),
            .ready(documentId: serverDocumentId.uuidString, uploadURL: Self.objectURL),
        ])
        let queue = UploadQueue(store: store, api: api)

        async let firstDrain: Void = queue.drain()
        try await waitUntil { await api.calls.contains("presign") }
        async let secondDrain: Void = queue.drain()
        await settle()
        await gate.open()
        _ = await (firstDrain, secondDrain)

        let callsAfterFailure = await api.calls
        XCTAssertEqual(callsAfterFailure.filter { $0 == "presign" }.count, 1)
        XCTAssertEqual(document.state, .retryableFailed)
        XCTAssertEqual(document.attempt, 1)

        await queue.drain()
        XCTAssertEqual(document.state, .uploaded)
        XCTAssertEqual(document.serverDocumentId, serverDocumentId)
    }

    private static let objectURL = "https://storage.example.test/object"

    @MainActor
    private func makeStore() throws -> LocalStore {
        storeCount += 1
        let vault = try RawVault(root: root.appendingPathComponent("vault-\(storeCount)"))
        return LocalStore(container: try LocalStore.makeContainer(storeURL: nil), vault: vault)
    }
    @MainActor
    private func makeBatch(_ store: LocalStore) throws -> LocalBatch {
        try store.createBatch(title: "Synthetic trip", expenseType: "meal", templateId: UUID(), batchDate: "2026-11-22")
    }
    @MainActor
    private func addDocument(
        _ store: LocalStore, _ batch: LocalBatch, _ name: String, _ contents: String
    ) throws -> LocalDocument {
        let url = root.appendingPathComponent("\(UUID().uuidString)-\(name)")
        try Data(contents.utf8).write(to: url)
        return try store.addDocument(to: batch, fileAt: url, originalName: name)
    }
    private func settle() async {
        for _ in 0..<10 { await Task.yield() }
    }
    @MainActor
    private func waitUntil(_ timeout: TimeInterval = 2, _ condition: () async -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await condition() { return }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Condition not satisfied before timeout")
    }
}

private actor PresignGate {
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var isOpen = false
    func wait() async {
        if isOpen { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func open() {
        guard !isOpen else { return }
        isOpen = true
        for waiter in waiters { waiter.resume() }
        waiters.removeAll()
    }
}
private struct FakeConnectivityMonitor: ConnectivityMonitor {
    private let stream: AsyncStream<Bool>
    private let continuation: AsyncStream<Bool>.Continuation
    init() {
        let pair = AsyncStream.makeStream(of: Bool.self)
        stream = pair.stream
        continuation = pair.continuation
    }
    func updates() -> AsyncStream<Bool> { stream }
    func send(_ online: Bool) { continuation.yield(online) }
    func finish() { continuation.finish() }
}
private actor FakeUploadAPI: UploadAPI {
    enum Reply {
        case ready(documentId: String, uploadURL: String)
        case instant(documentId: String)
        case missingUploadURL(documentId: String)
        case gated(documentId: String, uploadURL: String, gate: PresignGate)
        case gatedFailure(gate: PresignGate, error: AppError)
        case failure(AppError)
        case alwaysFailure(AppError)
    }
    static let batchId = UUID(uuidString: "33333333-3333-3333-3333-333333333333")!
    private static let timestamp = "2026-11-22T00:00:00Z"
    private(set) var calls: [String] = []
    private(set) var uploadedBytes: [Data] = []
    private var replies: [Reply]

    init(replies: [Reply]) { self.replies = replies }
    func createBatch(_ batch: BatchCreate) async throws -> BatchOut {
        calls.append("createBatch")
        await Task.yield()
        return BatchOut(
            batchDate: batch.batchDate, createdAt: Self.timestamp, destinations: [],
            expenseType: batch.expenseType, id: Self.batchId.uuidString.lowercased(),
            missingAlerts: [], status: "draft", templateId: batch.templateId, title: batch.title,
            tripEndDate: nil, tripStartDate: nil, updatedAt: Self.timestamp
        )
    }
    func presign(batchId: UUID, _ payload: PresignRequest) async throws -> PresignResponse {
        calls.append("presign")
        await Task.yield()
        guard !replies.isEmpty else { throw AppError.unknown }
        let reply = replies.removeFirst()
        switch reply {
        case let .alwaysFailure(error):
            replies.insert(reply, at: 0)
            throw error
        case let .failure(error):
            throw error
        case let .gated(documentId, uploadURL, gate):
            await gate.wait()
            return PresignResponse(alreadyExists: false, documentId: documentId, expiresIn: 300, uploadMethod: "PUT", uploadUrl: uploadURL)
        case let .gatedFailure(gate, error):
            await gate.wait()
            throw error
        case let .ready(documentId, uploadURL):
            return PresignResponse(alreadyExists: false, documentId: documentId, expiresIn: 300, uploadMethod: "PUT", uploadUrl: uploadURL)
        case let .instant(documentId):
            return PresignResponse(alreadyExists: true, documentId: documentId, expiresIn: nil, uploadMethod: nil, uploadUrl: nil)
        case let .missingUploadURL(documentId):
            return PresignResponse(alreadyExists: false, documentId: documentId, expiresIn: 300, uploadMethod: "PUT", uploadUrl: nil)
        }
    }
    func upload(to url: URL, fileURL: URL, contentType: String) async throws {
        calls.append("upload")
        await Task.yield()
        uploadedBytes.append(try Data(contentsOf: fileURL))
    }

    func confirm(batchId: UUID, _ payload: ConfirmRequest) async throws -> DocumentOut {
        calls.append("confirm")
        await Task.yield()
        return DocumentOut(
            batchId: batchId.uuidString.lowercased(), createdAt: Self.timestamp, docRole: "receipt",
            fileSizeBytes: 0, id: payload.documentId, mimeType: payload.mimeType,
            ocrStatus: "pending", originalFilename: payload.filename, sha256Hash: payload.sha256Hash
        )
    }
}
