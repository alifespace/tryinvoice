import Foundation
import SwiftData

enum LocalStoreError: Error, Equatable {
    case unsupportedFileType
    case conflictingServerId
}

/// 离线批次与上传队列的唯一写入入口。原始文件只经 RawVault 追加，LocalStore 不提供删除或改写 API。
@MainActor
final class LocalStore {
    typealias SaveOperation = (ModelContext) throws -> Void

    let container: ModelContainer
    let vault: RawVault

    private let saveOperation: SaveOperation

    init(
        container: ModelContainer,
        vault: RawVault,
        saveOperation: SaveOperation? = nil
    ) {
        self.container = container
        self.vault = vault
        self.saveOperation = saveOperation ?? { try $0.save() }
    }

    private var context: ModelContext {
        container.mainContext
    }

    static func makeContainer(storeURL: URL?) throws -> ModelContainer {
        let schema = Schema([LocalBatch.self, LocalDocument.self])
        let configuration: ModelConfiguration
        if let storeURL {
            let directory = storeURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
            configuration = ModelConfiguration(schema: schema, url: storeURL)
        } else {
            configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        }
        return try ModelContainer(for: schema, configurations: configuration)
    }

    @discardableResult
    func createBatch(
        title: String,
        expenseType: String,
        templateId: UUID,
        batchDate: String
    ) throws -> LocalBatch {
        let batch = LocalBatch(
            title: title,
            expenseType: expenseType,
            templateId: templateId,
            batchDate: batchDate
        )
        context.insert(batch)
        try save()
        return batch
    }

    @discardableResult
    func addDocument(
        to batch: LocalBatch,
        fileAt fileURL: URL,
        originalName: String
    ) throws -> LocalDocument {
        let file = try vault.store(
            fileAt: fileURL,
            batchId: batch.id,
            originalName: originalName
        )
        let mimeType = try Self.mimeType(
            forExtension: (originalName as NSString).pathExtension
        )
        let document = LocalDocument(
            relativePath: file.relativePath,
            sha256: file.sha256,
            sizeBytes: file.sizeBytes,
            mimeType: mimeType,
            originalName: originalName
        )
        document.batch = batch
        context.insert(document)
        try save()
        return document
    }

    func uploadCandidates() -> [LocalDocument] {
        guard let documents = try? context.fetch(FetchDescriptor<LocalDocument>()) else {
            return []
        }
        return documents
            .filter { $0.state == .pending || $0.state == .retryableFailed }
            .sorted {
                ($0.createdAt, $0.id.uuidString) < ($1.createdAt, $1.id.uuidString)
            }
    }

    func apply(
        _ event: UploadEvent,
        to document: LocalDocument,
        maxAttempts: Int = 5
    ) throws {
        let current = document.state
        guard !current.isTerminal else { return }
        let next = current.next(
            event,
            attempt: document.attempt,
            maxAttempts: maxAttempts
        )
        guard next != current else { return }
        document.stateRaw = next.rawValue
        if case .failed(.retryable) = event {
            document.attempt += 1
        }
        try save()
    }

    @discardableResult
    func recoverOnLaunch() throws -> Int {
        let documents = try context.fetch(FetchDescriptor<LocalDocument>())
        let interrupted = documents.filter { $0.state == .uploading }
        guard !interrupted.isEmpty else { return 0 }
        for document in interrupted {
            document.stateRaw = document.state.next(
                .launchRecovery,
                attempt: document.attempt
            ).rawValue
        }
        try save()
        return interrupted.count
    }

    func backfill(serverId: UUID, for batch: LocalBatch) throws {
        if let existing = batch.serverId {
            guard existing == serverId else {
                throw LocalStoreError.conflictingServerId
            }
            return
        }
        batch.serverId = serverId
        try save()
    }

    func backfill(serverDocumentId: UUID, for document: LocalDocument) throws {
        if let existing = document.serverDocumentId {
            guard existing == serverDocumentId else {
                throw LocalStoreError.conflictingServerId
            }
            return
        }
        document.serverDocumentId = serverDocumentId
        try save()
    }

    static func mimeType(forExtension fileExtension: String) throws -> String {
        switch fileExtension.lowercased() {
        case "jpg", "jpeg":
            return "image/jpeg"
        case "png":
            return "image/png"
        case "heic":
            return "image/heic"
        case "pdf":
            return "application/pdf"
        default:
            throw LocalStoreError.unsupportedFileType
        }
    }

    private func save() throws {
        do {
            try saveOperation(context)
        } catch {
            context.rollback()
            throw error
        }
    }
}
