import Foundation

/// 详情页可改选的关联凭证；角色、重复与占用校验全部由服务端负责。
struct LinkCandidate: Identifiable, Equatable {
    let id: UUID
    let name: String
}

/// 本批次可作为关联候选的只读凭证来源；不包含任何筛选或角色判断。
protocol LinkCandidateSource: Sendable {
    func candidates(forBatchId: UUID) async -> [LinkCandidate]
}

/// 复核与模板页共用的只读/转发依赖；只做装配，不承载任何报销规则。
struct ReviewServices {
    let reviewAPI: any TransactionReviewAPI
    let templateAPI: any TemplateAPI
    let images: any DocumentImageSource
    let linkCandidates: any LinkCandidateSource
}

/// 从本地已回填服务端 ID 的文档取候选；只读、不写盘。
struct StoreLinkCandidateSource: LinkCandidateSource {
    private let store: LocalStore

    init(store: LocalStore) {
        self.store = store
    }

    func candidates(forBatchId id: UUID) async -> [LinkCandidate] {
        await MainActor.run {
            store.linkCandidates(forBatchServerId: id).map {
                LinkCandidate(id: $0.id, name: $0.name)
            }
        }
    }
}

/// 把本地已回填的服务端文档 ID 映射到 RawVault 原件；不缓存、不写盘、不触碰网络与凭证。
struct StoreDocumentImageSource: DocumentImageSource {
    private let store: LocalStore

    init(store: LocalStore) {
        self.store = store
    }

    func imageData(forDocumentId id: String) async throws -> Data {
        guard let identifier = UUID(uuidString: id) else {
            throw DocumentImageError.unavailable
        }
        let relativePath = await MainActor.run {
            store.relativePath(forServerDocumentId: identifier)
        }
        guard let relativePath else {
            throw DocumentImageError.unavailable
        }
        let data = await MainActor.run { try? store.vault.read(relativePath) }
        guard let data else {
            throw DocumentImageError.unavailable
        }
        return data
    }
}
