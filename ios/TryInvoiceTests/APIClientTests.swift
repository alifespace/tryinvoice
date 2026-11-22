import Foundation
import XCTest
@testable import TryInvoice

final class APIClientTests: XCTestCase {
    override func tearDown() {
        URLProtocolStub.install { _ in
            .response(statusCode: 500)
        }
        super.tearDown()
    }

    func testHealthDecodesWithoutAuthorization() async throws {
        let provider = StubTokenProvider()
        URLProtocolStub.install { _ in
            .response(statusCode: 200, body: #"{"status":"ok"}"#)
        }
        let client = makeClient(provider: provider)

        let result = try await client.health()

        XCTAssertEqual(result, ["status": "ok"])
        XCTAssertNil(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"))
        let counts = await provider.callCounts()
        XCTAssertEqual(counts, [0, 0])
    }

    func testListBatchesDecodesPageAndBuildsAuthenticatedQueryRequest() async throws {
        let provider = StubTokenProvider(accessTokens: ["synthetic-token"])
        URLProtocolStub.install { _ in
            .response(statusCode: 200, body: Self.batchPageJSON)
        }
        let client = makeClient(
            provider: provider,
            baseURL: URL(string: "https://example.test/service/")!,
            timeout: 8
        )

        let page = try await client.listBatches(limit: 25, offset: 7)
        let request = try XCTUnwrap(URLProtocolStub.lastRequest)

        XCTAssertEqual(page.items.count, 1)
        XCTAssertEqual(page.items[0].id, "batch-1")
        XCTAssertEqual(page.total, 1)
        XCTAssertEqual(request.url?.path, "/service/api/v1/batches")
        XCTAssertEqual(request.url?.query, "limit=25&offset=7")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Cache-Control"), "no-cache, no-store")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Pragma"), "no-cache")
        XCTAssertEqual(request.timeoutInterval, 8)
    }

    func testCacheableResponsesAreRequestedAgainInsteadOfReused() async throws {
        URLProtocolStub.install { _ in
            .response(statusCode: 200, body: #"{"status":"ok"}"#)
        }
        let client = makeClient(provider: StubTokenProvider())

        _ = try await client.health()
        _ = try await client.health()

        XCTAssertEqual(URLProtocolStub.requests.count, 2)
        XCTAssertTrue(URLProtocolStub.requests.allSatisfy {
            $0.cachePolicy == .reloadIgnoringLocalCacheData
                && $0.value(forHTTPHeaderField: "Cache-Control") == "no-cache, no-store"
        })
    }

    func testCreateBatchEncodesRequestAndDecodesBatchResponse() async throws {
        let provider = StubTokenProvider(accessTokens: ["synthetic-token"])
        URLProtocolStub.install { _ in
            .response(statusCode: 201, body: Self.batchOutJSON)
        }
        let client = makeClient(provider: provider)
        let batch = BatchCreate(
            batchDate: "2026-11-22",
            expenseType: "meal",
            templateId: "template-1",
            title: "Synthetic batch"
        )

        let result = try await client.createBatch(batch)
        let request = try XCTUnwrap(URLProtocolStub.lastRequest)
        let body = try XCTUnwrap(request.httpBody)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])

