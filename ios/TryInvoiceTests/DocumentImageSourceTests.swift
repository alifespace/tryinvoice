import Foundation
import XCTest
@testable import TryInvoice

final class DocumentImageSourceTests: XCTestCase {
    private var root: URL!
    private var sourceFile: URL!
    private let mappedId = UUID(uuidString: "bbbbbbbb-0000-4000-8000-000000000001")!
    private let missingFileId = UUID(uuidString: "bbbbbbbb-0000-4000-8000-000000000002")!
    private let escapingId = UUID(uuidString: "bbbbbbbb-0000-4000-8000-000000000003")!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("DocumentImageSourceTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        sourceFile = root.appendingPathComponent("synthetic-receipt.jpg")
        try SyntheticFixtures.jpegData().write(to: sourceFile)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    func testReadsStoredVaultFileForMappedIdentifier() async throws {
        let vault = try RawVault(root: root.appendingPathComponent("vault"))
        let stored = try vault.store(fileAt: sourceFile, batchId: UUID(), originalName: "receipt.jpg")
        let source = LocalVaultImageSource(vault: vault) {
            [mappedId = self.mappedId, relativePath = stored.relativePath] id in
            id == mappedId ? relativePath : nil
        }

        let data = try await source.imageData(forDocumentId: mappedId.uuidString.lowercased())

        XCTAssertEqual(data, try Data(contentsOf: sourceFile))
        XCTAssertEqual(data, try vault.read(stored.relativePath))
    }

    func testUnavailableForInvalidUnmappedMissingAndEscapingIdentifiers() async throws {
        let vault = try RawVault(root: root.appendingPathComponent("vault"))
        let stored = try vault.store(fileAt: sourceFile, batchId: UUID(), originalName: "receipt.jpg")
        let absentPath = "\(UUID().uuidString)/missing.jpg"
        let source = LocalVaultImageSource(vault: vault) {
            [mappedId = self.mappedId, missingFileId = self.missingFileId, escapingId = self.escapingId] id in
            switch id {
            case mappedId: return stored.relativePath
            case missingFileId: return absentPath
            case escapingId: return "../escape.jpg"
            default: return nil
            }
        }

        await assertUnavailable(source, id: "not-a-uuid")
        await assertUnavailable(source, id: UUID().uuidString)
        await assertUnavailable(source, id: missingFileId.uuidString)
        await assertUnavailable(source, id: escapingId.uuidString)
    }

    private func assertUnavailable(
        _ source: DocumentImageSource,
        id: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        do {
            _ = try await source.imageData(forDocumentId: id)
            XCTFail("expected unavailable for \(id)", file: file, line: line)
        } catch {
            XCTAssertEqual(error as? DocumentImageError, .unavailable, file: file, line: line)
        }
    }
}
