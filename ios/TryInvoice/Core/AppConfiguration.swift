import Foundation

struct AppConfiguration: Equatable {
    let apiBaseURL: URL?

    static func load(from info: [String: Any]) -> Result<AppConfiguration, AppError> {
        guard let rawValue = info["API_BASE_URL"] else {
            return .failure(.configurationMissing("API_BASE_URL"))
        }
        guard let value = rawValue as? String else {
            return .failure(.configurationInvalid("API_BASE_URL"))
        }

        let urlValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !urlValue.isEmpty else {
            return .failure(.configurationMissing("API_BASE_URL"))
        }
        guard
            let components = URLComponents(string: urlValue),
            let scheme = components.scheme?.lowercased(),
            let host = components.host?.lowercased(),
            !host.isEmpty
        else {
            return .failure(.configurationInvalid("API_BASE_URL"))
        }

        let isLocalHost = host == "localhost" || host == "127.0.0.1"
        let isSecure = scheme == "https"
        let isLocalDevelopment = scheme == "http" && isLocalHost
        guard (isSecure || isLocalDevelopment), let url = components.url else {
            return .failure(.configurationInvalid("API_BASE_URL"))
        }

        return .success(AppConfiguration(apiBaseURL: url))
    }
}
