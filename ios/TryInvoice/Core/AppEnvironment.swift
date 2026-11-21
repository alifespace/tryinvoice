import Foundation

struct AppEnvironment {
    let configuration: AppConfiguration?
    let error: AppError?

    init(configuration: AppConfiguration) {
        self.configuration = configuration
        self.error = nil
    }

    init(error: AppError) {
        self.configuration = nil
        self.error = error
    }

    static func live(bundle: Bundle = .main) -> AppEnvironment {
        switch AppConfiguration.load(from: bundle.infoDictionary ?? [:]) {
        case let .success(configuration):
            AppEnvironment(configuration: configuration)
        case let .failure(error):
            AppEnvironment(error: error)
        }
    }
}
