import XCTest
@testable import LondresTradingAI

final class LondresMarketSnapshotBuilderTests: XCTestCase {
    func testSnapshotUsesFiveMinuteExecutionAndSMTWhileKeepingHTFContext() async throws {
        let provider = MockMarketDataProvider()
        let snapshot = try await LondresMarketSnapshotBuilder().build(
            provider: provider,
            plan: .nq
        )

        XCTAssertEqual(snapshot.providerID, "mock-market-data")
        XCTAssertEqual(snapshot.strategyInput.csdTimeframe, "5m")
        XCTAssertEqual(snapshot.strategyInput.smtTimeframe, "5m")
        XCTAssertEqual(snapshot.strategyInput.hierarchy.last, "5m")
        XCTAssertNil(snapshot.strategyInput.timeframeBars["1m"])
        XCTAssertTrue(snapshot.executionBars.allSatisfy { $0.timeframe == .fiveMinute })
        XCTAssertTrue(snapshot.strategyInput.csdBars.allSatisfy { $0.timeframe == .fiveMinute })
        XCTAssertTrue(
            snapshot.strategyInput.smtBars.values
                .flatMap { $0 }
                .allSatisfy { $0.timeframe == .fiveMinute }
        )
        XCTAssertTrue(snapshot.strategyInput.minuteBars.allSatisfy { $0.timeframe == .oneMinute })
        XCTAssertNotNil(snapshot.strategyInput.timeframeBars["1W"])
        XCTAssertNotNil(snapshot.strategyInput.timeframeBars["1D"])
        XCTAssertNotNil(snapshot.strategyInput.timeframeBars["4H"])
        XCTAssertNotNil(snapshot.strategyInput.timeframeBars["1H"])
        XCTAssertNotNil(snapshot.strategyInput.timeframeBars["15m"])
    }
}

private final class MockMarketDataProvider: MarketDataProvider, @unchecked Sendable {
    let providerID = "mock-market-data"
    private let now = Date(timeIntervalSince1970: 1_900_000_000)

    func candles(
        symbol: String,
        timeframe: LondresTimeframe,
        limit: Int
    ) async throws -> [MarketCandle] {
        let count = min(max(limit, 1), 24)
        let step = seconds(for: timeframe)
        return (0..<count).map { index in
            let base = 100.0 + Double(index) * 0.25
            return MarketCandle(
                symbol: symbol.uppercased(),
                timeframe: timeframe,
                openTime: now.addingTimeInterval(-Double(count - index) * step),
                open: base,
                high: base + 1,
                low: base - 1,
                close: base + 0.25,
                volume: 100
            )
        }
    }

    func quote(symbol: String) async throws -> MarketQuote {
        MarketQuote(
            symbol: symbol.uppercased(),
            bid: 105.0,
            ask: 105.25,
            timestamp: now
        )
    }

    func quoteStream(symbol: String) -> AsyncThrowingStream<MarketQuote, Error> {
        AsyncThrowingStream { continuation in
            continuation.yield(
                MarketQuote(
                    symbol: symbol.uppercased(),
                    bid: 105.0,
                    ask: 105.25,
                    timestamp: now
                )
            )
            continuation.finish()
        }
    }

    private func seconds(for timeframe: LondresTimeframe) -> TimeInterval {
        switch timeframe {
        case .weekly: return 7 * 24 * 60 * 60
        case .daily: return 24 * 60 * 60
        case .fourHour: return 4 * 60 * 60
        case .oneHour: return 60 * 60
        case .fifteenMinute: return 15 * 60
        case .fiveMinute: return 5 * 60
        case .threeMinute: return 3 * 60
        case .oneMinute: return 60
        }
    }
}
