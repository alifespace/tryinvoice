import Foundation

struct AppConfiguration: Equatable {
    let apiBaseURL: URL?
    let supabaseURL: URL?
    let supabaseAnonKey: String?

    var supabaseConfigurationError: AppError? {
        guard supabaseURL != nil else {
            return .configurationMissing("SUPABASE_URL")
        }
        guard supabaseAnonKey != nil else {
            return .configurationMissing("SUPABASE_ANON_KEY")
        }
        return nil
    }

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

        let supabaseURL: URL?
        if let rawSupabaseURL = info["SUPABASE_URL"] {
            guard let value = rawSupabaseURL as? String else {
                return .failure(.configurationInvalid("SUPABASE_URL"))
            }
            let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmedValue.isEmpty {
                supabaseURL = nil
            } else {
                guard
                    let components = URLComponents(string: trimmedValue),
                    let scheme = components.scheme?.lowercased(),
                    let host = components.host?.lowercased(),
                    !host.isEmpty
                else {
                    return .failure(.configurationInvalid("SUPABASE_URL"))
                }
                let isSecureSupabaseURL = scheme == "https"
                let isLocalSupabaseURL = scheme == "http"
                    && (host == "localhost" || host == "127.0.0.1")
                guard
                    (isSecureSupabaseURL || isLocalSupabaseURL),
                    let parsedURL = components.url
                else {
                    return .failure(.configurationInvalid("SUPABASE_URL"))
                }
                supabaseURL = parsedURL
            }
        } else {
            supabaseURL = nil
        }

        let supabaseAnonKey: String?
        if let rawSupabaseAnonKey = info["SUPABASE_ANON_KEY"] {
            guard let value = rawSupabaseAnonKey as? String else {
                return .failure(.configurationInvalid("SUPABASE_ANON_KEY"))
            }
            let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
            supabaseAnonKey = trimmedValue.isEmpty ? nil : trimmedValue
        } else {
            supabaseAnonKey = nil
        }

        return .success(
            AppConfiguration(
                apiBaseURL: url,
                supabaseURL: supabaseURL,
                supabaseAnonKey: supabaseAnonKey
            )
        )
    }
}
