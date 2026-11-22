import Foundation

struct AppEnvironment {
    let configuration: AppConfiguration?
    let error: AppError?
    let authService: (any AuthService)?
    let deleteAccount: (() async throws -> Void)?
    let authConfigurationError: AppError?
    let uploadQueue: UploadQueue?
    let connectivity: (any ConnectivityMonitor)?

    init(
        configuration: AppConfiguration,
        authService: (any AuthService)? = nil,
        deleteAccount: (() async throws -> Void)? = nil,
        authConfigurationError: AppError? = nil,
        uploadQueue: UploadQueue? = nil,
        connectivity: (any ConnectivityMonitor)? = nil
    ) {
        self.configuration = configuration
        self.error = nil
        self.authService = authService
        self.deleteAccount = deleteAccount
        self.authConfigurationError =
            authConfigurationError ?? configuration.supabaseConfigurationError
        self.uploadQueue = uploadQueue
        self.connectivity = connectivity
    }

#if DEBUG
    init(
        uiTestConfiguration configuration: AppConfiguration,
        authService: any AuthService,
        deleteAccount: (() async throws -> Void)?
    ) {
        self.configuration = configuration
        self.error = nil
        self.authService = authService
        self.deleteAccount = deleteAccount
        self.authConfigurationError = nil
        self.uploadQueue = nil
        self.connectivity = nil
    }
#endif

    init(error: AppError) {
        self.configuration = nil
        self.error = error
        self.authService = nil
        self.deleteAccount = nil
        self.authConfigurationError = error
        self.uploadQueue = nil
        self.connectivity = nil
    }

    static func live(
        bundle: Bundle = .main,
        arguments: [String] = ProcessInfo.processInfo.arguments
    ) -> AppEnvironment {
        switch AppConfiguration.load(from: bundle.infoDictionary ?? [:]) {
        case let .success(configuration):
            #if DEBUG
            if let authStub = UITestAuthService.from(arguments: arguments) {
                return AppEnvironment(
                    uiTestConfiguration: configuration,
                    authService: authStub,
                    deleteAccount: {}
                )
            }
            #endif

            guard
                let supabaseURL = configuration.supabaseURL,
                let supabaseAnonKey = configuration.supabaseAnonKey
            else {
                return AppEnvironment(configuration: configuration)
            }

            let authService = SupabaseAuthService(
                supabaseURL: supabaseURL,
                anonKey: supabaseAnonKey
            )
            guard let apiBaseURL = configuration.apiBaseURL else {
                return AppEnvironment(
                    configuration: configuration,
                    authService: authService,
                    authConfigurationError: .configurationMissing("API_BASE_URL")
                )
            }
            let apiClient = APIClient(baseURL: apiBaseURL, tokenProvider: authService)
            // 上传队列依赖 SwiftData 主上下文，只能在主线程装配；无法装配时保持 nil，不影响登录。
            let services = Thread.isMainThread
                ? MainActor.assumeIsolated { makeUploadServices(api: apiClient) }
                : nil
            return AppEnvironment(
                configuration: configuration,
                authService: authService,
                deleteAccount: { try await apiClient.deleteAccount() },
                uploadQueue: services?.queue,
                connectivity: services?.monitor
            )
        case let .failure(error):
            return AppEnvironment(error: error)
        }
    }

    /// 磁盘容器、RawVault 或队列任一不可用都返回 nil，绝不因离线存储拖垮应用启动。
    @MainActor
    private static func makeUploadServices(api: any UploadAPI) -> UploadServices? {
        guard let storeURL = uploadStoreURL else {
            return nil
        }
        do {
            let container = try LocalStore.makeContainer(storeURL: storeURL)
            let store = LocalStore(
                container: container,
                vault: try RawVault(root: RawVault.defaultRoot)
            )
            return UploadServices(
                queue: UploadQueue(store: store, api: api),
                monitor: NWPathConnectivityMonitor()
            )
        } catch {
            return nil
        }
    }

    private static var uploadStoreURL: URL? {
        FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first?
            .appendingPathComponent("TryInvoice", isDirectory: true)
            .appendingPathComponent("OfflineStore.sqlite")
    }
}

private struct UploadServices: Sendable {
    let queue: UploadQueue
    let monitor: any ConnectivityMonitor
}
