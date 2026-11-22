import Foundation
import XCTest
@testable import TryInvoice

final class DocumentUploadAPITests: XCTestCase {
    override func tearDown() {
        UploadURLProtocol.install { _ in .status(500) }
        super.tearDown()
    }
    func testPresignRefreshesAndEncodesContractPayload() async throws {
        var calls = 0
        UploadURLProtocol.install { _ in
            calls += 1
            return calls == 1 ? .status(401) : .status(200, """
            {"already_exists":false,"document_id":"doc-1",\
            "upload_url":"https://storage.example.test/object",\
            "upload_method":"PUT","expires_in":300}
            """)
        }
        let provider = UploadTokenProvider(
            accessTokens: ["expired-token"], refreshedTokens: ["refreshed-token"]
        )
        let batchId = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
        let result = try await makeClient(provider).presign(
            batchId: batchId,
            PresignRequest(
                filename: "synthetic.jpg", mimeType: "image/jpeg",
                sha256Hash: String(repeating: "a", count: 64)
            )
        )
        let request = try XCTUnwrap(UploadURLProtocol.lastRequest)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try XCTUnwrap(request.httpBody))
                as? [String: String]
        )
        XCTAssertEqual(
            UploadURLProtocol.requests.map { $0.value(forHTTPHeaderField: "Authorization") },
            ["Bearer expired-token", "Bearer refreshed-token"]
        )
        XCTAssertEqual(result.documentId, "doc-1")
        XCTAssertEqual(
            request.url?.path,
            "/api/v1/batches/11111111-1111-1111-1111-111111111111/documents/presign"
        )
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(object["filename"], "synthetic.jpg")
        XCTAssertEqual(object["mime_type"], "image/jpeg")
        XCTAssertEqual(object["sha256_hash"], String(repeating: "a", count: 64))
        UploadURLProtocol.install { _ in .status(200, """
        {"already_exists":true,"document_id":"doc-2","upload_url":null,\
        "upload_method":null,"expires_in":null}
        """) }
        let existing = try await makeClient(UploadTokenProvider(accessTokens: ["t"])).presign(
            batchId: batchId,
            PresignRequest(filename: "synthetic.jpg", mimeType: "image/jpeg",
                           sha256Hash: String(repeating: "a", count: 64))
        )
        XCTAssertEqual(existing, PresignResponse(
            alreadyExists: true, documentId: "doc-2", expiresIn: nil,
            uploadMethod: nil, uploadUrl: nil
        ))
    }

    func testConfirmAcceptsBothSuccessStatusesAndDecodesDocument() async throws {
        for statusCode in [200, 201] {
            UploadURLProtocol.install { _ in .status(statusCode, Self.documentJSON) }
            let client = makeClient(UploadTokenProvider(accessTokens: ["synthetic-token"]))
            let batchId = UUID()
            let document = try await client.confirm(
                batchId: batchId,
                ConfirmRequest(
                    clientOcrJson: .object(["source": .string("synthetic")]),
                    documentId: "doc-1", exifTakenAt: nil,
                    filename: "synthetic.pdf", mimeType: "application/pdf",
                    sha256Hash: String(repeating: "b", count: 64)
                )
            )
            let request = try XCTUnwrap(UploadURLProtocol.lastRequest)
            let body = try XCTUnwrap(
                JSONSerialization.jsonObject(with: try XCTUnwrap(request.httpBody))
                    as? [String: Any]
            )
            XCTAssertEqual(document.id, "doc-1")
            XCTAssertEqual(
                request.url?.path,
                "/api/v1/batches/\(batchId.uuidString.lowercased())/documents/confirm"
            )
            XCTAssertEqual(body["document_id"] as? String, "doc-1")
            XCTAssertNotNil(body["client_ocr_json"])
        }
    }

    func testUploadPutsFileWithoutAuthorizationOrTokenRefresh() async throws {
        UploadURLProtocol.install { _ in .status(200) }
        let provider = UploadTokenProvider(accessTokens: ["synthetic-token"])
        try await makeClient(provider).upload(
            to: URL(string: "https://storage.example.test/bucket/object?signature=synthetic")!,
            fileURL: try makeTemporaryFile(), contentType: "application/pdf"
        )
        let request = try XCTUnwrap(UploadURLProtocol.lastRequest)
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(request.url?.path, "/bucket/object")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/pdf")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(request.httpBody, Data("synthetic object".utf8))
        let counts = await provider.callCounts()
        XCTAssertEqual(counts, [0, 0])
    }

    func testUploadMapsStatusAndTransportFailures() async throws {
        let cases: [(UploadReply, AppError)] = [
            (.status(401), .unauthorized), (.status(403), .unauthorized),
            (.status(500), .server), (.status(400), .unknown),
            (.failure(URLError(.notConnectedToInternet)), .network),
        ]
        let fileURL = try makeTemporaryFile()
        let destination = URL(string: "https://storage.example.test/bucket/object")!
        for (reply, expectedError) in cases {
            UploadURLProtocol.install { _ in reply }
            do {
                try await makeClient(UploadTokenProvider()).upload(
                    to: destination, fileURL: fileURL, contentType: "image/jpeg"
                )
                XCTFail("Expected upload failure")
            } catch let error as AppError {
                XCTAssertEqual(error, expectedError)
            } catch {
                XCTFail("Unexpected error type")
            }
            XCTAssertEqual(UploadURLProtocol.requests.count, 1)
        }
    }

    private func makeClient(_ provider: UploadTokenProvider) -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [UploadURLProtocol.self]
        return APIClient(
            baseURL: URL(string: "https://example.test")!,
            tokenProvider: provider, session: URLSession(configuration: configuration)
        )
    }

    private func makeTemporaryFile() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("DocumentUploadAPITests-\(UUID().uuidString)")
        try Data("synthetic object".utf8).write(to: url)
        return url
    }

    private static let documentJSON = """
    {"id":"doc-1","batch_id":"batch-1","original_filename":"synthetic.pdf",\
    "mime_type":"application/pdf","file_size_bytes":9,"sha256_hash":"bbbb",\
    "doc_role":"receipt","ocr_status":"pending","created_at":"2026-11-22T10:00:00Z"}
    """
}

