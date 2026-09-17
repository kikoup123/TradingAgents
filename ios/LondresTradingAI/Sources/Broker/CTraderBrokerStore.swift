import Combine
import Foundation
import Security

struct BrokerGatewayConfiguration: Hashable, Sendable {
    let baseURL: URL
    let callbackScheme: String
    let useMockCTrader: Bool

    static func fromRuntime() -> BrokerGatewayConfiguration? {
        let environment = ProcessInfo.processInfo.environment
        let configured = environment["LONDRES_BROKER_GATEWAY_BASE_URL"]
            ?? environment["LONDRES_MARKET_DATA_BASE_URL"]
            ?? Bundle.main.object(forInfoDictionaryKey: "LONDRES_BROKER_GATEWAY_BASE_URL") as? String
            ?? Bundle.main.object(forInfoDictionaryKey: "LONDRES_MARKET_DATA_BASE_URL") as? String

        guard let raw = configured?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty,
              let url = URL(string: raw),
              let scheme = url.scheme?.lowercased(),
              scheme == "https" || ((scheme == "http") && ["localhost", "127.0.0.1"].contains(url.host ?? "")) else {
            return nil
        }

        let callback = environment["LONDRES_CTRADER_CALLBACK_SCHEME"]
            ?? Bundle.main.object(forInfoDictionaryKey: "LONDRES_CTRADER_CALLBACK_SCHEME") as? String
            ?? "londrestradingai"
        let mockValue = environment["LONDRES_CTRADER_MOCK_MODE"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        let useMock = ["1", "true", "yes", "on"].contains(mockValue ?? "")
        return BrokerGatewayConfiguration(
            baseURL: url,
            callbackScheme: callback.lowercased(),
            useMockCTrader: useMock
        )
    }
}

struct CTraderBrokerAccount: Codable, Hashable, Identifiable, Sendable {
    let accountKey: String
    let broker: String?
    let account: String
    let readOnly: Bool
    let oauthScope: String?
    let orderSubmissionEnabled: Bool?

    var id: String { accountKey }
}

struct CTraderAccountSnapshot: Codable, Hashable, Sendable {
    let broker: String?
    let maskedAccount: String
    let currency: String?
    let balance: Double
    let equity: Double
    let usedMargin: Double
    let freeMargin: Double
    let moneyDigits: Int
    let connected: Bool
    let status: String
    let accountEnvironment: String

    enum CodingKeys: String, CodingKey {
        case broker
        case maskedAccount = "masked_account"
        case currency
        case balance
        case equity
        case usedMargin = "used_margin"
        case freeMargin = "free_margin"
        case moneyDigits = "money_digits"
        case connected
        case status
        case accountEnvironment = "account_environment"
    }
}

enum CTraderBrokerConnectionState: String, Sendable {
    case unavailable
    case disconnected
    case connecting
    case connected
    case failed

    var displayName: String {
        switch self {
        case .unavailable:
            return "Not configured"
        case .disconnected:
            return "Not connected"
        case .connecting:
            return "Connecting…"
        case .connected:
            return "Connected · Read Only"
        case .failed:
            return "Connection error"
        }
    }
}

protocol BrokerSessionStorage {
    func save(_ value: String) throws
    func load() throws -> String?
    func delete() throws
}

@MainActor
final class CTraderBrokerStore: ObservableObject {
    @Published private(set) var state: CTraderBrokerConnectionState
    @Published private(set) var accounts: [CTraderBrokerAccount] = []
    @Published private(set) var selectedAccountSnapshot: CTraderAccountSnapshot?
    @Published private(set) var errorMessage: String?

    private let configuration: BrokerGatewayConfiguration?
    private let session: URLSession
    private let sessionStorage: any BrokerSessionStorage

    init(
        configuration: BrokerGatewayConfiguration? = BrokerGatewayConfiguration.fromRuntime(),
        session: URLSession = .shared,
        sessionStorage: any BrokerSessionStorage = BrokerSessionKeychain()
    ) {
        self.configuration = configuration
        self.session = session
        self.sessionStorage = sessionStorage
        self.state = configuration == nil ? .unavailable : .disconnected
    }

    var isConfigured: Bool { configuration != nil }
    var isMockMode: Bool { configuration?.useMockCTrader == true }

    func authorizationStartURL() -> URL? {
        guard let configuration else { return nil }
        return configuration.baseURL.appendingPathComponent(brokerPath("start"))
    }

    func handleCallback(_ url: URL) async {
        guard let configuration,
              url.scheme?.lowercased() == configuration.callbackScheme,
              url.host?.lowercased() == "ctrader",
              url.path == "/complete",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return
        }

        var values: [String: String] = [:]
        for item in components.queryItems ?? [] {
            if let value = item.value {
                values[item.name] = value
            }
        }
        if values["error"] != nil {
            state = .failed
            errorMessage = "cTrader authorization was not completed."
            return
        }
        guard let handoffCode = values["code"], !handoffCode.isEmpty else {
            state = .failed
            errorMessage = "The cTrader callback did not contain a valid handoff code."
            return
        }

        state = .connecting
        errorMessage = nil
        do {
            let body = try JSONEncoder().encode(CompleteOAuthRequest(code: handoffCode))
            let response: CompleteOAuthResponse = try await request(
                path: brokerPath("complete"),
                method: "POST",
                body: body,
                brokerSession: nil
            )
            try sessionStorage.save(response.brokerSessionToken)
            accounts = response.accounts
            state = response.connected ? .connected : .disconnected
        } catch {
            state = .failed
            errorMessage = userFacingMessage(for: error)
        }
    }

