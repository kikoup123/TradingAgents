import Foundation
import XCTest
@testable import LondresTradingAI

final class CTraderBrokerStoreTests: XCTestCase {
    override func tearDown() {
        BrokerURLProtocol.handler = nil
        super.tearDown()
    }

    @MainActor
    func testMockModeUsesDedicatedReadOnlyStartPath() {
        let store = CTraderBrokerStore(
            configuration: BrokerGatewayConfiguration(
                baseURL: URL(string: "https://gateway.example.test")!,
                callbackScheme: "londrestradingai",
                useMockCTrader: true
            ),
            sessionStorage: MemoryBrokerSessionStorage()
        )

        XCTAssertTrue(store.isMockMode)
        XCTAssertEqual(
            store.authorizationStartURL()?.path,
            "/v1/brokers/ctrader/mock/start"
        )
    }

    @MainActor
    func testProductionModeKeepsRealCTraderPath() {
        let store = CTraderBrokerStore(
            configuration: BrokerGatewayConfiguration(
                baseURL: URL(string: "https://gateway.example.test")!,
                callbackScheme: "londrestradingai",
                useMockCTrader: false
            ),
            sessionStorage: MemoryBrokerSessionStorage()
        )

        XCTAssertFalse(store.isMockMode)
        XCTAssertEqual(
            store.authorizationStartURL()?.path,
            "/v1/brokers/ctrader/start"
        )
    }

    @MainActor
    func testOAuthCallbackStoresOnlyOpaqueSessionAndLoadsMaskedSnapshot() async throws {
        let storage = MemoryBrokerSessionStorage()
        let session = makeStubSession { request in
            switch request.url?.path {
            case "/v1/brokers/ctrader/mock/complete":
                XCTAssertEqual(request.httpMethod, "POST")
                let body = try XCTUnwrap(request.httpBody)
                let object = try XCTUnwrap(
                    JSONSerialization.jsonObject(with: body) as? [String: String]
                )
                XCTAssertEqual(object["code"], "handoff-code")
                return try self.response(
                    request,
                    status: 200,
                    json: [
                        "connected": true,
                        "brokerSessionToken": "opaque-session-token",
                        "accounts": [[
                            "accountKey": "opaque-account-key",
                            "broker": "Trading Sand Mock",
                            "account": "••••1234",
                            "readOnly": true,
                            "oauthScope": "accounts",
                            "orderSubmissionEnabled": false,
                        ]],
                    ]
                )

            case "/v1/brokers/ctrader/mock/account":
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "X-Londres-Broker-Session"),
                    "opaque-session-token"
                )
                XCTAssertEqual(
                    URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false)?
                        .queryItems?.first(where: { $0.name == "account_key" })?.value,
                    "opaque-account-key"
                )
                return try self.response(
                    request,
                    status: 200,
                    json: [
                        "snapshot": [
                            "broker": "Trading Sand Mock",
                            "masked_account": "••••1234",
                            "currency": "EUR",
                            "balance": 5_000.0,
                            "equity": 5_125.5,
                            "used_margin": 250.0,
                            "free_margin": 4_875.5,
                            "money_digits": 2,
                            "connected": true,
                            "status": "CONNECTED",
                            "account_environment": "HIDDEN_INTERNAL",
                        ],
                    ]
                )

            default:
                XCTFail("Unexpected broker request: \(request.url?.absoluteString ?? "nil")")
                return try self.response(request, status: 404, json: [:])
            }
        }

        let store = CTraderBrokerStore(
            configuration: BrokerGatewayConfiguration(
                baseURL: URL(string: "https://gateway.example.test")!,
                callbackScheme: "londrestradingai",
                useMockCTrader: true
            ),
            session: session,
            sessionStorage: storage
        )

        await store.handleCallback(
            URL(string: "londrestradingai://ctrader/complete?code=handoff-code")!
        )

        XCTAssertEqual(store.state.rawValue, "connected")
        XCTAssertEqual(storage.value, "opaque-session-token")
        XCTAssertEqual(store.accounts.count, 1)
        XCTAssertEqual(store.accounts[0].account, "••••1234")
        XCTAssertTrue(store.accounts[0].readOnly)
        XCTAssertEqual(store.accounts[0].oauthScope, "accounts")
        XCTAssertEqual(store.accounts[0].orderSubmissionEnabled, false)

        await store.loadAccount(store.accounts[0])
        XCTAssertEqual(store.selectedAccountSnapshot?.maskedAccount, "••••1234")
        XCTAssertEqual(store.selectedAccountSnapshot?.currency, "EUR")
        XCTAssertEqual(store.selectedAccountSnapshot?.balance, 5_000.0)
        XCTAssertEqual(store.selectedAccountSnapshot?.equity, 5_125.5)
        XCTAssertEqual(store.selectedAccountSnapshot?.accountEnvironment, "HIDDEN_INTERNAL")
    }

    @MainActor
    func testUnauthorizedStatusDeletesStoredBrokerSession() async {
        let storage = MemoryBrokerSessionStorage(value: "expired-session")
        let session = makeStubSession { request in
            XCTAssertEqual(request.url?.path, "/v1/brokers/ctrader/status")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "X-Londres-Broker-Session"),
                "expired-session"
            )
            return try self.response(request, status: 401, json: ["detail": "expired"])
        }
        let store = CTraderBrokerStore(
            configuration: BrokerGatewayConfiguration(
                baseURL: URL(string: "https://gateway.example.test")!,
                callbackScheme: "londrestradingai",
                useMockCTrader: false
            ),
            session: session,
            sessionStorage: storage
        )

        await store.refreshStatus()

        XCTAssertNil(storage.value)
        XCTAssertEqual(store.state.rawValue, "disconnected")
        XCTAssertTrue(store.accounts.isEmpty)
    }

    private func makeStubSession(
        handler: @escaping (URLRequest) throws -> (HTTPURLResponse, Data)
    ) -> URLSession {
        BrokerURLProtocol.handler = handler
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BrokerURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    private func response(
        _ request: URLRequest,
        status: Int,
        json: [String: Any]
    ) throws -> (HTTPURLResponse, Data) {
        let url = try XCTUnwrap(request.url)
        let response = try XCTUnwrap(
            HTTPURLResponse(
                url: url,
                statusCode: status,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            )
        )
        return (response, try JSONSerialization.data(withJSONObject: json))
    }
}

private final class MemoryBrokerSessionStorage: BrokerSessionStorage {
    var value: String?

    init(value: String? = nil) {
        self.value = value
    }

    func save(_ value: String) throws {
        self.value = value
    }

    func load() throws -> String? {
        value
    }

    func delete() throws {
        value = nil
    }
}

private final class BrokerURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
