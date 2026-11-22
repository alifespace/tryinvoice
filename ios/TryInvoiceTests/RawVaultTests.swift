import Foundation
import XCTest
@testable import TryInvoice

final class RawVaultTests: XCTestCase {
    private var root: URL!
    private var sources: URL!

    override func setUpWithError() throws {
        let base = FileManager.default.temporaryDirectory
        root = base.appendingPathComponent("RawVaultTests-\(UUID().uuidString)", isDirectory: true)
        sources = base.appendingPathComponent("RawVaultSources-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: sources, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
        try? FileManager.default.removeItem(at: sources)
    }

    func testSHA256KnownVectorsAndStreamingChunks() throws {
        XCTAssertEqual(
            FileHasher.sha256(of: Data()),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        XCTAssertEqual(
            FileHasher.sha256(of: Data("abc".utf8)),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

        let payload = Data(repeating: 0x61, count: FileHasher.chunkSize + 7)
        let file = sources.appendingPathComponent("chunked.bin")
        try payload.write(to: file)
        let digest = try FileHasher.sha256(of: file)

        XCTAssertEqual(digest, FileHasher.sha256(of: payload))
        XCTAssertEqual(digest.count, 64)
        XCTAssertTrue(digest.allSatisfy { "0123456789abcdef".contains($0) })
    }

    func testStoreCopiesBytesWithHashPermissionsAndBackupExclusion() throws {
        let payload = Data("synthetic-image".utf8)
        let source = try makeSource(name: "IMG_0001.JPG", contents: payload)
        let batchId = UUID()
        let fileId = UUID()
        let vault = try RawVault(root: root, idProvider: { fileId })

        let stored = try vault.store(fileAt: source, batchId: batchId, originalName: "IMG_0001.JPG")

        XCTAssertEqual(stored.relativePath, "\(batchId.uuidString)/\(fileId.uuidString).jpg")
        XCTAssertEqual(stored.sha256, FileHasher.sha256(of: payload))
        XCTAssertEqual(stored.sizeBytes, payload.count)
        XCTAssertFalse(stored.relativePath.contains("IMG_0001"))

        let fileURL = try vault.url(forRelativePath: stored.relativePath)
        XCTAssertTrue(try vault.exists(stored.relativePath))
        XCTAssertEqual(try vault.read(stored.relativePath), payload)

        let attributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)
        let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0
        XCTAssertEqual(permissions & 0o777, 0o444)
        XCTAssertEqual(
            try fileURL.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup,
            true
        )
        XCTAssertEqual(
            try root.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup,
            true
        )
        XCTAssertEqual(try Data(contentsOf: source), payload)
    }

    func testDuplicateDestinationDoesNotOverwriteOriginal() throws {
        let batchId = UUID()
        let fileId = UUID()
        let first = try makeSource(name: "first.pdf", contents: Data("first".utf8))
        let second = try makeSource(name: "duplicate.pdf", contents: Data("second".utf8))
        let vault = try RawVault(root: root, idProvider: { fileId })

        let stored = try vault.store(fileAt: first, batchId: batchId, originalName: "first.pdf")
        XCTAssertThrowsError(
            try vault.store(fileAt: second, batchId: batchId, originalName: "duplicate.PDF")
        ) { error in
            XCTAssertEqual(error as? RawVaultError, .alreadyExists)
        }

        XCTAssertEqual(try vault.read(stored.relativePath), Data("first".utf8))
        let batchDirectory = root.appendingPathComponent(batchId.uuidString)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: batchDirectory.path).count, 1)
    }

    func testUnsupportedExtensionsAreRejectedAndCaseIsIgnored() throws {
        let vault = try RawVault(root: root)
        let batchId = UUID()

        for name in ["notes.txt", "archive.zip", "noextension", "image.gif"] {
            let source = try makeSource(name: name, contents: Data("x".utf8))
            XCTAssertThrowsError(try vault.store(fileAt: source, batchId: batchId, originalName: name)) {
                XCTAssertEqual($0 as? RawVaultError, .unsupportedType)
            }
        }

        let heic = try makeSource(name: "photo.HEIC", contents: Data("heic".utf8))
        let stored = try vault.store(fileAt: heic, batchId: batchId, originalName: "photo.HEIC")
        XCTAssertTrue(stored.relativePath.hasSuffix(".heic"))
    }

