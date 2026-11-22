import Foundation

struct APIClient {
    private let baseURL: URL
    private let tokenProvider: any AccessTokenProvider
    private let session: URLSession
    private let timeout: TimeInterval

    init(
        baseURL: URL,
        tokenProvider: any AccessTokenProvider,
        session: URLSession = .shared,
        timeout: TimeInterval = 15
    ) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        self.session = session
        self.timeout = timeout
    }

    func health() async throws -> [String: String] {
        let request = try makeRequest(path: "health", method: "GET")
        let (data, response) = try await perform(request, authenticated: false)
        try validate(response, expectedStatus: 200)
        return try decode([String: String].self, from: data)
    }

    func listBatches(limit: Int, offset: Int) async throws -> BatchPage {
        let queryItems = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
        ]
        let request = try makeRequest(
            path: "batches",
            method: "GET",
            queryItems: queryItems
        )
        let (data, response) = try await perform(request, authenticated: true)
        try validate(response, expectedStatus: 200)
        return try decode(BatchPage.self, from: data)
    }

    func createBatch(_ batch: BatchCreate) async throws -> BatchOut {
        var request = try makeRequest(path: "batches", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            request.httpBody = try JSONEncoder().encode(batch)
        } catch {
            throw AppError.unknown
        }
        let (data, response) = try await perform(request, authenticated: true)
        try validate(response, expectedStatus: 201)
        return try decode(BatchOut.self, from: data)
    }

    func deleteAccount() async throws {
        let request = try makeRequest(path: "account", method: "DELETE")
        let (_, response) = try await perform(request, authenticated: true)
        try validate(response, expectedStatus: 204)
    }

    func presign(batchId: UUID, _ payload: PresignRequest) async throws -> PresignResponse {
        let request = try makeJSONRequest(
            path: "batches/\(batchId.uuidString.lowercased())/documents/presign",
            method: "POST",
            body: payload
        )
        let (data, response) = try await perform(request, authenticated: true)
        try validate(response, expectedStatus: 200)
        return try decode(PresignResponse.self, from: data)
    }

    func confirm(batchId: UUID, _ payload: ConfirmRequest) async throws -> DocumentOut {
        let request = try makeJSONRequest(
            path: "batches/\(batchId.uuidString.lowercased())/documents/confirm",
            method: "POST",
            body: payload
        )
        let (data, response) = try await perform(request, authenticated: true)
        try validate(response, expectedStatuses: [200, 201])
        return try decode(DocumentOut.self, from: data)
    }

    func upload(to url: URL, fileURL: URL, contentType: String) async throws {
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = "PUT"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        request.setValue("no-cache, no-store", forHTTPHeaderField: "Cache-Control")
        request.setValue("no-cache", forHTTPHeaderField: "Pragma")

        let response: URLResponse
        do {
            (_, response) = try await session.upload(for: request, fromFile: fileURL)
        } catch is URLError {
            throw AppError.network
        } catch {
            throw AppError.unknown
        }
        guard let httpResponse = response as? HTTPURLResponse else {
            throw AppError.unknown
        }
        switch httpResponse.statusCode {
        case 200...299:
            return
        case 401, 403:
            throw AppError.unauthorized
        case 500...599:
            throw AppError.server
        default:
            throw AppError.unknown
        }
    }

    private func makeRequest(
        path: String,
        method: String,
        queryItems: [URLQueryItem] = []
    ) throws -> URLRequest {
        guard var components = URLComponents(
            url: baseURL,
            resolvingAgainstBaseURL: false
        ) else {
            throw AppError.unknown
        }
        let basePath = components.path.trimmingCharacters(
            in: CharacterSet(charactersIn: "/")
        )
        let endpoint = ["api/v1", path].joined(separator: "/")
        components.path = "/" + [basePath, endpoint]
            .filter { !$0.isEmpty }
            .joined(separator: "/")
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw AppError.unknown
        }

        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("no-cache, no-store", forHTTPHeaderField: "Cache-Control")
        request.setValue("no-cache", forHTTPHeaderField: "Pragma")
        return request
    }

    private func makeJSONRequest<Body: Encodable>(
        path: String,
        method: String,
        body: Body
    ) throws -> URLRequest {
        var request = try makeRequest(path: path, method: method)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            request.httpBody = try JSONEncoder().encode(body)
        } catch {
            throw AppError.unknown
        }
        return request
    }

    private func perform(
        _ originalRequest: URLRequest,
        authenticated: Bool
    ) async throws -> (Data, HTTPURLResponse) {
        var request = originalRequest
        if authenticated {
            do {
                request.setValue(
                    "Bearer \(try await tokenProvider.accessToken())",
                    forHTTPHeaderField: "Authorization"
                )
            } catch {
                throw AppError.unauthorized
            }
        }

        let first = try await send(request)
        guard authenticated, first.1.statusCode == 401 else {
            return first
        }

        do {
            request.setValue(
                "Bearer \(try await tokenProvider.refreshedAccessToken())",
                forHTTPHeaderField: "Authorization"
            )
        } catch {
            throw AppError.unauthorized
        }
        let retry = try await send(request)
        guard retry.1.statusCode != 401 else {
            throw AppError.unauthorized
        }
        return retry
    }

    private func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch is URLError {
            throw AppError.network
        } catch {
            throw AppError.unknown
        }
        guard let httpResponse = response as? HTTPURLResponse else {
            throw AppError.unknown
        }
        return (data, httpResponse)
    }

    private func validate(
        _ response: HTTPURLResponse,
        expectedStatus: Int
    ) throws {
        try validate(response, expectedStatuses: [expectedStatus])
    }

    private func validate(
        _ response: HTTPURLResponse,
        expectedStatuses: Set<Int>
    ) throws {
        guard !expectedStatuses.contains(response.statusCode) else {
            return
        }
        switch response.statusCode {
        case 401:
            throw AppError.unauthorized
        case 422:
            throw AppError.validation
        case 500...599:
            throw AppError.server
        default:
            throw AppError.unknown
        }
    }

    private func decode<Value: Decodable>(
        _ type: Value.Type,
        from data: Data
    ) throws -> Value {
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw AppError.unknown
        }
    }
}

extension APIClient: UploadAPI {}
