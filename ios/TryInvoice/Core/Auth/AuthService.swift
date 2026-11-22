import Foundation

struct AuthSession: Equatable, Sendable {
    let userEmail: String
}

protocol AuthService: Sendable {
    func restoreSession() async -> AuthSession?
    func signIn(email: String, password: String) async throws -> AuthSession
    func signOut() async throws
    func clearLocalSession() async throws
}

#if DEBUG
actor UITestAuthService: AuthService {
    private var session: AuthSession?

    init(signedIn: Bool) {
        session = signedIn ? AuthSession(userEmail: "test@example.test") : nil
    }

    func restoreSession() async -> AuthSession? {
        session
    }

    func signIn(email: String, password: String) async throws -> AuthSession {
        let result = AuthSession(userEmail: email)
        session = result
        return result
    }

    func signOut() async throws {
        session = nil
    }

    func clearLocalSession() async throws {
        session = nil
    }

    static func from(arguments: [String]) -> UITestAuthService? {
        guard
            let index = arguments.firstIndex(of: "-UITestAuthStub"),
            arguments.indices.contains(index + 1)
        else {
            return nil
        }
        switch arguments[index + 1] {
        case "signedOut":
            return UITestAuthService(signedIn: false)
        case "signedIn":
            return UITestAuthService(signedIn: true)
        default:
            return nil
        }
    }
}
#endif