    func testRelativePathTraversalIsRejected() throws {
        let vault = try RawVault(root: root)

        for path in ["", "../secret.txt", "a/../../secret.txt", "/etc/passwd", "a//b.txt", "a/./b.txt"] {
            XCTAssertThrowsError(try vault.url(forRelativePath: path)) { error in
                XCTAssertEqual(error as? RawVaultError, .pathEscapes, "unexpected result for \(path)")
            }
        }
    }

    func testSymlinkEscapeIsRejected() throws {
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent("RawVaultOutside-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: outside) }
        try Data("secret".utf8).write(to: outside.appendingPathComponent("secret.txt"))
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent("link"),
            withDestinationURL: outside
        )
        let vault = try RawVault(root: root)

        XCTAssertThrowsError(try vault.url(forRelativePath: "link/secret.txt")) { error in
            XCTAssertEqual(error as? RawVaultError, .pathEscapes)
        }
    }

    func testFailedStoreLeavesNoTemporaryFilesAndHidesPaths() throws {
        let batchId = UUID()
        let missing = sources.appendingPathComponent("missing.jpg")
        let vault = try RawVault(root: root)

        var captured: Error?
        XCTAssertThrowsError(
            try vault.store(fileAt: missing, batchId: batchId, originalName: "missing.jpg")
        ) { error in
            captured = error
            XCTAssertEqual(error as? RawVaultError, .unreadableSource)
            XCTAssertFalse(String(describing: error).contains(missing.path))
        }
        XCTAssertNotNil(captured)

        let batchDirectory = root.appendingPathComponent(batchId.uuidString)
        let remaining = try FileManager.default.contentsOfDirectory(atPath: batchDirectory.path)
        XCTAssertTrue(remaining.isEmpty)
    }

