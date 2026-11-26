import SwiftUI

/// 批次列表的展示引用；id 使用服务端批次 ID。
struct ReviewBatchRef: Identifiable, Equatable {
    let id: UUID
    let title: String
}

/// 已同步批次入口；只读取本地批次元数据，不发起网络请求。
struct BatchListView: View {
    let store: LocalStore
    let services: ReviewServices

    @State private var batches: [ReviewBatchRef] = []

    init(store: LocalStore, services: ReviewServices) {
        self.store = store
        self.services = services
    }

    var body: some View {
        Group {
            if batches.isEmpty {
                Text("暂无已同步批次")
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("review.empty")
            } else {
                List(batches) { batch in
                    NavigationLink(batch.title) {
                        TransactionReviewView(
                            batchId: batch.id,
                            api: services.reviewAPI,
                            images: services.images,
                            linkCandidates: services.linkCandidates
                        )
                    }
                    .accessibilityIdentifier("review.batch.\(batch.id.uuidString)")
                }
            }
        }
        .navigationTitle("复核")
        .task { await reload() }
    }

    @MainActor
    private func reload() {
        batches = store.syncedBatches().compactMap { batch in
            batch.serverId.map { ReviewBatchRef(id: $0, title: batch.title) }
        }
    }
}
