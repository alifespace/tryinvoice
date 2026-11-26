import Foundation
import XCTest
@testable import TryInvoice

final class TransactionReviewViewModelTests: XCTestCase {
    private let batchId = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
    private let verifiedId = "aaaaaaaa-0000-4000-8000-000000000001"
    private let flaggedId = "aaaaaaaa-0000-4000-8000-000000000002"
    private let plainId = "aaaaaaaa-0000-4000-8000-000000000003"
    private let unknownId = "aaaaaaaa-0000-4000-8000-0000000000ff"

    @MainActor
    func testLoadBuildsGroupsAlertsAndDerivedCounters() async {
        let api = StubReviewAPI()
        await api.enqueueList(.success(list([
            group("meals", [tx(verifiedId, verified: true), tx(flaggedId, flags: ["possible_duplicate"])]),
            group("travel", [tx(plainId, category: "travel")], region: "JP-TY"),
        ], alerts: [.string("missing_airport_transfer")], status: "review")))
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.groups.map(\.category), ["meals", "travel"])
        XCTAssertEqual(viewModel.groups[1].scopes[0].regions[0].transactions.first?.id, plainId)
        XCTAssertEqual(viewModel.missingAlerts, [.string("missing_airport_transfer")])
        XCTAssertEqual(viewModel.batchStatus, "review")
        XCTAssertEqual(viewModel.pendingCount, 2)
        XCTAssertEqual(viewModel.flaggedIds, [flaggedId])
        let calls = await api.listBatchIds()
        XCTAssertEqual(calls, [batchId])
    }

    @MainActor
    func testLoadFailureKeepsPreviousGroupsAndSetsFailedState() async {
        let api = StubReviewAPI()
        await api.enqueueList(.success(list([group("meals", [tx(flaggedId, flags: ["tip_included"])])])))
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()
        let loaded = viewModel.groups

        await api.enqueueList(.failure(.network))
        await viewModel.reload()

        XCTAssertEqual(viewModel.state, .failed(.network))
        XCTAssertEqual(viewModel.groups, loaded)
        XCTAssertEqual(viewModel.pendingCount, 1)
        XCTAssertEqual(viewModel.flaggedIds, [flaggedId])
        XCTAssertEqual(viewModel.errorMessage, AppError.network.userMessage)
    }

    @MainActor
    func testOptimisticPatchIsVisibleAndServerValueReconciles() async {
        let gate = Gate()
        let api = StubReviewAPI()
        let id = flaggedId
        await api.setGate(gate, for: id)
        await api.enqueueList(.success(list([group("meals", [tx(id, claim: "10.00", notes: "旧备注")])])))
        await api.enqueueUpdate(.success(
            tx(id, category: "travel", claim: "99.00", notes: "服务端备注", tip: "2.50", split: 2)
        ), for: id)
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()
        let patch = TransactionPatch(
            category: "travel", invoiceDocId: nil, notes: "新备注",
            receiptDocId: nil, splitCount: 2, tipAmount: .number(2.5)
        )
        let update = Task { await viewModel.update(transactionId: id, patch: patch) }
        await api.waitForUpdateCount(1)

        let pending = first(viewModel)
        XCTAssertEqual(pending?.notes, "新备注")
        XCTAssertEqual(pending?.category, "travel")
        XCTAssertEqual(pending?.splitCount, 2)
        XCTAssertEqual(pending?.tipAmount, "2.5")
        XCTAssertEqual(pending?.claimAmount, "10.00")

        await gate.release()
        await update.value
        let reconciled = first(viewModel)
        XCTAssertEqual(reconciled?.notes, "服务端备注")
        XCTAssertEqual(reconciled?.claimAmount, "99.00")
        XCTAssertEqual(reconciled?.tipAmount, "2.50")
        XCTAssertNil(viewModel.errorMessage)
        let calls = await api.receivedUpdates()
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.id, UUID(uuidString: id))
        XCTAssertEqual(calls.first?.patch, patch)
    }

    @MainActor
    func testFailedUpdateRollsBackAndShowsMessage() async {
        let api = StubReviewAPI()
        let id = flaggedId
        await api.enqueueList(.success(list([group("meals", [tx(id, claim: "10.00", notes: "原备注", tip: "1.00")])])))
        await api.enqueueUpdate(.failure(.server), for: id)
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()
        let before = first(viewModel)

        await viewModel.update(transactionId: id, patch: TransactionPatch(
            category: "travel", invoiceDocId: nil, notes: "未生效",
            receiptDocId: nil, splitCount: 3, tipAmount: .string("9.99")
        ))

        XCTAssertEqual(first(viewModel), before)
        XCTAssertEqual(viewModel.errorMessage, AppError.server.userMessage)
        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertEqual(viewModel.pendingCount, 1)
    }

    /// A 失败晚于 B 成功时，只回滚 A，不抹掉 B 已对账的服务端结果。
    @MainActor
    func testFailedUpdateRollsBackOnlyTargetAfterOtherTransactionSucceeded() async {
        let api = StubReviewAPI()
        let gateA = Gate()
        let gateB = Gate()
        await api.setGate(gateA, for: flaggedId)
        await api.setGate(gateB, for: plainId)
        await api.enqueueList(.success(list([
            group("meals", [tx(flaggedId, notes: "oldA"), tx(plainId, notes: "oldB")]),
        ])))
        await api.enqueueUpdate(.failure(.server), for: flaggedId)
        await api.enqueueUpdate(.success(tx(plainId, notes: "serverB")), for: plainId)
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()

        let updateA = Task { await viewModel.update(transactionId: flaggedId, patch: notesPatch("newA")) }
        let updateB = Task { await viewModel.update(transactionId: plainId, patch: notesPatch("newB")) }
        await api.waitForUpdateCount(2)
        XCTAssertEqual(transaction(flaggedId, in: viewModel)?.notes, "newA")
        XCTAssertEqual(transaction(plainId, in: viewModel)?.notes, "newB")

        await gateB.release()
        await updateB.value
        XCTAssertEqual(transaction(plainId, in: viewModel)?.notes, "serverB")

        await gateA.release()
        await updateA.value

        XCTAssertEqual(transaction(flaggedId, in: viewModel)?.notes, "oldA")
        XCTAssertEqual(transaction(plainId, in: viewModel)?.notes, "serverB")
        XCTAssertEqual(viewModel.errorMessage, AppError.server.userMessage)
    }

    /// A 先失败写入提示，B 随后成功返回时必须清除提示。
    @MainActor
    func testSuccessClearsErrorLeftByOtherTransactionFailure() async {
        let api = StubReviewAPI()
        let gateA = Gate()
        let gateB = Gate()
        await api.setGate(gateA, for: flaggedId)
        await api.setGate(gateB, for: plainId)
        await api.enqueueList(.success(list([
            group("meals", [tx(flaggedId, notes: "oldA"), tx(plainId, notes: "oldB")]),
        ])))
        await api.enqueueUpdate(.failure(.server), for: flaggedId)
        await api.enqueueUpdate(.success(tx(plainId, notes: "serverB")), for: plainId)
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()

        let updateA = Task { await viewModel.update(transactionId: flaggedId, patch: notesPatch("newA")) }
        let updateB = Task { await viewModel.update(transactionId: plainId, patch: notesPatch("newB")) }
        await api.waitForUpdateCount(2)

        await gateA.release()
        await updateA.value
        XCTAssertEqual(viewModel.errorMessage, AppError.server.userMessage)
        XCTAssertEqual(transaction(flaggedId, in: viewModel)?.notes, "oldA")

        await gateB.release()
        await updateB.value

        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(transaction(plainId, in: viewModel)?.notes, "serverB")
        XCTAssertEqual(transaction(flaggedId, in: viewModel)?.notes, "oldA")
    }

    @MainActor
    func testConcurrentUpdateForSameTransactionIsRejected() async {
        let gate = Gate()
        let api = StubReviewAPI()
        let id = flaggedId
        await api.setGate(gate, for: id)
        await api.enqueueList(.success(list([group("meals", [tx(id, notes: "原备注")])])))
        await api.enqueueUpdate(.success(tx(id, notes: "第一次")), for: id)
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()
        let firstUpdate = Task { await viewModel.update(transactionId: id, patch: notesPatch("第一次")) }
        await api.waitForUpdateCount(1)

        await viewModel.update(transactionId: id, patch: notesPatch("第二次"))

        XCTAssertEqual(first(viewModel)?.notes, "第一次")
        let calls = await api.receivedUpdates()
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.patch.notes, "第一次")

        await gate.release()
        await firstUpdate.value
        XCTAssertEqual(first(viewModel)?.notes, "第一次")
    }

    @MainActor
    func testInvalidOrUnknownIdentifierSendsNoRequest() async {
        let api = StubReviewAPI()
        await api.enqueueList(.success(list([group("meals", [tx(flaggedId)])])))
        let viewModel = TransactionReviewViewModel(batchId: batchId, api: api)
        await viewModel.load()

        await viewModel.update(transactionId: unknownId, patch: notesPatch("任意"))
        XCTAssertNil(viewModel.errorMessage)
        await viewModel.update(transactionId: "not-a-uuid", patch: notesPatch("任意"))
        XCTAssertEqual(viewModel.errorMessage, AppError.validation.userMessage)
        let calls = await api.receivedUpdates()
        XCTAssertTrue(calls.isEmpty)
    }

    @MainActor
    private func first(_ viewModel: TransactionReviewViewModel) -> TransactionOut? {
        viewModel.groups.first?.scopes.first?.regions.first?.transactions.first
    }

    @MainActor
    private func transaction(_ id: String, in viewModel: TransactionReviewViewModel) -> TransactionOut? {
        viewModel.groups
            .flatMap(\.scopes)
            .flatMap(\.regions)
            .flatMap(\.transactions)
            .first { $0.id == id }
    }

    private func notesPatch(_ notes: String) -> TransactionPatch {
        TransactionPatch(
            category: nil, invoiceDocId: nil, notes: notes,
            receiptDocId: nil, splitCount: nil, tipAmount: nil
        )
    }

    private func list(
        _ groups: [CategoryGroup],
        alerts: [JSONValue] = [],
        status: String = "review"
    ) -> TransactionListOut {
        TransactionListOut(
            batchId: batchId.uuidString.lowercased(), groups: groups,
            missingAlerts: alerts, status: status
        )
    }

    private func group(
        _ category: String,
        _ transactions: [TransactionOut],
        region: String = "US-CA"
    ) -> CategoryGroup {
        CategoryGroup(category: category, scopes: [ScopeGroup(regionScope: "in_scope", regions: [
            RegionGroup(regionDetail: region, transactions: transactions),
        ])])
    }

    private func tx(
        _ id: String, category: String = "meals", claim: String = "10.00",
        notes: String = "", tip: String = "0", split: Int = 1,
        verified: Bool = false, flags: [String] = []
    ) -> TransactionOut {
        TransactionOut(
            airportDirection: nil, airportIata: nil, category: category, claimAmount: claim,
            currency: "USD", documents: [], flags: flags, id: id, isUserVerified: verified,
            merchant: "Synthetic Merchant", notes: notes, originalAmount: "10.00",
            paymentStatus: "paid", regionDetail: "US-CA", regionScope: "in_scope",
            splitCount: split, tipAmount: tip, txDate: "2026-01-02"
        )
    }
}

