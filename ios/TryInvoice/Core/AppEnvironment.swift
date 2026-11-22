import Foundation

struct AppEnvironment {
    let configuration: AppConfiguration?
    let error: AppError?
    let authService: (any AuthService)?
    let deleteAccount: (() async throws -> Void)?
    let authConfigurationError: AppError?

    init(
        configuration: AppConfiguration,
        authService: (any AuthService)? = nil,
        deleteAccount: (() async throws -> Void)? = nil,
        authConfigurationError: AppError? = nil
    ) {
        self.configuration = configuration
        self.error = nil
        self.authService = authService
        self.deleteAccount = deleteAccount
        self.authConfigurationError =
            authConfigurationError ?? configuration.supabaseConfigurationError
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
    }
#endif

    init(error: AppError) {
        self.configuration = nil
        self.error = error
        self.authService = nil
        self.deleteAccount = nil
        self.authConfigurationError = error
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
            return AppEnvironment(
                configuration: configuration,
                authService: authService,
                deleteAccount: { try await apiClient.deleteAccount() }
            )
        case let .failure(error):
            return AppEnvironment(error: error)
        }
    }
}
