import Combine
import Foundation
import UIKit

/// 采集页状态与确定性反馈；不做去重、配对或金额计算，也不直接联网。
@MainActor
final class CaptureViewModel: ObservableObject {
    enum State: Equatable {
        case idle
        case importing
        case finished(CaptureSummary)
    }

    struct CaptureSummary: Equatable {
        let imported: Int
        let failures: [ImportError]
    }

    /// 真实费用类型与模板由 M4.6 选择，上传到服务端前由其回填；S3 只写占位草稿。
    static let draftDefaults = (
        expenseType: "unassigned",
        templateId: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
    )

    @Published private(set) var state: State = .idle
    /// 页面级提示（如设备不支持扫描）；由 ViewModel 独占持有，导入或再次反馈时清空，避免旧提示遮挡结果。
    @Published private(set) var scanNotice: String?
    private let pipeline: ImportPipeline
    private let store: LocalStore
    private let now: () -> Date
    private var draftBatch: LocalBatch?

    init(pipeline: ImportPipeline, store: LocalStore, now: @escaping () -> Date = Date.init) {
        self.pipeline = pipeline
        self.store = store
        self.now = now
    }

    /// 成功数与失败原因；失败按类型去重计数，文案固定且可直接展示。页面级提示优先。
    var feedback: String? {
        if let scanNotice { return scanNotice }
        guard case let .finished(summary) = state else { return nil }
        var parts: [String] = []
        if summary.imported > 0 { parts.append("成功导入 \(summary.imported) 份。") }
        let counts = summary.failures.reduce(into: [ImportError: Int]()) { $0[$1, default: 0] += 1 }
        let details = Self.failureOrder.compactMap { error in
            counts[error].map { "\($0) 项：\(error.userMessage)" }
        }
        if !details.isEmpty { parts.append("未导入 " + details.joined(separator: " ")) }
        return parts.isEmpty ? "没有可导入的文件。" : parts.joined()
    }

    func importScanPages(_ images: [UIImage]) async {
        guard beginEntry() else { return }
        await importSource(CaptureSources.fromScan(images))
    }

    func importFileURLs(_ urls: [URL]) async {
        guard beginEntry() else { return }
        await importSource(CaptureSources.fromFileURLs(urls))
    }

    /// 相册取字节期间保持 `.importing`，从入口层杜绝并发导入与提前恢复可操作状态。
    func importPhotos(loaders: [() async -> Data?]) async {
        guard beginEntry() else { return }
        state = .importing
        var photos: [Data] = []
        var unreadable = 0
        for load in loaders {
            if let data = await load() { photos.append(data) } else { unreadable += 1 }
        }
        let source = CaptureSources.fromPhotos(photos, names: photos.indices.map { "photo-\($0 + 1)" })
        await importSource(source, extraFailures: Array(repeating: .corruptFile, count: unreadable))
    }

    /// 串行导入并清理本次转换出的临时文件；草稿批次在同一个 ViewModel 内复用。
    func importItems(_ items: [ImportItem]) async {
        let directories = Set(items.map { $0.fileURL.deletingLastPathComponent() })
        guard beginEntry() else {
            CaptureSources.removeDirectories(Array(directories))
            return
        }
        await run(items: items, extraFailures: [], directories: directories)
    }

    func reportPermissionDenied() { finish(CaptureSummary(imported: 0, failures: [.permissionDenied])) }

    func reportCancelled() { finish(CaptureSummary(imported: 0, failures: [.cancelled])) }

    /// 文件选择器返回读取失败：与用户取消分开反馈，同样不创建批次或记录。
    func reportReadFailure() { finish(CaptureSummary(imported: 0, failures: [.corruptFile])) }

    func reportScanUnsupported() {
        guard state != .importing else { return }
        scanNotice = "此设备不支持扫描。"
        state = .idle
    }

    /// 反馈只在无活跃导入时生效：迟到的回调不得解除互斥，也不遮挡进行中的导入结果。
    private func finish(_ summary: CaptureSummary) {
        guard state != .importing else { return }
        scanNotice = nil
        state = .finished(summary)
    }

    /// 导入入口公共闸门：导入中直接忽略（不改状态、不建批次），其余先清空页面级提示。
    private func beginEntry() -> Bool {
        guard state != .importing else { return false }
        scanNotice = nil
        return true
    }

    private func importSource(_ source: CaptureSourceBatch, extraFailures: [ImportError] = []) async {
        await run(
            items: source.items, extraFailures: source.failures.map(\.error) + extraFailures,
            directories: Set(source.directories)
        )
    }

    private func run(items: [ImportItem], extraFailures: [ImportError], directories: Set<URL>) async {
        defer { CaptureSources.removeDirectories(Array(directories)) }
        guard !items.isEmpty else {
            state = .finished(CaptureSummary(imported: 0, failures: extraFailures))
            return
        }
        state = .importing
        guard let batch = openDraftBatch() else {
            let failures = extraFailures + Array(repeating: .corruptFile, count: items.count)
            state = .finished(CaptureSummary(imported: 0, failures: failures))
            return
        }
        let report = await pipeline.importAll(items, into: batch)
        state = .finished(CaptureSummary(
            imported: report.imported.count, failures: report.failures.map(\.error) + extraFailures
        ))
    }

    private func openDraftBatch() -> LocalBatch? {
        if let draftBatch { return draftBatch }
        let day = Self.dayFormatter.string(from: now())
        let batch = try? store.createBatch(
            title: "本地导入 \(day)", expenseType: Self.draftDefaults.expenseType,
            templateId: Self.draftDefaults.templateId, batchDate: day
        )
        draftBatch = batch
        return batch
    }

    private static let failureOrder: [ImportError] = [
        .unsupportedType, .fileTooLarge, .corruptFile, .duplicate, .ocrFailed, .permissionDenied, .cancelled,
    ]

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}
