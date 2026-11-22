import Auth
import Foundation

final class SupabaseAuthService: AuthService, AccessTokenProvider, @unchecked Sendable {
    private let client: AuthClient
    private let localStorage: any AuthLocalStorage
    private static let sessionStorageKey = "supabase.session"

    init(
        supabaseURL: URL,
        anonKey: String,
        localStorage: any AuthLocalStorage = AuthClient.Configuration.defaultLocalStorage
    ) {
        let authURL = supabaseURL.appendingPathComponent("auth/v1")
        self.localStorage = localStorage
        client = AuthClient(
            url: authURL,
            headers: ["apikey": anonKey],
            localStorage: localStorage
        )
    }

    func restoreSession() async -> AuthSession? {
        do {
            return try await makeSession(from: client.session)
        } catch {
            return nil
        }
    }

    func signIn(email: String, password: String) async throws -> AuthSession {
        do {
            let session = try await client.signIn(email: email, password: password)
            return try makeSession(from: session)
        } catch {
            throw Self.mapError(error)
        }
    }

    func signOut() async throws {
        do {
            try await client.signOut()
        } catch {
            throw Self.mapError(error)
        }
    }

    func clearLocalSession() async throws {
        // The SDK's signOut(.local) still validates/refreshes the session and calls /logout.
        // Remove the SDK-owned persisted session directly so deletion does not depend on either.
        do {
            try localStorage.remove(key: Self.sessionStorageKey)
        } catch {
            // Fail closed if deletion itself fails: the SDK cannot decode this tombstone as a
            // session, so the old credentials cannot be restored. Propagate a failed overwrite.
            try localStorage.store(key: Self.sessionStorageKey, value: Data())
        }
    }

    func accessToken() async throws -> String {
        do {
            return try await client.session.accessToken
        } catch {
            throw Self.mapError(error)
        }
    }

    func refreshedAccessToken() async throws -> String {
        do {
            return try await client.refreshSession().accessToken
        } catch {
            throw Self.mapError(error)
        }
    }

    static func mapError(_ error: Error) -> AppError {
        if let appError = error as? AppError {
            return appError
        }
        if error is URLError {
            return .network
        }

        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain {
            return .network
        }
        if let underlying = nsError.userInfo[NSUnderlyingErrorKey] as? Error {
            let mapped = mapError(underlying)
            if mapped == .network || mapped == .unauthorized {
                return mapped
            }
        }

        let diagnostic = "\(nsError.domain) \(nsError.code) \(error)"
            .lowercased()
        if [400, 401, 403].contains(nsError.code)
            || ["invalid login credentials", "invalid credentials", "invalid_grant"]
                .contains(where: diagnostic.contains)
            || diagnostic.contains("statuscode: 400")
            || diagnostic.contains("statuscode: 401")
            || diagnostic.contains("statuscode: 403")
            || diagnostic.contains("status code: 400")
            || diagnostic.contains("status code: 401")
            || diagnostic.contains("status code: 403")
        {
            return .unauthorized
        }
        return .unknown
    }

    private func makeSession(from session: Session) throws -> AuthSession {
        guard let email = session.user.email, !email.isEmpty else {
            throw AppError.unauthorized
        }
        return AuthSession(userEmail: email)
    }
}

#if DEBUG
final class InMemoryAuthLocalStorage: AuthLocalStorage, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Data] = [:]
    private let shouldFailRemovals: Bool
    private var shouldFailStores = false

    init(failRemovals: Bool = false) { shouldFailRemovals = failRemovals }

    func seedSession(email: String) throws {
        let now = Date()
        let user = User(
            id: UUID(), appMetadata: [:], userMetadata: [:], aud: "authenticated",
            email: email, createdAt: now, updatedAt: now
        )
        let session = Session(
            accessToken: "synthetic-access-token", tokenType: "bearer", expiresIn: 3_600,
            expiresAt: now.addingTimeInterval(3_600).timeIntervalSince1970,
            refreshToken: "synthetic-refresh-token", user: user
        )
        let value = try AuthClient.Configuration.jsonEncoder.encode(
            TestStoredSession(session: session, expirationDate: now.addingTimeInterval(3_600))
        )
        try store(key: "supabase.session", value: value)
    }

    func failFutureStores() { synchronized { shouldFailStores = true } }

    func hasStoredSession() throws -> Bool { try retrieve(key: "supabase.session") != nil }

    func store(key: String, value: Data) throws {
        try synchronized {
            guard !shouldFailStores else { throw NSError(domain: "Synthetic.Storage", code: 2) }
            values[key] = value
        }
    }

    func retrieve(key: String) throws -> Data? { synchronized { values[key] } }

    func remove(key: String) throws {
        try synchronized {
            guard !shouldFailRemovals else { throw NSError(domain: "Synthetic.Storage", code: 1) }
            values[key] = nil
        }
    }

    private func synchronized<T>(_ operation: () throws -> T) rethrows -> T {
        lock.lock()
        defer { lock.unlock() }
        return try operation()
    }
}

private struct TestStoredSession: Encodable {
    let session: Session
    let expirationDate: Date
}
#endif
