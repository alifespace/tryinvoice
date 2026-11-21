import SwiftUI

struct RootView: View {
    let environment: AppEnvironment

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Text("TryInvoice")
                    .font(.largeTitle)
                Text("发票管理即将开始")
                    .font(.body)
            }
            .navigationTitle("首页")
            .errorBanner(environment.error)
        }
    }
}
