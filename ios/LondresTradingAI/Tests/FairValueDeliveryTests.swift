import XCTest
@testable import LondresTradingAI

final class FairValueDeliveryTests: XCTestCase {
    func testStructuralBullishFVGRecordsConfirmedReferenceAndRemainsContextOnly() throws {
        let bars = [
            candle(0, open: 98, high: 100, low: 95, close: 99),
            candle(1, open: 100, high: 110, low: 96, close: 105),
            candle(2, open: 104, high: 105, low: 97, close: 104),
            candle(3, open: 105, high: 112, low: 104, close: 111),
            candle(4, open: 108, high: 111, low: 107, close: 109)
        ]

        let result = try FairValueEngine(pivotSpan: 1).analyze(bars: bars, timeframe: "M5")
        let gap = try XCTUnwrap(result.gaps.first)

        XCTAssertEqual(gap.direction, .bullish)
        XCTAssertEqual(gap.low, 105)
        XCTAssertEqual(gap.high, 107)
        XCTAssertEqual(gap.structuralReference?.price, 110)
        XCTAssertEqual(gap.reasonCodes, ["THREE_CANDLE_FVG", "STRUCTURAL_CLOSE_THROUGH"])
        XCTAssertTrue(result.reasonCodes.contains("FVG_IS_CONTEXT_NOT_ENTRY_SIGNAL"))
    }

    func testDeliveryRequiresRaidBeforeDistributionAndCanRebalanceRedistribute() throws {
        let bars = [
            candle(0, open: 100, high: 105, low: 95, close: 102),
            candle(1, open: 102, high: 104, low: 90, close: 98),
            candle(2, open: 98, high: 103, low: 94, close: 100),
            candle(3, open: 100, high: 102, low: 89, close: 99),
            candle(4, open: 99, high: 110, low: 98, close: 108),
            candle(5, open: 106, high: 109, low: 105, close: 107),
            candle(6, open: 107, high: 108, low: 104, close: 106),
            candle(7, open: 106, high: 113, low: 105, close: 112)
        ]

        let result = try PriceDeliveryEngine(pivotSpan: 1, consolidationWindow: 3)
            .analyze(bars: bars, timeframe: "M5")

        XCTAssertEqual(result.direction, .bullish)
        XCTAssertEqual(result.sequence, .redistribute)
        XCTAssertEqual(result.phase, .expansion)
        XCTAssertEqual(result.originalConsolidation?.status, "DISPLACEMENT_CONFIRMED")
        XCTAssertTrue(result.events.contains(where: { $0.event == .neutralize && $0.position == 3 }))
        XCTAssertTrue(result.events.contains(where: { $0.event == .distribute && $0.position == 5 }))
        XCTAssertTrue(result.events.contains(where: { $0.event == .rebalance && $0.position == 6 }))
        XCTAssertTrue(result.events.contains(where: { $0.event == .reaccumulation && $0.position == 7 }))
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
