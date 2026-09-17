import Foundation

struct MarketCandle: Identifiable, Codable, Hashable, Sendable {
    let symbol: String
    let timeframe: LondresTimeframe
    let openTime: Date
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Double?

    var id: String {
        "\(symbol)|\(timeframe.rawValue)|\(openTime.timeIntervalSince1970)"
    }

    var isValid: Bool {
        let prices = [open, high, low, close]
        return !symbol.isEmpty
            && prices.allSatisfy { $0.isFinite && $0 > 0 }
            && high >= max(open, close)
            && low <= min(open, close)
            && high >= low
            && (volume == nil || (volume?.isFinite == true && (volume ?? 0) >= 0))
    }

    var bodyHigh: Double { max(open, close) }
    var bodyLow: Double { min(open, close) }
    var isUpClose: Bool { close > open }
    var isDownClose: Bool { close < open }
}

struct MarketQuote: Codable, Hashable, Sendable {
    let symbol: String
    let bid: Double?
    let ask: Double?
    let timestamp: Date

    var mid: Double? {
        switch (bid, ask) {
        case let (bid?, ask?): return (bid + ask) / 2
        case let (bid?, nil): return bid
        case let (nil, ask?): return ask
        default: return nil
        }
    }
}

protocol MarketDataProvider: Sendable {
    var providerID: String { get }

    func candles(
        symbol: String,
        timeframe: LondresTimeframe,
        limit: Int
    ) async throws -> [MarketCandle]

    func quote(symbol: String) async throws -> MarketQuote

    func quoteStream(symbol: String) -> AsyncThrowingStream<MarketQuote, Error>
}

enum MarketDataError: Error, Equatable {
    case invalidSymbol
    case invalidLimit
    case malformedCandle
    case unavailable
    case staleData
}
