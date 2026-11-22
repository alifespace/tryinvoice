import Foundation
import SwiftData

@Model
final class LocalBatch {
    @Attribute(.unique) var id: UUID
    var serverId: UUID?
    var title: String
    var expenseType: String
    var templateId: UUID
    var batchDate: String
    var createdAt: Date
    @Relationship(deleteRule: .cascade, inverse: \LocalDocument.batch)
    var documents: [LocalDocument]

    init(
        id: UUID = UUID(),
        serverId: UUID? = nil,
        title: String,
        expenseType: String,
        templateId: UUID,
        batchDate: String,
        createdAt: Date = .now
    ) {
        self.id = id
        self.serverId = serverId
        self.title = title
        self.expenseType = expenseType
        self.templateId = templateId
        self.batchDate = batchDate
        self.createdAt = createdAt
        self.documents = []
    }
}

@Model
final class LocalDocument {
    @Attribute(.unique) var id: UUID
    var serverDocumentId: UUID?
    var relativePath: String
    var sha256: String
    var sizeBytes: Int
    var mimeType: String
    var originalName: String
    var stateRaw: String
    var attempt: Int
    var createdAt: Date
    var batch: LocalBatch?

    var state: UploadState {
        get { UploadState(rawValue: stateRaw) ?? .pending }
        set { stateRaw = newValue.rawValue }
    }

    init(
        id: UUID = UUID(),
        serverDocumentId: UUID? = nil,
        relativePath: String,
        sha256: String,
        sizeBytes: Int,
        mimeType: String,
        originalName: String,
        state: UploadState = .pending,
        attempt: Int = 0,
        createdAt: Date = .now
    ) {
        self.id = id
        self.serverDocumentId = serverDocumentId
        self.relativePath = relativePath
        self.sha256 = sha256
        self.sizeBytes = sizeBytes
        self.mimeType = mimeType
        self.originalName = originalName
        self.stateRaw = state.rawValue
        self.attempt = attempt
        self.createdAt = createdAt
    }
}
