import Foundation

/// 上传所需的网络能力。生产实现是 `APIClient`，测试使用无网络的假实现。
protocol UploadAPI {
    func createBatch(_ batch: BatchCreate) async throws -> BatchOut
    func presign(batchId: UUID, _ payload: PresignRequest) async throws -> PresignResponse
    func upload(to url: URL, fileURL: URL, contentType: String) async throws
    func confirm(batchId: UUID, _ payload: ConfirmRequest) async throws -> DocumentOut
}

/// 传输中断：掉线或运行生命周期结束，只用于在尚未发出的步骤前退出传输，不产生失败状态。
private struct TransferInterruption: Error {}

/// 串行上传执行器：只读取 RawVault 原件，所有状态迁移经 LocalStore 落库。
@MainActor
final class UploadQueue {
    private let store: LocalStore
    private let api: any UploadAPI
    private var isDraining = false
    private var drainWaiters: [CheckedContinuation<Void, Never>] = []
    /// 每次 `run` 领取一个序号；旧运行的 drain 不再持有放行权。
    private var currentRun = 0
    /// 正在执行的 drain 所属运行序号；直接 `drain()` 为 nil。
    private var activeRun: Int?
    /// 当前连通性；默认在线，便于不依赖监测器的直接 drain。
    private(set) var isOnline = true
    /// 运行生命周期：`run` 存活期间为 true；结束（含视图取消）后不再启动新步骤。
    /// 对外只读，供接线与测试核验“取消后零新增请求”的门闩。
    private(set) var isRunActive = true

    init(store: LocalStore, api: any UploadAPI) {
        self.store = store
        self.api = api
    }

    /// 启动时先订阅监测流，再等待上一运行收尾并回收残留状态；等待期间不发请求，
    /// 也不复位旧运行仍持有的上传中状态。运行取消后，流终止即关闭生命周期。
    func run(connectivity: any ConnectivityMonitor) async {
        currentRun &+= 1
        let run = currentRun
        isRunActive = true
        let updates = connectivity.updates()
        await waitForIdleDrain()
        guard run == currentRun else { return }
        _ = try? store.recoverOnLaunch()
        var wasOnline = false
        var drains: [Task<Void, Never>] = []
        for await online in updates {
            isOnline = online
            if online && !wasOnline {
                drains.append(Task { await self.drain(run: run) })
            }
            wasOnline = online
        }
        // 监测流结束后先关闭生命周期，再等待已发出的请求自然收尾。
        if run == currentRun { isRunActive = false }
        for drain in drains { await drain.value }
    }

    /// 直接 drain 已在执行时立即返回；由 `run` 发起的 drain 先等待上一轮完全收尾，
    /// 避免旧运行收尾期与最新运行重叠发请求。
    func drain(run: Int? = nil) async {
        if run != nil {
            await waitForIdleDrain()
        } else if isDraining {
            return
        }
        guard isRunActive, isOnline, run == nil || run == currentRun else { return }
        isDraining = true
        activeRun = run
        defer {
            isDraining = false
            activeRun = nil
            drainWaiters.forEach { $0.resume() }
            drainWaiters.removeAll()
        }
        for document in store.uploadCandidates() {
            guard canStartWork else { return }
            await upload(document)
        }
    }

    private func waitForIdleDrain() async {
        while isDraining {
            await withCheckedContinuation { drainWaiters.append($0) }
        }
    }

    private func upload(_ document: LocalDocument) async {
        do {
            try store.apply(.start, to: document)
        } catch { return }
        do {
            try await transfer(document)
        } catch is TransferInterruption {
            // 掉线或运行结束只停在尚未发出的步骤；把 uploading 退回 pending，供下次续传。
            try? store.apply(.launchRecovery, to: document)
        } catch {
            fail(document, with: error)
        }
    }

    private func transfer(_ document: LocalDocument) async throws {
        guard let batch = document.batch else {
            throw AppError.validation
        }
        let batchId = try await serverBatchId(batch)
        let fileURL = try store.vault.url(forRelativePath: document.relativePath)
        try ensureCanStart()
        let presign = try await api.presign(batchId: batchId, PresignRequest(
            filename: document.originalName, mimeType: document.mimeType,
            sha256Hash: document.sha256))
        let serverDocumentId = try self.uuid(presign.documentId)
        if presign.alreadyExists {
            try store.backfill(serverDocumentId: serverDocumentId, for: document)
            try store.apply(.presignedExisting, to: document)
            return
        }
        guard let uploadURLValue = presign.uploadUrl,
              let uploadURL = URL(string: uploadURLValue) else { throw AppError.validation }
        try ensureCanStart()
        try await api.upload(
            to: uploadURL,
            fileURL: fileURL,
            contentType: document.mimeType
        )
        try ensureCanStart()
        let confirmed = try await api.confirm(batchId: batchId, ConfirmRequest(
            clientOcrJson: clientOcrJSON(for: document), documentId: presign.documentId,
            exifTakenAt: nil, filename: document.originalName, mimeType: document.mimeType,
            sha256Hash: document.sha256))
        try store.backfill(serverDocumentId: try uuid(confirmed.id), for: document)
        try store.apply(.confirmed, to: document)
    }

    private func serverBatchId(_ batch: LocalBatch) async throws -> UUID {
        if let serverId = batch.serverId { return serverId }
        try ensureCanStart()
        let created = try await api.createBatch(BatchCreate(
            batchDate: batch.batchDate, expenseType: batch.expenseType,
            templateId: batch.templateId.uuidString.lowercased(), title: batch.title))
        let serverId = try uuid(created.id)
        try store.backfill(serverId: serverId, for: batch)
        return serverId
    }

    /// 掉线或运行生命周期结束后不得启动新的网络步骤；已发出的请求不被取消，允许自然完成。
    private func ensureCanStart() throws {
        guard canStartWork else { throw TransferInterruption() }
    }

    /// 只有在线、`run` 仍存活，且当前步骤属于最新运行时才允许发起新请求。
    private var canStartWork: Bool {
        isOnline && isRunActive && (activeRun == nil || activeRun == currentRun)
    }

    /// 已存 OCR JSON 解码为并发契约值；缺失或解码失败一律 nil，绝不阻断上传。
    private func clientOcrJSON(for document: LocalDocument) -> JSONValue? {
        guard let raw = document.ocrJSON, let data = raw.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(JSONValue.self, from: data)
    }

    /// 服务端 ID 必须是 UUID；解析失败按未知错误处理，最终归为永久失败。
    private func uuid(_ value: String) throws -> UUID {
        guard let identifier = UUID(uuidString: value) else { throw AppError.unknown }
        return identifier
    }

    /// 仅网络、服务端与授权问题可重试；错误内容不写日志。
    private func fail(_ document: LocalDocument, with error: Error) {
        let failure = (error as? AppError).map(UploadFailure.classify) ?? .permanent
        try? store.apply(.failed(failure), to: document)
    }
}
