import Combine
import Foundation

/// 复核页的展示状态与乐观更新；只读写服务端数据，不计算 claimAmount、均分、小费、去重或配对。
@MainActor
final class TransactionReviewViewModel: ObservableObject {
    enum State: Equatable {
        case idle
        case loading
        case loaded
        case failed(AppError)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var groups: [CategoryGroup] = []
    @Published private(set) var missingAlerts: [JSONValue] = []
    @Published private(set) var batchStatus: String = ""
    @Published private(set) var errorMessage: String?
    /// 仅统计未人工确认的交易条数，不涉及金额。
    @Published private(set) var pendingCount: Int = 0
    @Published private(set) var flaggedIds: Set<String> = []

    private let batchId: UUID
    private let api: any TransactionReviewAPI
    private var inFlight: Set<String> = []

    init(batchId: UUID, api: any TransactionReviewAPI) {
        self.batchId = batchId
        self.api = api
    }

    /// 失败时保留上一次成功数据，只切换状态与本页提示。
    func load() async {
        state = .loading
        do {
            let list = try await api.listTransactions(batchId: batchId)
            groups = list.groups
            missingAlerts = list.missingAlerts
            batchStatus = list.status
            recomputeDerived()
            state = .loaded
            errorMessage = nil
        } catch {
            let appError = Self.appError(error)
            state = .failed(appError)
            errorMessage = appError.userMessage
        }
    }

    func reload() async {
        await load()
    }

    /// 乐观写入 patch 中显式给出的字段，成功后以服务端返回整体替换，失败只回滚目标交易到修改前快照。
    func update(transactionId: String, patch: TransactionPatch) async {
        guard let identifier = UUID(uuidString: transactionId) else {
            errorMessage = AppError.validation.userMessage
            return
        }
        guard
            let original = transactions.first(where: { $0.id == transactionId }),
            !inFlight.contains(transactionId)
        else {
            return
        }

        inFlight.insert(transactionId)
        defer { inFlight.remove(transactionId) }

        replace(Self.applying(patch, to: original))
        do {
            let updated = try await api.updateTransaction(id: identifier, patch)
            replace(updated)
            errorMessage = nil
        } catch {
            // 只恢复本次修改的交易，保留其他交易已确认的服务端结果。
            replace(original)
            errorMessage = Self.appError(error).userMessage
        }
    }

    private var transactions: [TransactionOut] {
        groups.flatMap { category in
            category.scopes.flatMap { scope in
                scope.regions.flatMap(\.transactions)
            }
        }
    }

    private func recomputeDerived() {
        let all = transactions
        pendingCount = all.filter { !$0.isUserVerified }.count
        flaggedIds = Set(all.filter { !$0.flags.isEmpty }.map(\.id))
    }

    private func replace(_ updated: TransactionOut) {
        groups = Self.replacing(updated, in: groups)
        recomputeDerived()
    }

    private static func replacing(
        _ updated: TransactionOut,
        in groups: [CategoryGroup]
    ) -> [CategoryGroup] {
        groups.map { category in
            CategoryGroup(
                category: category.category,
                scopes: category.scopes.map { scope in
                    ScopeGroup(
                        regionScope: scope.regionScope,
                        regions: scope.regions.map { region in
                            RegionGroup(
                                regionDetail: region.regionDetail,
                                transactions: region.transactions.map {
                                    $0.id == updated.id ? updated : $0
                                }
                            )
                        }
                    )
                }
            )
        }
    }

    /// 只复制 patch 中已设置的展示字段，claimAmount 等金额字段始终沿用服务端原值。
    private static func applying(
        _ patch: TransactionPatch,
        to original: TransactionOut
    ) -> TransactionOut {
        TransactionOut(
            airportDirection: original.airportDirection,
            airportIata: original.airportIata,
            category: patch.category ?? original.category,
            claimAmount: original.claimAmount,
            currency: original.currency,
            documents: original.documents,
            flags: original.flags,
            id: original.id,
            isUserVerified: original.isUserVerified,
            merchant: original.merchant,
            notes: patch.notes ?? original.notes,
            originalAmount: original.originalAmount,
            paymentStatus: original.paymentStatus,
            regionDetail: original.regionDetail,
            regionScope: original.regionScope,
            splitCount: patch.splitCount ?? original.splitCount,
            tipAmount: tipDisplay(patch.tipAmount) ?? original.tipAmount,
            txDate: original.txDate
        )
    }

    /// 仅供请求在途时显示；非字符串/数字的小费补丁不改变本地显示。
    private static func tipDisplay(_ value: JSONValue?) -> String? {
        switch value {
        case let .string(text):
            return text
        case let .integer(number):
            return String(number)
        case let .number(number):
            guard number.rounded() == number, number.magnitude < 1e15 else {
                return String(number)
            }
            return String(Int(number))
        default:
            return nil
        }
    }

    private static func appError(_ error: Error) -> AppError {
        (error as? AppError) ?? .unknown
    }
}
