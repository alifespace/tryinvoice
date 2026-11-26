import Foundation

/// 复核页所需的最小服务端能力；协议只描述读写，不承载任何金额或分组规则。
protocol TransactionReviewAPI: Sendable {
    func listTransactions(batchId: UUID) async throws -> TransactionListOut
    func updateTransaction(id: UUID, _ payload: TransactionPatch) async throws -> TransactionOut
}

extension APIClient: TransactionReviewAPI {}
