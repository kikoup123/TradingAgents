import XCTest
@testable import LondresTradingAI

final class OrderFlowEngineTests: XCTestCase {
    func testBullishIOFRequiresBodyCloseAboveDownCloseRange() throws {
        let bars = [
            candle(0, open: 100, high: 102, low: 95, close: 97),
            candle(1, open: 97, high: 103, low: 96, close: 101),
            candle(2, open: 101, high: 104, low: 100, close: 103)
        ]

        let result = try OrderFlowEngine().analyze(bars: bars, timeframe: "M5")

        XCTAssertEqual(result.control, .bullish)
        let range = try XCTUnwrap(result.latestEvent)
        XCTAssertEqual(range.direction, .bullish)
        XCTAssertEqual(range.role, .support)
        XCTAssertEqual(range.sourcePosition, 0)
        XCTAssertEqual(range.confirmedPosition, 2)
        XCTAssertEqual(range.status, .confirmed)
    }

    func testWickAboveRangeDoesNotConfirmBullishIOF() throws {
        let bars = [
            candle(0, open: 100, high: 102, low: 95, close: 97),
            candle(1, open: 97, high: 104, low: 96, close: 101)
        ]

        let result = try OrderFlowEngine().analyze(bars: bars, timeframe: "M5")

        XCTAssertEqual(result.control, .unconfirmed)
        XCTAssertTrue(result.confirmedEvents.isEmpty)
    }

    func testInvalidatedLatestRangeTransitionsWithoutOpposingConfirmation() throws {
        let bars = [
            candle(0, open: 100, high: 102, low: 95, close: 97),
            candle(1, open: 97, high: 104, low: 96, close: 103),
            candle(2, open: 103, high: 104, low: 93, close: 94)
        ]

        let result = try OrderFlowEngine().analyze(bars: bars, timeframe: "M5")

        XCTAssertEqual(result.control, .transition)
        XCTAssertEqual(result.latestEvent?.status, .invalidated)
        XCTAssertNotNil(result.transitionReason)
    }

    func testPostCSDIOFCOnlyAcceptsNewRangeAfterAnchor() throws {
        let bars = [
            candle(0, open: 100, high: 102, low: 95, close: 97),
            candle(1, open: 97, high: 104, low: 96, close: 103),
            candle(2, open: 103, high: 105, low: 100, close: 101),
            candle(3, open: 101, high: 106, low: 100, close: 105)
        ]

        let confirmed = try OrderFlowEngine().findIOFCAfter(
            bars: bars,
            anchorPosition: 1,
            expectedDirection: .bullish
        )

        XCTAssertTrue(confirmed.confirmed)
        XCTAssertEqual(confirmed.confirmationRange?.sourcePosition, 2)
        XCTAssertEqual(confirmed.reason, "BODY_CLOSE_CONFIRMED_POST_CSD_IOFC")
    }

    private func candle(
        _ minute: Int,
        open: Double,
        high: Double,
        low: Double,
        close: Double
    ) -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: .fiveMinute,
            openTime: Date(timeIntervalSince1970: TimeInterval(minute * 300)),
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }
}