private actor UploadTokenProvider: AccessTokenProvider {
    private var access: [String]
    private var refresh: [String]
    private var counts = [0, 0]
    init(accessTokens: [String] = [], refreshedTokens: [String] = []) {
        access = accessTokens; refresh = refreshedTokens
    }
    func accessToken() async throws -> String {
        counts[0] += 1
        guard !access.isEmpty else { throw AppError.unauthorized }
        return access.removeFirst()
    }
    func refreshedAccessToken() async throws -> String {
        counts[1] += 1
        guard !refresh.isEmpty else { throw AppError.unauthorized }
        return refresh.removeFirst()
    }
    func callCounts() -> [Int] { counts }
}

private struct UploadReply {
    let status: Int?
    let body: Data
    let error: Error?

    static func status(_ code: Int, _ body: String = "") -> Self {
        Self(status: code, body: Data(body.utf8), error: nil)
    }
    static func failure(_ error: Error) -> Self {
        Self(status: nil, body: Data(), error: error)
    }
}

private final class UploadURLProtocol: URLProtocol {
    static var reply: (URLRequest) -> UploadReply = { _ in .status(500) }
    static var requests: [URLRequest] = []
    static var lastRequest: URLRequest? { requests.last }

    static func install(_ next: @escaping (URLRequest) -> UploadReply) {
        reply = next; requests = []
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(Self.readBody(request))
        let result = Self.reply(request)
        if let error = result.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        let response = HTTPURLResponse(
            url: request.url!, statusCode: result.status ?? 500,
            httpVersion: "HTTP/1.1", headerFields: nil
        )!

        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: result.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    private static func readBody(_ request: URLRequest) -> URLRequest {
        guard request.httpBody == nil, let stream = request.httpBodyStream else {
            return request
        }
        stream.open()
        defer { stream.close() }
        var body = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count > 0 else { break }
            body.append(buffer, count: count)
        }
        var copy = request
        copy.httpBody = body
        return copy
    }
}
