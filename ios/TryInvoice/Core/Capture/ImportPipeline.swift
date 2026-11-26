import Foundation

/// 单个待导入文件：只携带调用方临时文件位置与原始文件名，不含票据内容。
struct ImportItem: Equatable, Sendable {
    let fileURL: URL
    let originalName: String
}

/// 批量导入结果：成功记录按输入顺序排列，失败项带原输入下标与可展示错误。
struct ImportReport {
    let imported: [LocalDocument]
    let failures: [(index: Int, error: ImportError)]
}

/// 导入管线：确定性校验 → 同批去重 → 端侧 OCR → 落库并保留 OCR JSON。
/// 全程离线，由确定性代码完成，不调用云端模型。
@MainActor
final class ImportPipeline {
    private let store: LocalStore
    private let validator: ImportValidator
    private let engine: any OCREngine

    /// 在途导入占用键：批次对象 + 内容哈希。
    private struct ImportKey: Hashable {
        let batch: ObjectIdentifier
        let sha256: String
    }

    /// 进行中的导入占用。「查重 + 占用」在同一 `@MainActor` 步进内完成，中间没有 `await`，
    /// 因此 OCR 挂起期间同批同哈希的第二次请求仍会在识别前被拒为 `.duplicate`。
    /// 导入失败退出时释放占用，允许随后重试同一内容；成功后由批次内的哈希继续去重。
    private static var inFlight: Set<ImportKey> = []

    init(
        store: LocalStore,
        validator: ImportValidator = ImportValidator(),
        engine: any OCREngine
    ) {
        self.store = store
        self.validator = validator
        self.engine = engine
    }

    /// 单文件导入。只抛 `ImportError`，失败时不产生任何 `LocalDocument`。
    func importFile(_ item: ImportItem, into batch: LocalBatch) async throws -> LocalDocument {
        try await importFile(item, into: batch, knownHashes: Set(batch.documents.map(\.sha256)))
    }

    /// 按输入顺序串行导入；单项失败不影响其余；任务取消后剩余项记 `.cancelled`。
    func importAll(_ items: [ImportItem], into batch: LocalBatch) async -> ImportReport {
        var imported: [LocalDocument] = []
        var failures: [(index: Int, error: ImportError)] = []
        var knownHashes = Set(batch.documents.map(\.sha256))

        for (index, item) in items.enumerated() {
            guard !Task.isCancelled else {
                for remaining in index..<items.count {
                    failures.append((remaining, .cancelled))
                }
                break
            }
            do {
                let document = try await importFile(item, into: batch, knownHashes: knownHashes)
                knownHashes.insert(document.sha256)
                imported.append(document)
            } catch let error as ImportError {
                failures.append((index, error))
            } catch {
                failures.append((index, .corruptFile))
            }
        }
        return ImportReport(imported: imported, failures: failures)
    }

    /// 顺序固定：校验 → 哈希去重（早于 OCR）→ OCR → 落库。源文件只读，不删除。
    private func importFile(
        _ item: ImportItem,
        into batch: LocalBatch,
        knownHashes: Set<String>
    ) async throws -> LocalDocument {
        let validated = try validate(item)
        let sha256 = try hash(of: item.fileURL)
        let key = try claim(sha256, in: batch, knownHashes: knownHashes)
        defer { Self.inFlight.remove(key) }

        let ocr = try await recognize(item, kind: validated.kind)
        guard let ocrJSON = Self.encode(ocr) else {
            throw ImportError.ocrFailed
        }
        return try persist(item, into: batch, ocrJSON: ocrJSON)
    }

    /// 扩展名、大小、文件头与可解码性校验；错误原样抛出。
    private func validate(_ item: ImportItem) throws -> ValidatedFile {
        do { return try validator.validate(fileAt: item.fileURL, originalName: item.originalName) }
        catch let error as ImportError { throw error }
        catch { throw ImportError.corruptFile }
    }

    private func hash(of url: URL) throws -> String {
        do { return try FileHasher.sha256(of: url) }
        catch { throw ImportError.corruptFile }
    }

    /// 同批去重（已存记录 + 在途占用）在同一个 `@MainActor` 步进内完成，跨 OCR 挂起仍有效。
    private func claim(
        _ sha256: String,
        in batch: LocalBatch,
        knownHashes: Set<String>
    ) throws -> ImportKey {
        guard !knownHashes.contains(sha256), !batch.documents.contains(where: { $0.sha256 == sha256 })
        else { throw ImportError.duplicate }
        let key = ImportKey(batch: ObjectIdentifier(batch), sha256: sha256)
        guard !Self.inFlight.contains(key) else { throw ImportError.duplicate }
        Self.inFlight.insert(key)
        return key
    }

    private func recognize(_ item: ImportItem, kind: ValidatedFile.Kind) async throws -> OCRDocument {
        do { return try await engine.recognize(fileAt: item.fileURL, kind: kind) }
        catch is CancellationError { throw ImportError.cancelled }
        catch let error as ImportError where error == .cancelled { throw ImportError.cancelled }
        catch { throw ImportError.ocrFailed }
    }

    /// 落库失败一律映射 `.corruptFile`；LocalStore 既有回滚保证无半成品记录。
    private func persist(
        _ item: ImportItem,
        into batch: LocalBatch,
        ocrJSON: String
    ) throws -> LocalDocument {
        do {
            return try store.addDocument(
                to: batch, fileAt: item.fileURL, originalName: item.originalName, ocrJSON: ocrJSON
            )
        } catch { throw ImportError.corruptFile }
    }

    /// 紧凑、键序稳定的 UTF-8 JSON；不写日志、不含路径。
    private static func encode(_ ocr: OCRDocument) -> String? {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(ocr.asJSONValue()) else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }
}