        XCTAssertEqual(result.id, "batch-1")
        XCTAssertEqual(request.url?.path, "/api/v1/batches")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(object["batch_date"], "2026-11-22")
        XCTAssertEqual(object["template_id"], "template-1")
    }

    func testDeleteAccountAccepts204WithoutResponseBody() async throws {
        let provider = StubTokenProvider(accessTokens: ["synthetic-token"])
        URLProtocolStub.install { _ in
            .response(statusCode: 204)
        }
        let client = makeClient(provider: provider)

        try await client.deleteAccount()

        XCTAssertEqual(URLProtocolStub.lastRequest?.httpMethod, "DELETE")
        XCTAssertEqual(URLProtocolStub.lastRequest?.url?.path, "/api/v1/account")
    }

    func testUnauthorizedRefreshesAndRetriesOnlyOnce() async throws {
        let provider = StubTokenProvider(
            accessTokens: ["expired-token"],
            refreshedTokens: ["refreshed-token"]
        )
        let recorder = ResponseSequence([
            .response(statusCode: 401),
            .response(statusCode: 200, body: Self.batchPageJSON),
        ])
        URLProtocolStub.install { _ in recorder.next() }
        let client = makeClient(provider: provider)

        _ = try await client.listBatches(limit: 20, offset: 0)

        XCTAssertEqual(recorder.count, 2)
        XCTAssertEqual(
            URLProtocolStub.requests.map { $0.value(forHTTPHeaderField: "Authorization") },
            ["Bearer expired-token", "Bearer refreshed-token"]
        )
        let counts = await provider.callCounts()
        XCTAssertEqual(counts, [1, 1])
    }

    func testSecondUnauthorizedMapsToUnauthorizedWithoutFurtherRetry() async {
        let provider = StubTokenProvider(
            accessTokens: ["expired-token"],
            refreshedTokens: ["still-invalid"]
        )
        let recorder = ResponseSequence([
            .response(statusCode: 401),
            .response(statusCode: 401, body: #"{"detail":"sensitive response"}"#),
        ])
        URLProtocolStub.install { _ in recorder.next() }
        let client = makeClient(provider: provider)

        do {
            _ = try await client.listBatches(limit: 20, offset: 0)
            XCTFail("Expected unauthorized error")
        } catch let error as AppError {
            XCTAssertEqual(error, .unauthorized)
            XCTAssertFalse(error.userMessage.contains("sensitive response"))
        } catch {
            XCTFail("Unexpected error type")
        }
        XCTAssertEqual(recorder.count, 2)
    }

    func testValidationAndServerResponsesMapToSafeErrors() async {
        for (statusCode, expectedError) in [(422, AppError.validation), (503, .server)] {
            URLProtocolStub.install { _ in
                .response(statusCode: statusCode, body: #"{"detail":"synthetic private text"}"#)
            }
            let client = makeClient(
                provider: StubTokenProvider(accessTokens: ["synthetic-token"])
            )

            do {
                _ = try await client.listBatches(limit: 20, offset: 0)
                XCTFail("Expected mapped HTTP error")
            } catch let error as AppError {
                XCTAssertEqual(error, expectedError)
                XCTAssertFalse(error.userMessage.contains("synthetic private text"))
            } catch {
                XCTFail("Unexpected error type")
            }
        }
    }

    func testTimeoutMapsToNetworkError() async {
        URLProtocolStub.install { _ in .failure(URLError(.timedOut)) }
        let client = makeClient(
            provider: StubTokenProvider(accessTokens: ["synthetic-token"])
        )

        do {
            _ = try await client.listBatches(limit: 20, offset: 0)
            XCTFail("Expected network error")
        } catch let error as AppError {
            XCTAssertEqual(error, .network)
        } catch {
            XCTFail("Unexpected error type")
        }
    }

    func testTokenProviderFailureMapsToUnauthorized() async {
        let provider = StubTokenProvider(failAccess: true)
        let client = makeClient(provider: provider)

        do {
            _ = try await client.listBatches(limit: 20, offset: 0)
            XCTFail("Expected unauthorized error")
        } catch let error as AppError {
            XCTAssertEqual(error, .unauthorized)
        } catch {
            XCTFail("Unexpected error type")
        }
    }

    private func makeClient(
        provider: StubTokenProvider,
        baseURL: URL = URL(string: "https://example.test")!,
        timeout: TimeInterval = 15
    ) -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [URLProtocolStub.self]
        return APIClient(
            baseURL: baseURL,
            tokenProvider: provider,
            session: URLSession(configuration: configuration),
            timeout: timeout
        )
    }

    private static let batchPageJSON = """
    {"items":[\(batchOutJSON)],"total":1,"limit":20,"offset":0}
    """

    private static let batchOutJSON = """
    {"id":"batch-1","template_id":"template-1","expense_type":"meal","title":"Synthetic batch","batch_date":"2026-11-22","trip_start_date":null,"trip_end_date":null,"destinations":[],"status":"draft","missing_alerts":[],"created_at":"2026-11-22T10:00:00Z","updated_at":"2026-11-22T10:00:00Z"}
    """
}

