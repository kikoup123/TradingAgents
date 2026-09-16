import Foundation

struct MarketDataGatewayConfiguration: Hashable, Sendable {
    let baseURL: URL
    let quotePollingInterval: TimeInterval

    init(baseURL: URL, quotePollingInterval: TimeInterval = 1.0) {
        self.baseURL = baseURL
        self.quotePollingInterval = max(0.25, quotePollingInterval)
    }

    static func fromRuntime() -> MarketDataGatewayConfiguration? {
        let environment = ProcessInfo.processInfo.environment
        let configured = environment["LONDRES_MARKET_DATA_BASE_URL"]
            ?? Bundle.main.object(forInfoDictionaryKey: "LONDRES_MARKET_DATA_BASE_URL") as? String
        guard let value = configured?.trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty,
              let url = URL(string: value),
              url.scheme?.lowercased() == "https" else {
            return nil
        }
        return MarketDataGatewayConfiguration(baseURL: url)
    }
}

private struct GatewayCandleEnvelope: Decodable {
    let candles: [GatewayCandlePayload]
}

private struct GatewayCandlePayload: Decodable {
    let symbol: String
    let timeframe: String
    let openTime: Date
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Double?
}

private struct GatewayQuotePayload: Decodable {
    let symbol: String
    let bid: Double?
    let ask: Double?
    let timestamp: Date
}

enum CanonicalMarketDataAdapterError: Error, Equatable {
    case invalidEndpoint
    case invalidHTTPStatus(Int)
    case symbolMismatch
    case timeframeMismatch
    case emptyResponse
}

/// Broker-independent market-data client for the Londres app-owned gateway.
///
/// Gateway contract:
/// - GET /v1/market/candles?symbol=...&timeframe=...&limit=...
/// - GET /v1/market/quote?symbol=...
/// - candle responses contain CLOSED candles only, ordered or unordered
/// - timestamps are ISO-8601
///
/// No brokerage credentials, account identifiers or order-routing capabilities
/// exist in this adapter.
final class CanonicalHTTPMarketDataAdapter: MarketDataProvider, @unchecked Sendable {
    let providerID = "londres-canonical-http-market-data"

    private let configuration: MarketDataGatewayConfiguration
    private let session: URLSession
    private let decoder: JSONDecoder

    init(
        configuration: MarketDataGatewayConfiguration,
        session: URLSession = .shared
    ) {
        self.configuration = configuration
        self.session = session
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
    }

    func candles(
        symbol: String,
        timeframe: LondresTimeframe,
        limit: Int
    ) async throws -> [MarketCandle] {
        let cleanSymbol = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !cleanSymbol.isEmpty else { throw MarketDataError.invalidSymbol }
        guard limit > 0 else { throw MarketDataError.invalidLimit }

        let url = try endpoint(
            path: "v1/market/candles",
            queryItems: [
                URLQueryItem(name: "symbol", value: cleanSymbol),
                URLQueryItem(name: "timeframe", value: timeframe.rawValue),
                URLQueryItem(name: "limit", value: String(limit))
            ]
        )
        let envelope: GatewayCandleEnvelope = try await request(url)
        guard !envelope.candles.isEmpty else {
            throw CanonicalMarketDataAdapterError.emptyResponse
        }

        let bars = try envelope.candles.map { payload -> MarketCandle in
            guard payload.symbol.uppercased() == cleanSymbol else {
                throw CanonicalMarketDataAdapterError.symbolMismatch
            }
            guard payload.timeframe.uppercased() == timeframe.rawValue.uppercased() else {
                throw CanonicalMarketDataAdapterError.timeframeMismatch
            }
            let candle = MarketCandle(
                symbol: cleanSymbol,
                timeframe: timeframe,
                openTime: payload.openTime,
                open: payload.open,
                high: payload.high,
                low: payload.low,
                close: payload.close,
                volume: payload.volume
            )
            guard candle.isValid else { throw MarketDataError.malformedCandle }
            return candle
        }
        return bars.sorted { $0.openTime < $1.openTime }
    }

    func quote(symbol: String) async throws -> MarketQuote {
        let cleanSymbol = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !cleanSymbol.isEmpty else { throw MarketDataError.invalidSymbol }
        let url = try endpoint(
            path: "v1/market/quote",
            queryItems: [URLQueryItem(name: "symbol", value: cleanSymbol)]
        )
        let payload: GatewayQuotePayload = try await request(url)
        guard payload.symbol.uppercased() == cleanSymbol else {
            throw CanonicalMarketDataAdapterError.symbolMismatch
        }
        let quote = MarketQuote(
            symbol: cleanSymbol,
            bid: payload.bid,
            ask: payload.ask,
            timestamp: payload.timestamp
        )
        guard quote.mid?.isFinite == true, (quote.mid ?? 0) > 0 else {
            throw MarketDataError.unavailable
        }
        return quote
    }

    func quoteStream(symbol: String) -> AsyncThrowingStream<MarketQuote, Error> {
        let interval = configuration.quotePollingInterval
        return AsyncThrowingStream { continuation in
            let task = Task {
                while !Task.isCancelled {
                    do {
                        continuation.yield(try await self.quote(symbol: symbol))
                        let nanoseconds = UInt64(interval * 1_000_000_000)
                        try await Task.sleep(nanoseconds: nanoseconds)
                    } catch is CancellationError {
                        continuation.finish()
                        return
                    } catch {
                        continuation.finish(throwing: error)
                        return
                    }
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func endpoint(path: String, queryItems: [URLQueryItem]) throws -> URL {
        let base = configuration.baseURL.appendingPathComponent(path)
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            throw CanonicalMarketDataAdapterError.invalidEndpoint
        }
        components.queryItems = queryItems
        guard let url = components.url else {
            throw CanonicalMarketDataAdapterError.invalidEndpoint
        }
        return url
    }

    private func request<T: Decodable>(_ url: URL) async throws -> T {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw MarketDataError.unavailable
        }
        guard (200..<300).contains(http.statusCode) else {
            throw CanonicalMarketDataAdapterError.invalidHTTPStatus(http.statusCode)
        }
        return try decoder.decode(T.self, from: data)
    }
}
