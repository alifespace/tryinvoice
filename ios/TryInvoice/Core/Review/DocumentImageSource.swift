import Foundation

/// 文档原始图片的只读入口。后续若接入预签名下载端点，只需新增实现。
protocol DocumentImageSource: Sendable {
    func imageData(forDocumentId id: String) async throws -> Data
}

enum DocumentImageError: Error, Equatable {
    case unavailable
}

/// 从 RawVault 本地原件读取图片；不接受网络、不写盘、不缓存路径或凭证。
///
/// 如后续引入临时缓存，只允许放在 `NSTemporaryDirectory()` 并在退出时清理；
/// 本 Subtask 不实现缓存。
struct LocalVaultImageSource: DocumentImageSource {
    private let reader: VaultReader
    private let relativePath: @Sendable (UUID) -> String?

    init(vault: RawVault, relativePath: @escaping @Sendable (UUID) -> String?) {
        self.reader = VaultReader(vault: vault)
        self.relativePath = relativePath
    }

    func imageData(forDocumentId id: String) async throws -> Data {
        guard
            let identifier = UUID(uuidString: id),
            let path = relativePath(identifier)
        else {
            throw DocumentImageError.unavailable
        }
        do {
            return try reader.read(path)
        } catch {
            throw DocumentImageError.unavailable
        }
    }
}

/// RawVault 的读取器只做不可变文件读取，跨并发域传递时由调用方保证只读语义。
private struct VaultReader: @unchecked Sendable {
    let vault: RawVault

    func read(_ relativePath: String) throws -> Data {
        try vault.read(relativePath)
    }
}