/// 记录调用并按交易 id 返回结果；每个 id 的 `Gate` 只拦第一次更新，
/// 这样若并发闸门被移除，第二次调用会立即拿到空队列失败而不会挂住测试。
actor StubReviewAPI: TransactionReviewAPI {
    private var lists: [Result<TransactionListOut, AppError>] = []
    private var updates: [String: [Result<TransactionOut, AppError>]] = [:]
    private var gates: [String: Gate] = [:]
    private var listed: [UUID] = []
    private var received: [(id: UUID, patch: TransactionPatch)] = []
    private var waiters: [(count: Int, continuation: CheckedContinuation<Void, Never>)] = []

    func enqueueList(_ result: Result<TransactionListOut, AppError>) { lists.append(result) }

    func enqueueUpdate(_ result: Result<TransactionOut, AppError>, for id: String) {
        updates[key(id), default: []].append(result)
    }

    func setGate(_ gate: Gate, for id: String) { gates[key(id)] = gate }

    func listBatchIds() -> [UUID] { listed }
    func receivedUpdates() -> [(id: UUID, patch: TransactionPatch)] { received }

    /// 等到第 `count` 次更新已经进入 API 桩，用于在放行前确认请求确实在途。
    func waitForUpdateCount(_ count: Int) async {
        guard received.count < count else { return }
        await withCheckedContinuation { waiters.append((count, $0)) }
    }

    func listTransactions(batchId: UUID) async throws -> TransactionListOut {
        listed.append(batchId)
        guard let next = lists.first else { throw AppError.unknown }
        lists.removeFirst()
        return try next.get()
    }

    func updateTransaction(id: UUID, _ payload: TransactionPatch) async throws -> TransactionOut {
        received.append((id, payload))
        let pending = waiters
        waiters = pending.filter { $0.count > received.count }
        pending.filter { $0.count <= received.count }.forEach { $0.continuation.resume() }

        let identifier = key(id.uuidString)
        if let gate = gates.removeValue(forKey: identifier) { await gate.wait() }
        guard var queue = updates[identifier], !queue.isEmpty else { throw AppError.unknown }
        let next = queue.removeFirst()
        updates[identifier] = queue
        return try next.get()
    }

    private func key(_ id: String) -> String { id.lowercased() }
}

actor Gate {
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var open = false

    func wait() async {
        guard !open else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        open = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}
