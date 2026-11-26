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

    func testListTransactionsDecodesThreeLevelGroupsAndAlerts() async throws {
        URLProtocolStub.install { _ in .response(statusCode: 200, body: Self.transactionListJSON) }
        let client = makeClient(provider: StubTokenProvider(accessTokens: ["synthetic-token"]))
        let batchId = try XCTUnwrap(UUID(uuidString: "44444444-4444-4444-4444-444444444444"))

        let list = try await client.listTransactions(batchId: batchId)
        let request = try XCTUnwrap(URLProtocolStub.lastRequest)

        XCTAssertEqual(request.url?.path, "/api/v1/batches/44444444-4444-4444-4444-444444444444/transactions")
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
        XCTAssertEqual(list.missingAlerts.count, 1)
        XCTAssertEqual(list.groups.first?.category, "meal")
        let transaction = try XCTUnwrap(list.groups.first?.scopes.first?.regions.first?.transactions.first)
        XCTAssertEqual(transaction.claimAmount, "120.00")
        XCTAssertEqual(transaction.documents.first?.role, "invoice")
    }

    func testUpdateTransactionSendsPatchWithOnlySetFields() async throws {
        URLProtocolStub.install { _ in .response(statusCode: 200, body: Self.transactionOutJSON) }
        let client = makeClient(provider: StubTokenProvider(accessTokens: ["synthetic-token"]))
        let patch = TransactionPatch(
            category: nil, invoiceDocId: nil, notes: "Synthetic note",
            receiptDocId: nil, splitCount: 3, tipAmount: .string("0.00")
        )

        let updated = try await client.updateTransaction(id: Self.transactionId, patch)
        let request = try XCTUnwrap(URLProtocolStub.lastRequest)
        XCTAssertEqual(request.url?.path, "/api/v1/transactions/22222222-2222-2222-2222-222222222222")
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
        let object = try Self.jsonObject(from: request)
        XCTAssertEqual(Set(object.keys), ["notes", "split_count", "tip_amount"])
        XCTAssertEqual(updated.merchant, "Synthetic cafe")
    }

    func testTemplateListAndCreateFollowContract() async throws {
        let recorder = ResponseSequence([
            .response(statusCode: 200, body: "[\(Self.templateOutJSON)]"),
            .response(statusCode: 201, body: Self.templateOutJSON),
        ])
        URLProtocolStub.install { _ in recorder.next() }
        let client = makeClient(provider: StubTokenProvider(accessTokens: ["synthetic-token"]))
        let rules = try JSONDecoder().decode(RuleTemplateDSL.self, from: Data(Self.ruleDSLJSON.utf8))

        let templates = try await client.listTemplates()
        XCTAssertEqual(templates.map(\.name), ["Synthetic template"])
        XCTAssertEqual(URLProtocolStub.lastRequest?.url?.path, "/api/v1/templates")
        XCTAssertEqual(URLProtocolStub.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")

        _ = try await client.createTemplate(TemplateCreate(description: nil, name: "Copy", rules: rules))
        let request = try XCTUnwrap(URLProtocolStub.lastRequest)
        let object = try Self.jsonObject(from: request)
        XCTAssertEqual(request.url?.path, "/api/v1/templates")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
        XCTAssertEqual(object["name"] as? String, "Copy")
        XCTAssertEqual((object["rules"] as? [String: Any])?["template_code"] as? String, "synthetic_tpl")
    }

    func testPatchTemplateOmitsNilFieldsAndCloneAllowsEmptyBody() async throws {
        let recorder = ResponseSequence([
            .response(statusCode: 200, body: Self.templateOutJSON),
            .response(statusCode: 201, body: Self.templateOutJSON),
        ])
        URLProtocolStub.install { _ in recorder.next() }
        let client = makeClient(provider: StubTokenProvider(accessTokens: ["synthetic-token"]))

        _ = try await client.patchTemplate(
            id: Self.templateId,
            TemplatePatch(description: nil, name: "Synthetic rename", rules: nil)
        )
        var request = try XCTUnwrap(URLProtocolStub.lastRequest)
        XCTAssertEqual(request.url?.path, "/api/v1/templates/11111111-1111-1111-1111-111111111111")
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
        let patched = try Self.jsonObject(from: request)
        XCTAssertEqual(Set(patched.keys), ["name"])

        let cloned = try await client.cloneTemplate(id: Self.templateId, nil)
        request = try XCTUnwrap(URLProtocolStub.lastRequest)
        XCTAssertEqual(request.url?.path, "/api/v1/templates/11111111-1111-1111-1111-111111111111/clone")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer synthetic-token")
        XCTAssertNil(request.httpBody)
        XCTAssertNil(request.value(forHTTPHeaderField: "Content-Type"))
        XCTAssertEqual(cloned.name, "Synthetic template")
    }

    func testReviewStatusesMapToExistingSafeErrors() async throws {
        let mappings: [(Int, AppError)] = [(404, .unknown), (409, .unknown), (422, .validation)]
        for (statusCode, expected) in mappings {
            URLProtocolStub.install { _ in .response(statusCode: statusCode, body: #"{"detail":"private"}"#) }
            let client = makeClient(provider: StubTokenProvider(accessTokens: ["synthetic-token"]))
            do {
                _ = try await client.updateTransaction(id: Self.transactionId, Self.emptyPatch)
                XCTFail("Expected mapped HTTP error for \(statusCode)")
            } catch let error as AppError {
                XCTAssertEqual(error, expected)
                XCTAssertFalse(error.userMessage.contains("private"))
            } catch {
                XCTFail("Unexpected error type for \(statusCode)")
            }
        }
    }

    private static let transactionId = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
    private static let templateId = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
    private static let emptyPatch = TransactionPatch(category: nil, invoiceDocId: nil, notes: nil, receiptDocId: nil, splitCount: nil, tipAmount: nil)

    private static func jsonObject(from request: URLRequest) throws -> [String: Any] {
        let body = try XCTUnwrap(request.httpBody)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
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

    private static let transactionOutJSON = """
    {"id":"22222222-2222-2222-2222-222222222222","category":"meal","merchant":"Synthetic cafe","tx_date":"2026-11-22","region_scope":"in_scope","region_detail":"cn","currency":"CNY","original_amount":"120.00","claim_amount":"120.00","tip_amount":"0.00","split_count":2,"payment_status":"paid","airport_direction":null,"airport_iata":null,"flags":["meal_split"],"notes":"","is_user_verified":false,"documents":[{"id":"33333333-3333-3333-3333-333333333333","role":"invoice","original_filename":"invoice.pdf"}]}
    """

    private static let transactionListJSON = """
    {"batch_id":"44444444-4444-4444-4444-444444444444","status":"review","missing_alerts":[{"code":"missing_receipt","transaction_id":"22222222-2222-2222-2222-222222222222"}],"groups":[{"category":"meal","scopes":[{"region_scope":"in_scope","regions":[{"region_detail":"cn","transactions":[\(transactionOutJSON)]}]}]}]}
    """

    private static let ruleDSLJSON = """
    {"schema_version":"1.0","template_code":"synthetic_tpl","expense_types":[{"code":"meal","label":"Meal","require_trip_reference":false}],"categories":[{"code":"meal","label":"Meal","group":"living"}],"region_hierarchy":{"in_scope":{"label":"In","regions":[{"code":"cn","label":"China","currencies":["CNY"]}]},"out_of_scope":{"label":"Out","regions":[{"code":"sg","label":"Singapore","currencies":["SGD"]}]},"preserve_original_currency":true},"compliance_rules":{"require_invoice_payment_closure":true,"transit_direct_pay_exemptions":["taxi"],"meal_split_for_multiple_diners":true,"exclude_ride_hailing_tips":{"enabled":true,"providers":["didi"],"highlight_flag":"tip"},"timezone_tolerance_days":2,"check_missing_airport_transfers":true,"deduplicate_reshot_receipts":true,"filter_irrelevant_files":true},"naming_and_export":{"standard_pattern":"{date}","airport_transfer_pattern":"{iata}","excel_group_levels":["category_group"],"excel_filename_pattern":"expense.xlsx"}}
    """

    private static let templateOutJSON = """
    {"id":"11111111-1111-1111-1111-111111111111","name":"Synthetic template","description":"Synthetic","schema_version":"1.0","is_system_default":false,"rules":\(ruleDSLJSON),"created_at":"2026-11-22T10:00:00Z","updated_at":"2026-11-22T10:00:00Z"}
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
