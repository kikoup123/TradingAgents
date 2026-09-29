import XCTest
@testable import LondresTradingAI

final class CSDEngineTests: XCTestCase {
    func testBullishCSDRequiresSellSideRaidThenBodyCloseAboveThresholdOpen() throws {
        let bars = [
            candle(0, open: 105, high: 106, low: 100, close: 104),
            candle(1, open: 104, high: 105, low: 95, close: 96),
            candle(2, open: 96, high: 103, low: 96, close: 102),
            candle(3, open: 102, high: 103, low: 94, close: 100),
            candle(4, open: 100, high: 105, low: 96, close: 103)
        ]

        let result = try CSDEngine(pivotSpan: 1).analyze(bars: bars, timeframe: "M5")

        XCTAssertTrue(result.confirmed)
        XCTAssertEqual(result.direction, .bullish)
        let event = try XCTUnwrap(result.latestEvent)
        XCTAssertEqual(event.liquiditySide, "SELL_SIDE")
        XCTAssertEqual(event.liquidityReference.price, 95)
        XCTAssertEqual(event.raidPosition, 3)
        XCTAssertEqual(event.thresholdOpen, 102)
        XCTAssertEqual(event.confirmationPosition, 4)
        XCTAssertEqual(event.protectedExtreme, 94)
    }

    func testWickThroughThresholdDoesNotConfirmCSD() throws {
        let bars = [
            candle(0, open: 105, high: 106, low: 100, close: 104),
            candle(1, open: 104, high: 105, low: 95, close: 96),
            candle(2, open: 96, high: 103, low: 96, close: 102),
            candle(3, open: 102, high: 103, low: 94, close: 100),
            candle(4, open: 100, high: 105, low: 96, close: 101)
        ]

        let result = try CSDEngine(pivotSpan: 1).analyze(bars: bars, timeframe: "M5")

        XCTAssertFalse(result.confirmed)
        XCTAssertEqual(result.direction, .unconfirmed)
        XCTAssertNil(result.latestEvent)
    }

    func testLatestEventAtOrAfterFiltersByConfirmationTime() throws {
        let bars = [
            candle(0, open: 105, high: 106, low: 100, close: 104),
            candle(1, open: 104, high: 105, low: 95, close: 96),
            candle(2, open: 96, high: 103, low: 96, close: 102),
            candle(3, open: 102, high: 103, low: 94, close: 100),
            candle(4, open: 100, high: 105, low: 96, close: 103)
        ]
        let result = try CSDEngine(pivotSpan: 1).analyze(bars: bars, timeframe: "M5")
        let before = Date(timeIntervalSince1970: 3 * 300)
        let after = Date(timeIntervalSince1970: 5 * 300)

        XCTAssertNotNil(result.latestEvent(atOrAfter: before))
        XCTAssertNil(result.latestEvent(atOrAfter: after))
    }

    private func candle(
        _ index: Int,
        open: Double,
        high: Double,
        low: Double,
        close: Double
    ) -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: .fiveMinute,
            openTime: Date(timeIntervalSince1970: TimeInterval(index * 300)),
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }
}