    func refreshStatus() async {
        guard configuration != nil else {
            state = .unavailable
            return
        }
        guard let token = storedSessionToken(), !token.isEmpty else {
            state = .disconnected
            accounts = []
            selectedAccountSnapshot = nil
            return
        }

        do {
            let response: BrokerStatusResponse = try await request(
                path: brokerPath("status"),
                method: "GET",
                brokerSession: token
            )
            accounts = response.accounts
            state = response.connected ? .connected : .disconnected
            errorMessage = nil
        } catch BrokerGatewayError.invalidHTTPStatus(401) {
            try? sessionStorage.delete()
            accounts = []
            selectedAccountSnapshot = nil
            state = .disconnected
        } catch BrokerGatewayError.invalidHTTPStatus(503) {
            state = .unavailable
            errorMessage = isMockMode
                ? "The cTrader mock gateway is missing its development session secret."
                : "The cTrader gateway is waiting for its approved API credentials."
        } catch {
            state = .failed
            errorMessage = userFacingMessage(for: error)
        }
    }

    func refreshCredentials() async {
        guard let token = storedSessionToken(), !token.isEmpty else {
            state = .disconnected
            return
        }
        do {
            let response: RefreshBrokerSessionResponse = try await request(
                path: brokerPath("refresh"),
                method: "POST",
                brokerSession: token
            )
            try sessionStorage.save(response.brokerSessionToken)
            accounts = response.accounts
            state = .connected
            errorMessage = nil
        } catch {
            state = .failed
            errorMessage = userFacingMessage(for: error)
        }
    }

    func loadAccount(_ account: CTraderBrokerAccount) async {
        guard let token = storedSessionToken(), !token.isEmpty else {
            state = .disconnected
            return
        }
        do {
            let response: AccountSnapshotResponse = try await request(
                path: brokerPath("account"),
                method: "GET",
                queryItems: [URLQueryItem(name: "account_key", value: account.accountKey)],
                brokerSession: token
            )
            selectedAccountSnapshot = response.snapshot
            errorMessage = nil
        } catch {
            errorMessage = userFacingMessage(for: error)
        }
    }

    func disconnectDevice() {
        try? sessionStorage.delete()
        accounts = []
        selectedAccountSnapshot = nil
        errorMessage = nil
        state = configuration == nil ? .unavailable : .disconnected
    }

    private func brokerPath(_ endpoint: String) -> String {
        let prefix = configuration?.useMockCTrader == true
            ? "v1/brokers/ctrader/mock"
            : "v1/brokers/ctrader"
        return "\(prefix)/\(endpoint)"
    }

    private func storedSessionToken() -> String? {
        do {
            return try sessionStorage.load()
        } catch {
            return nil
        }
    }

    private func request<Response: Decodable>(
        path: String,
        method: String,
        queryItems: [URLQueryItem] = [],
        body: Data? = nil,
        brokerSession: String?
    ) async throws -> Response {
        guard let configuration else { throw BrokerGatewayError.notConfigured }
        let base = configuration.baseURL.appendingPathComponent(path)
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            throw BrokerGatewayError.invalidEndpoint
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else { throw BrokerGatewayError.invalidEndpoint }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let brokerSession, !brokerSession.isEmpty {
            request.setValue(brokerSession, forHTTPHeaderField: "X-Londres-Broker-Session")
        }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BrokerGatewayError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw BrokerGatewayError.invalidHTTPStatus(http.statusCode)
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func userFacingMessage(for error: Error) -> String {
        if case BrokerGatewayError.notConfigured = error {
            return "The cTrader broker gateway is not configured in this build."
        }
        if case BrokerGatewayError.invalidHTTPStatus(let status) = error {
            return "The broker gateway returned HTTP \(status)."
        }
        return "The cTrader connection could not be completed."
    }
}

private struct CompleteOAuthRequest: Encodable {
    let code: String
}

private struct CompleteOAuthResponse: Decodable {
    let connected: Bool
    let brokerSessionToken: String
    let accounts: [CTraderBrokerAccount]
}

private struct BrokerStatusResponse: Decodable {
    let connected: Bool
    let accounts: [CTraderBrokerAccount]
}

private struct RefreshBrokerSessionResponse: Decodable {
    let brokerSessionToken: String
    let accounts: [CTraderBrokerAccount]
}

private struct AccountSnapshotResponse: Decodable {
    let snapshot: CTraderAccountSnapshot
}

private enum BrokerGatewayError: Error, Equatable {
    case notConfigured
    case invalidEndpoint
    case invalidResponse
    case invalidHTTPStatus(Int)
}

struct BrokerSessionKeychain: BrokerSessionStorage {
    private let service = "com.tradingsand.londrestradingai.ctrader"
    private let account = "broker-session-v1"

    func save(_ value: String) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess { return }
        if updateStatus != errSecItemNotFound {
            throw KeychainError.status(updateStatus)
        }
        var insert = query
        for (key, value) in attributes {
            insert[key] = value
        }
        let addStatus = SecItemAdd(insert as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw KeychainError.status(addStatus)
        }
    }

    func load() throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else {
            throw KeychainError.status(status)
        }
        return value
    }

    func delete() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.status(status)
        }
    }
}

private enum KeychainError: Error {
    case status(OSStatus)
}
