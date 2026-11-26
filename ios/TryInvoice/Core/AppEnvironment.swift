import Foundation

struct AppEnvironment {
    let configuration: AppConfiguration?
    let error: AppError?
    let authService: (any AuthService)?
    let deleteAccount: (() async throws -> Void)?
    let authConfigurationError: AppError?
    let uploadQueue: UploadQueue?
    let connectivity: (any ConnectivityMonitor)?
    let importPipeline: ImportPipeline?
    let captureStore: LocalStore?
    let reviewServices: ReviewServices?

    init(
        configuration: AppConfiguration,
        authService: (any AuthService)? = nil,
        deleteAccount: (() async throws -> Void)? = nil,
        authConfigurationError: AppError? = nil,
        uploadQueue: UploadQueue? = nil,
        connectivity: (any ConnectivityMonitor)? = nil,
        importPipeline: ImportPipeline? = nil,
        captureStore: LocalStore? = nil,
        reviewServices: ReviewServices? = nil
    ) {
        self.configuration = configuration
        self.error = nil
        self.authService = authService
        self.deleteAccount = deleteAccount
        self.authConfigurationError =
            authConfigurationError ?? configuration.supabaseConfigurationError
        self.uploadQueue = uploadQueue
        self.connectivity = connectivity
        self.importPipeline = importPipeline
        self.captureStore = captureStore
        self.reviewServices = reviewServices
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
        let capture = Thread.isMainThread
            ? MainActor.assumeIsolated { Self.makeUITestCaptureServices() }
            : nil
        self.importPipeline = capture?.pipeline
        self.captureStore = capture?.store
        self.reviewServices = capture?.review
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
        self.importPipeline = nil
        self.captureStore = nil
        self.reviewServices = nil
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
            let reviewServices = services.map { upload in
                ReviewServices(
                    reviewAPI: apiClient,
                    templateAPI: apiClient,
                    images: StoreDocumentImageSource(store: upload.store),
                    linkCandidates: StoreLinkCandidateSource(store: upload.store)
                )
            }
            return AppEnvironment(
                configuration: configuration,
                authService: authService,
                deleteAccount: { try await apiClient.deleteAccount() },
                uploadQueue: services?.queue,
                connectivity: services?.monitor,
                importPipeline: services?.pipeline,
                captureStore: services?.store,
                reviewServices: reviewServices
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
                monitor: NWPathConnectivityMonitor(),
                store: store,
                pipeline: ImportPipeline(store: store, engine: VisionOCREngine())
            )
        } catch {
            return nil
        }
    }

#if DEBUG
    /// UI 测试用内存容器 + 临时 RawVault；装配失败返回 nil，不影响登录与首页启动。
    @MainActor
    private static func makeUITestCaptureServices() -> (
        pipeline: ImportPipeline,
        store: LocalStore,
        review: ReviewServices
    )? {
        let vaultRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("TryInvoiceUITestVault-\(UUID().uuidString)", isDirectory: true)
        guard let container = try? LocalStore.makeContainer(storeURL: nil),
              let vault = try? RawVault(root: vaultRoot) else {
            return nil
        }
        let store = LocalStore(container: container, vault: vault)
        // 种入一条已回填服务端 ID 的合成批次，使复核入口在 UI 测试中可达。
        if let batch = try? store.createBatch(
            title: UITestReviewAPI.batchTitle,
            expenseType: "meal",
            templateId: UUID(),
            batchDate: "2026-11-20"
        ) {
            _ = try? store.backfill(serverId: UITestReviewAPI.batchId, for: batch)
        }
        return (
            ImportPipeline(store: store, engine: VisionOCREngine()),
            store,
            ReviewServices(
                reviewAPI: UITestReviewAPI(),
                templateAPI: UITestTemplateAPI(),
                images: UITestImageSource(),
                linkCandidates: UITestLinkCandidateSource()
            )
        )
    }
#endif

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
    let store: LocalStore
    let pipeline: ImportPipeline
}