private actor StubTokenProvider: AccessTokenProvider {
    private var accessTokens: [String]
    private var refreshedTokens: [String]
    private let failAccess: Bool
    private var accessCount = 0
    private var refreshCount = 0

    init(
        accessTokens: [String] = [],
        refreshedTokens: [String] = [],
        failAccess: Bool = false
    ) {
        self.accessTokens = accessTokens
        self.refreshedTokens = refreshedTokens
        self.failAccess = failAccess
    }

    func accessToken() async throws -> String {
        accessCount += 1
        guard !failAccess, !accessTokens.isEmpty else {
            throw AppError.unauthorized
        }
        return accessTokens.removeFirst()
    }

    func refreshedAccessToken() async throws -> String {
        refreshCount += 1
        guard !refreshedTokens.isEmpty else {
            throw AppError.unauthorized
        }
        return refreshedTokens.removeFirst()
    }

    func callCounts() -> [Int] {
        [accessCount, refreshCount]
    }
}

private struct StubResponse {
    let statusCode: Int?
    let body: Data
    let error: Error?

    static func response(statusCode: Int, body: String = "") -> Self { Self(statusCode: statusCode, body: Data(body.utf8), error: nil) }

    static func failure(_ error: Error) -> Self { Self(statusCode: nil, body: Data(), error: error) }
}

private final class ResponseSequence {
    private let lock = NSLock()
    private var responses: [StubResponse]
    private(set) var count = 0

    init(_ responses: [StubResponse]) { self.responses = responses }

    func next() -> StubResponse {
        lock.lock()
        defer { lock.unlock() }
        count += 1
        return responses.isEmpty ? .response(statusCode: 500) : responses.removeFirst()
    }
}

private final class URLProtocolStub: URLProtocol {
    typealias Handler = (URLRequest) -> StubResponse

    private static let lock = NSLock()
    private static var handler: Handler = { _ in .response(statusCode: 500) }
    private static var recordedRequests: [URLRequest] = []

    static var requests: [URLRequest] { lock.lock(); defer { lock.unlock() }; return recordedRequests }

    static var lastRequest: URLRequest? { requests.last }

    static func install(_ newHandler: @escaping Handler) {
        lock.lock(); defer { lock.unlock() }
        handler = newHandler
        recordedRequests = []
    }

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let recordedRequest = Self.requestWithBody(request)
        Self.lock.lock()
        let currentHandler = Self.handler
        Self.recordedRequests.append(recordedRequest)
        Self.lock.unlock()

        let result = currentHandler(request)
        if let error = result.error {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        guard let statusCode = result.statusCode,
              let response = HTTPURLResponse(
                url: request.url!,
                statusCode: statusCode,
                httpVersion: "HTTP/1.1",
                headerFields: [
                    "Content-Type": "application/json",
                    "Cache-Control": "public, max-age=3600",
                ]
              )
        else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .allowed)
        client?.urlProtocol(self, didLoad: result.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    private static func requestWithBody(_ request: URLRequest) -> URLRequest {
        guard let stream = request.httpBodyStream else { return request }
        stream.open()
        defer { stream.close() }
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4096)
        defer { buffer.deallocate() }
        var body = Data()
        while stream.hasBytesAvailable {
            let count = stream.read(buffer, maxLength: 4096)
            guard count > 0 else { break }
            body.append(buffer, count: count)
        }
        var copy = request
        copy.httpBody = body
        return copy
    }
}
