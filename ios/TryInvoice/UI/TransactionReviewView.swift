import SwiftUI

/// 批次复核页：只消费服务端分组、金额与告警，客户端不做任何金额或均分计算。
struct TransactionReviewView: View {
    @StateObject private var viewModel: TransactionReviewViewModel
    private let batchId: UUID
    private let images: any DocumentImageSource
    private let linkCandidates: any LinkCandidateSource
    @State private var sourceCandidates: [LinkCandidate] = []

    init(
        batchId: UUID,
        api: any TransactionReviewAPI,
        images: any DocumentImageSource,
        linkCandidates: any LinkCandidateSource
    ) {
        _viewModel = StateObject(
            wrappedValue: TransactionReviewViewModel(batchId: batchId, api: api)
        )
        self.batchId = batchId
        self.images = images
        self.linkCandidates = linkCandidates
    }

    var body: some View {
        List {
            Section {
                Text("待确认 \(viewModel.pendingCount) 笔")
                    .font(.callout)
                    .accessibilityIdentifier("review.pending")
            }

            if !viewModel.missingAlerts.isEmpty {
                Section("异常提示") {
                    ForEach(Array(viewModel.missingAlerts.enumerated()), id: \.offset) { _, alert in
                        Text(Self.alertText(alert))
                            .font(.callout)
                            .foregroundStyle(.orange)
                            .accessibilityIdentifier("review.alerts")
                    }
                }
            }

            ForEach(viewModel.groups, id: \.category) { category in
                Section(category.category) {
                    ForEach(category.scopes, id: \.regionScope) { scope in
                        Text(scope.regionScope)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        ForEach(scope.regions, id: \.regionDetail) { region in
                            Text(region.regionDetail)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            ForEach(region.transactions, id: \.id) { transaction in
                                row(transaction, candidates: availableCandidates)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("复核")
        .errorBanner(bannerError)
        .refreshable {
            await viewModel.reload()
            await loadSourceCandidates()
        }
        .task {
            await viewModel.load()
            await loadSourceCandidates()
        }
    }

    private var bannerError: AppError? {
        if case let .failed(error) = viewModel.state {
            return error
        }
        return nil
    }

    /// 只缓存未过滤的候选；可用集合始终按当前 `groups` 派生，避免保存/刷新后过期。
    private func loadSourceCandidates() async {
        sourceCandidates = await linkCandidates.candidates(forBatchId: batchId)
    }

    /// 过滤掉本批已关联的凭证；仅做集合运算，不做角色或金额判断。
    /// 依赖 `viewModel.groups`，交易关联变化后自动重算；当前交易自己的凭证在详情页仍可回选。
    private var availableCandidates: [LinkCandidate] {
        let linked = linkedDocumentIds
        return sourceCandidates.filter { !linked.contains($0.id) }
    }

    private var linkedDocumentIds: Set<UUID> {
        Set(
            viewModel.groups
                .flatMap(\.scopes)
                .flatMap(\.regions)
                .flatMap(\.transactions)
                .flatMap(\.documents)
                .compactMap { UUID(uuidString: $0.id) }
        )
    }

    /// 行内金额与 flag 文案均由服务端返回，这里只原样展示。
    private func row(_ transaction: TransactionOut, candidates: [LinkCandidate]) -> some View {
        NavigationLink {
            TransactionDetailView(
                transaction: transaction,
                candidates: candidates,
                images: images
            ) { patch in
                await viewModel.update(transactionId: transaction.id, patch: patch)
                return viewModel.errorMessage
            }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(transaction.merchant)
                Text(transaction.claimAmount)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !transaction.flags.isEmpty {
                    Text(transaction.flags.joined(separator: "、"))
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .accessibilityIdentifier("review.flag.\(transaction.id)")
                }
            }
            .accessibilityElement(children: .contain)
        }
        .listRowBackground(
            viewModel.flaggedIds.contains(transaction.id)
                ? Color.orange.opacity(0.15)
                : nil
        )
    }

    /// 告警内容只做展示拼装，不解释规则。
    private static func alertText(_ value: JSONValue) -> String {
        return switch value {
        case let .string(message):
            message
        case let .integer(number):
            "\(number)"
        case let .number(number):
            "\(number)"
        case let .boolean(flag):
            flag ? "true" : "false"
        case let .array(items):
            items.map(alertText).joined(separator: "、")
        case let .object(fields):
            fields
                .sorted { $0.key < $1.key }
                .map { "\($0.key)：\(alertText($0.value))" }
                .joined(separator: "、")
        case .null:
            "未知异常"
        }
    }
}
