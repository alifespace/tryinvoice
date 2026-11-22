import Foundation
import Network

/// 把网络可达性暴露为异步流；`true` 表示可以发起上传。
protocol ConnectivityMonitor: Sendable {
    func updates() -> AsyncStream<Bool>
}

/// 基于 `NWPathMonitor` 的生产实现，流终止时停止监控。
struct NWPathConnectivityMonitor: ConnectivityMonitor {
    func updates() -> AsyncStream<Bool> {
        AsyncStream { continuation in
            let monitor = NWPathMonitor()
            monitor.pathUpdateHandler = { path in
                continuation.yield(path.status == .satisfied)
            }
            continuation.onTermination = { _ in monitor.cancel() }
            monitor.start(queue: DispatchQueue(label: "com.tryinvoice.connectivity"))
        }
    }
}
