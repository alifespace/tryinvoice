import Foundation

enum RawVaultError: Error, Equatable {
    case unsupportedType
    case alreadyExists
    case pathEscapes
    case unreadableSource
    case missing
    case storageUnavailable
}

/// 原始凭证只读仓库。文件一经写入即不可原位修改，错误与日志均不携带路径、原文件名或内容。
struct RawVault {
    struct RawFile: Codable, Equatable, Sendable {
        let relativePath: String
        let sha256: String
        let sizeBytes: Int
    }

    static let allowedExtensions: Set<String> = ["jpg", "jpeg", "png", "heic", "pdf"]

    let root: URL
    private let fileManager: FileManager
    private let idProvider: () -> UUID
    private let tempIdProvider: () -> UUID

    static var defaultRoot: URL {
        FileManager.default
            .urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("RawVault", isDirectory: true)
    }

    init(
        root: URL,
        fileManager: FileManager = .default,
        idProvider: @escaping () -> UUID = UUID.init,
        tempIdProvider: @escaping () -> UUID = UUID.init
    ) throws {
        self.root = root
        self.fileManager = fileManager
        self.idProvider = idProvider
        self.tempIdProvider = tempIdProvider
        do {
            try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
            try Self.excludeFromBackup(root)
        } catch {
            throw RawVaultError.storageUnavailable
        }
    }

    /// 复制到随机文件名后原子移动，绝不覆盖既有目标，绝不改动源文件。
    func store(fileAt source: URL, batchId: UUID, originalName: String) throws -> RawFile {
        let fileExtension = (originalName as NSString).pathExtension.lowercased()
        guard Self.allowedExtensions.contains(fileExtension) else {
            throw RawVaultError.unsupportedType
        }

        let identifier = idProvider()
        let temporaryIdentifier = tempIdProvider()
        let batchDirectory = root.appendingPathComponent(batchId.uuidString, isDirectory: true)
        let destination = batchDirectory
            .appendingPathComponent(identifier.uuidString)
            .appendingPathExtension(fileExtension)
        let temporary = batchDirectory.appendingPathComponent(".tmp-\(temporaryIdentifier.uuidString)")
        var ownsTemporary = false

        // 只删除本次操作实际创建的临时文件；预先存在的同名项不属于本次操作。
        defer {
            if ownsTemporary {
                try? fileManager.removeItem(at: temporary)
            }
        }

        do {
            // 文件系统变更前先确认批次目录与目标是仓库内的正常路径，避免仓库外副作用。
            try verifyBatchDirectory(batchDirectory)
            try verifyDestinationIsVacant(destination)

            try fileManager.createDirectory(at: batchDirectory, withIntermediateDirectories: true)
            try Self.excludeFromBackup(batchDirectory)
            let sourceAttributes = try? fileManager.attributesOfItem(atPath: source.path)
            guard (sourceAttributes?[.type] as? FileAttributeType) == .typeRegular else {
                throw RawVaultError.unreadableSource
            }
            try fileManager.copyItem(at: source, to: temporary)
            ownsTemporary = true

            let attributes = try fileManager.attributesOfItem(atPath: temporary.path)
            let sizeBytes = (attributes[.size] as? NSNumber)?.intValue ?? 0
            let digest = try FileHasher.sha256(of: temporary)

            // 备份排除需要写 xattr，必须先于只读位设置，否则对 0444 文件写 xattr 会失败。
            try Self.excludeFromBackup(temporary)
            try fileManager.setAttributes([.posixPermissions: 0o444], ofItemAtPath: temporary.path)
            try fileManager.moveItem(at: temporary, to: destination)
            ownsTemporary = false

            return RawFile(
                relativePath: "\(batchId.uuidString)/\(identifier.uuidString).\(fileExtension)",
                sha256: digest,
                sizeBytes: sizeBytes
            )
        } catch let error as RawVaultError {
            throw error
        } catch {
            throw RawVaultError.storageUnavailable
        }
    }

    /// 拒绝仓库外路径、符号链接与同名非目录项；不创建任何内容。
    private func verifyBatchDirectory(_ directory: URL) throws {
        if let attributes = try? fileManager.attributesOfItem(atPath: directory.path) {
            guard (attributes[.type] as? FileAttributeType) == .typeDirectory else {
                throw RawVaultError.pathEscapes
            }
        }
        try verifyInsideRoot(directory)
    }

    /// 目标存在（含悬空符号链接）时拒绝写入；符号链接按逃逸处理。
    private func verifyDestinationIsVacant(_ destination: URL) throws {
        guard let attributes = try? fileManager.attributesOfItem(atPath: destination.path) else {
            return
        }
        if (attributes[.type] as? FileAttributeType) == .typeSymbolicLink {
            throw RawVaultError.pathEscapes
        }
        throw RawVaultError.alreadyExists
    }

    private func verifyInsideRoot(_ candidate: URL) throws {
        let rootPath = root.resolvingSymlinksInPath().standardizedFileURL.path
        let candidatePath = candidate.resolvingSymlinksInPath().standardizedFileURL.path
        let prefix = rootPath.hasSuffix("/") ? rootPath : rootPath + "/"
        guard candidatePath.hasPrefix(prefix) else {
            throw RawVaultError.pathEscapes
        }
    }

    /// 只接受仓库内的相对路径；拒绝绝对路径、空段、`..` 与符号链接逃逸。
    func url(forRelativePath relativePath: String) throws -> URL {
        guard !relativePath.isEmpty, !relativePath.hasPrefix("/") else {
            throw RawVaultError.pathEscapes
        }
        let segments = relativePath.split(separator: "/", omittingEmptySubsequences: false)
        guard segments.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
            throw RawVaultError.pathEscapes
        }

        let candidate = segments.reduce(root) { url, segment in
            url.appendingPathComponent(String(segment))
        }
        try verifyInsideRoot(candidate)
        return candidate
    }

    func exists(_ relativePath: String) throws -> Bool {
        fileManager.fileExists(atPath: try url(forRelativePath: relativePath).path)
    }

    func read(_ relativePath: String) throws -> Data {
        let fileURL = try url(forRelativePath: relativePath)
        guard fileManager.fileExists(atPath: fileURL.path) else {
            throw RawVaultError.missing
        }
        do {
            return try Data(contentsOf: fileURL)
        } catch {
            throw RawVaultError.storageUnavailable
        }
    }

    private static func excludeFromBackup(_ url: URL) throws {
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var target = url
        try target.setResourceValues(values)
    }
}
