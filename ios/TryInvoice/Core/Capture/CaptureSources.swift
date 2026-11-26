import Foundation
import UIKit

/// 采集转换结果：可导入项、逐项失败原因与本次临时目录（供调用方导入后清理）。
struct CaptureSourceBatch {
    let items: [ImportItem]
    let failures: [(index: Int, error: ImportError)]
    let directories: [URL]
}

/// 三源 → 临时文件；只搬运字节并按魔数/扩展名命名，类型判定仍由 `ImportValidator` 负责。
enum CaptureSources {
    static let directoryPrefix = "TryInvoiceCapture-"

    /// 每页压缩为 JPEG(0.9)，按页序命名。
    static func fromScan(_ pageImages: [UIImage]) -> CaptureSourceBatch {
        build(pageImages.count) { directory in
            pageImages.enumerated().map { index, image in
                let name = "scan-\(index + 1).jpg"
                guard let data = image.jpegData(compressionQuality: 0.9) else { return .failure(.corruptFile) }
                return write(data, name, directory, name)
            }
        }
    }

    /// 相册字节按魔数定扩展名；`names` 只作文件名基底，不参与类型判定。
    static func fromPhotos(_ photos: [Data], names: [String]) -> CaptureSourceBatch {
        build(photos.count) { directory in
            photos.enumerated().map { index, data in
                let base = index < names.count ? names[index] : "photo-\(index + 1)"
                let stem = (base as NSString).deletingPathExtension
                let fallback = (base as NSString).pathExtension.lowercased()
                let ext = detectedExtension(of: data) ?? (fallback.isEmpty ? "bin" : fallback)
                let name = "\(stem.isEmpty ? "photo-\(index + 1)" : stem).\(ext)"
                return write(data, name, directory, name)
            }
        }
    }

    /// 文件选择结果只在安全作用域内读取一次，随即复制到本模块临时目录。
    static func fromFileURLs(_ urls: [URL]) -> CaptureSourceBatch {
        build(urls.count) { directory in
            urls.enumerated().map { index, url in
                let accessed = url.startAccessingSecurityScopedResource()
                defer { if accessed { url.stopAccessingSecurityScopedResource() } }
                guard let data = try? Data(contentsOf: url) else { return .failure(.corruptFile) }
                let copied = "\(index + 1)-\(url.lastPathComponent)"
                return write(data, copied, directory, url.lastPathComponent)
            }
        }
    }

    /// 只删除本模块登记并拥有的临时目录；外部传入的同名前缀目录一律保留。
    static func removeDirectories(_ directories: [URL]) {
        for directory in directories where ownedDirectories.claim(directory) {
            try? FileManager.default.removeItem(at: directory)
        }
    }

    /// 进程内有序登记表：仅记录本模块创建成功的临时目录，`NSLock` 保护跨 actor 访问。
    private static let ownedDirectories = DirectoryRegistry()

    private final class DirectoryRegistry: @unchecked Sendable {
        private let lock = NSLock()
        private var paths: Set<String> = []

        func register(_ directory: URL) { _ = lock.withLock { paths.insert(directory.standardizedFileURL.path) } }

        /// 认领并注销；返回 false 表示该目录不由本模块创建，调用方不得删除。
        func claim(_ directory: URL) -> Bool {
            lock.withLock { paths.remove(directory.standardizedFileURL.path) != nil }
        }
    }

    /// 按文件头判定扩展名；无法判定时返回 nil，交由校验器给出错误。
    static func detectedExtension(of data: Data) -> String? {
        let bytes = [UInt8](data.prefix(12))
        if bytes.starts(with: [0xFF, 0xD8, 0xFF]) { return "jpg" }
        if bytes.starts(with: [0x89, 0x50, 0x4E, 0x47]) { return "png" }
        if bytes.starts(with: Array("%PDF".utf8)) { return "pdf" }
        guard bytes.count == 12, Array(bytes[4..<8]) == Array("ftyp".utf8) else { return nil }
        let brand = String(decoding: bytes[8..<12], as: UTF8.self)
        return ["heic", "heix", "mif1", "heif"].contains(brand) ? "heic" : nil
    }

    private static func build(
        _ count: Int, _ make: (URL) -> [Result<ImportItem, ImportError>]
    ) -> CaptureSourceBatch {
        guard let directory = try? temporaryDirectory() else {
            return CaptureSourceBatch(
                items: [], failures: (0..<count).map { ($0, .corruptFile) }, directories: []
            )
        }
        let outcomes = make(directory)
        return CaptureSourceBatch(
            items: outcomes.compactMap { try? $0.get() },
            failures: outcomes.enumerated().compactMap { index, outcome in
                if case let .failure(error) = outcome { return (index, error) }
                return nil
            },
            directories: [directory]
        )
    }

    private static func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(directoryPrefix + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        ownedDirectories.register(url)
        return url
    }

    private static func write(
        _ data: Data, _ name: String, _ directory: URL, _ originalName: String
    ) -> Result<ImportItem, ImportError> {
        let url = directory.appendingPathComponent(name)
        do {
            try data.write(to: url, options: .atomic)
            return .success(ImportItem(fileURL: url, originalName: originalName))
        } catch {
            return .failure(.corruptFile)
        }
    }
}
