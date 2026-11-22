import Foundation

protocol AccessTokenProvider: Sendable {
    func accessToken() async throws -> String
    func refreshedAccessToken() async throws -> String
}