    func testStoreRejectsSymlinkedBatchDirectoryAndLeavesOutsideUntouched() throws {
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent("RawVaultWriteOutside-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: outside) }
        let batchId = UUID()
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent(batchId.uuidString),
            withDestinationURL: outside
        )
        let source = try makeSource(name: "photo.jpg", contents: Data("synthetic".utf8))
        let vault = try RawVault(root: root)

        XCTAssertThrowsError(
            try vault.store(fileAt: source, batchId: batchId, originalName: "photo.jpg")
        ) { error in
            XCTAssertEqual(error as? RawVaultError, .pathEscapes)
        }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: outside.path), [])
    }

    func testStoreRejectsSymlinkedDestinationWithoutFollowingIt() throws {
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent("RawVaultDestinationOutside-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: outside) }
        let outsideFile = outside.appendingPathComponent("keep.jpg")
        try Data("outside".utf8).write(to: outsideFile)

        let batchId = UUID()
        let fileId = UUID()
        let batchDirectory = root.appendingPathComponent(batchId.uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: batchDirectory, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(
            at: batchDirectory.appendingPathComponent("\(fileId.uuidString).jpg"),
            withDestinationURL: outsideFile
        )
        let source = try makeSource(name: "photo.jpg", contents: Data("synthetic".utf8))
        let vault = try RawVault(root: root, idProvider: { fileId })

        XCTAssertThrowsError(
            try vault.store(fileAt: source, batchId: batchId, originalName: "photo.jpg")
        ) { error in
            XCTAssertEqual(error as? RawVaultError, .pathEscapes)
        }
        XCTAssertEqual(try Data(contentsOf: outsideFile), Data("outside".utf8))
    }

    func testDuplicateDestinationKeepsPreexistingTemporaryItDidNotCreate() throws {
        let batchId = UUID()
        let fileId = UUID()
        let temporaryId = UUID()
        let batchDirectory = root.appendingPathComponent(batchId.uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: batchDirectory, withIntermediateDirectories: true)
        let destination = batchDirectory.appendingPathComponent("\(fileId.uuidString).jpg")
        let foreignTemporary = batchDirectory.appendingPathComponent(".tmp-\(temporaryId.uuidString)")
        try Data("original".utf8).write(to: destination)
        try Data("foreign".utf8).write(to: foreignTemporary)
        let source = try makeSource(name: "new.jpg", contents: Data("new".utf8))
        let vault = try RawVault(root: root, idProvider: { fileId }, tempIdProvider: { temporaryId })

        XCTAssertThrowsError(
            try vault.store(fileAt: source, batchId: batchId, originalName: "new.jpg")
        ) { error in
            XCTAssertEqual(error as? RawVaultError, .alreadyExists)
        }
        XCTAssertEqual(try Data(contentsOf: destination), Data("original".utf8))
        XCTAssertEqual(try Data(contentsOf: foreignTemporary), Data("foreign".utf8))
    }

    func testStoreDoesNotDeleteForeignTemporaryWhenCopyCollides() throws {
        let batchId = UUID()
        let fileId = UUID()
        let temporaryId = UUID()
        let batchDirectory = root.appendingPathComponent(batchId.uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: batchDirectory, withIntermediateDirectories: true)
        let foreignTemporary = batchDirectory.appendingPathComponent(".tmp-\(temporaryId.uuidString)")
        try Data("foreign".utf8).write(to: foreignTemporary)
        let destination = batchDirectory.appendingPathComponent("\(fileId.uuidString).jpg")
        let source = try makeSource(name: "photo.jpg", contents: Data("new".utf8))
        let vault = try RawVault(root: root, idProvider: { fileId }, tempIdProvider: { temporaryId })

        XCTAssertThrowsError(
            try vault.store(fileAt: source, batchId: batchId, originalName: "photo.jpg")
        ) { error in
            XCTAssertEqual(error as? RawVaultError, .storageUnavailable)
        }
        XCTAssertEqual(try Data(contentsOf: foreignTemporary), Data("foreign".utf8))
        XCTAssertFalse(FileManager.default.fileExists(atPath: destination.path))
    }

    func testFailureAfterCopyRemovesOnlyItsOwnTemporaryFile() throws {
        let fileManager = FailingMoveFileManager()
        let batchId = UUID()
        let payload = Data("synthetic".utf8)
        let source = try makeSource(name: "photo.jpg", contents: payload)
        let vault = try RawVault(root: root, fileManager: fileManager)

        XCTAssertThrowsError(
            try vault.store(fileAt: source, batchId: batchId, originalName: "photo.jpg")
        ) { error in
            XCTAssertEqual(error as? RawVaultError, .storageUnavailable)
        }

        let batchDirectory = root.appendingPathComponent(batchId.uuidString)
        XCTAssertEqual(try fileManager.contentsOfDirectory(atPath: batchDirectory.path), [])
        XCTAssertEqual(try Data(contentsOf: source), payload)
    }

    func testReadReportsMissingFiles() throws {
        let vault = try RawVault(root: root)

        XCTAssertFalse(try vault.exists("missing.txt"))
        XCTAssertThrowsError(try vault.read("missing.txt")) { error in
            XCTAssertEqual(error as? RawVaultError, .missing)
        }
    }

    func testDefaultRootLivesInDocuments() {
        XCTAssertEqual(RawVault.defaultRoot.lastPathComponent, "RawVault")
        XCTAssertEqual(RawVault.defaultRoot.deletingLastPathComponent().lastPathComponent, "Documents")
    }

    private func makeSource(name: String, contents: Data) throws -> URL {
        let url = sources.appendingPathComponent(name)
        try contents.write(to: url)
        return url
    }
}

/// 在原子移动阶段失败，用于验证已创建临时文件的归属与清理。
private final class FailingMoveFileManager: FileManager {
    override func moveItem(at srcURL: URL, to dstURL: URL) throws {
        throw NSError(domain: "RawVaultTests", code: 1)
    }
}
