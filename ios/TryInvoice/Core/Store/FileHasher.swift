import CryptoKit
import Foundation

/// 流式 SHA-256，用于秒传与幂等重试。输出固定为小写十六进制。
enum FileHasher {
    static let chunkSize = 1_048_576

    static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            guard let chunk = try handle.read(upToCount: chunkSize), !chunk.isEmpty else {
                break
            }
            hasher.update(data: chunk)
        }
        return hex(hasher.finalize())
    }

    static func sha256(of data: Data) -> String {
        hex(SHA256.hash(data: data))
    }

    private static func hex(_ digest: SHA256.Digest) -> String {
        digest.map { String(format: "%02x", $0) }.joined()
    }
}
