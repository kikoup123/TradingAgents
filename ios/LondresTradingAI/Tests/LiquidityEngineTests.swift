import XCTest
@testable import LondresTradingAI

final class LiquidityEngineTests: XCTestCase {
    func testStructuralPoolsProtectedLiquidityActiveDrawAndDealingRange() throws {
        let bars = [
            bar(0, high: 100, low: 95, close: 98),
            bar(1, high: 110, low: 97, close: 105),
            bar(2, high: 103, low: 90, close: 95),
            bar(3, high: 108, low: 96, close: 104),
            bar(4, high: 102, low: 92, close: 95),
            bar(5, high: 106, low: 94, close: 100),
            bar(6, high: 101, low: 93, close: 98)
        ]

        let result = try LiquidityEngine(pivotSpan: 1).analyze(
            bars: bars,
            timeframe: "H1",
            orderFlowControl: .bullish
        )

        XCTAssertEqual(result.externalHigh, 110)
        XCTAssertEqual(result.externalLow, 90)
        XCTAssertEqual(result.dealingRangeEquilibrium, 100)
        XCTAssertEqual(result.dealingRangeLocation, "DISCOUNT")
        XCTAssertEqual(result.protectedPool?.side, .sellSide)
        XCTAssertEqual(result.protectedPool?.sourcePosition, 4)
        XCTAssertEqual(result.activeDraw?.side, .buySide)
        XCTAssertEqual(result.activeDraw?.price, 110)
        XCTAssertEqual(result.activeDraw?.liquidityClass, .external)
    }

    func testBuySideWickRaidAndReclaimMatchesPythonClassification() throws {
        let bars = [
            bar(0, high: 95, low: 90, close: 93),
            bar(1, high: 100, low: 92, close: 98),
            bar(2, high: 96, low: 91, close: 94),
            bar(3, high: 101, low: 93, close: 99)
        ]

        let result = try LiquidityEngine(pivotSpan: 1).analyze(
            bars: bars,
            timeframe: "H1",
            orderFlowControl: .unconfirmed
        )

        let pool = try XCTUnwrap(result.buySide.first(where: { $0.price == 100 }))
        XCTAssertEqual(pool.status, .raidedReclaimed)
        XCTAssertEqual(pool.eventPosition, 3)
        XCTAssertEqual(pool.eventClose, 99)
    }

    func testBodyCloseThroughLiquidityConsumesPool() throws {
        let bars = [
            bar(0, high: 95, low: 90, close: 93),
            bar(1, high: 100, low: 92, close: 98),
            bar(2, high: 96, low: 91, close: 94),
            bar(3, high: 102, low: 94, close: 101)
        ]

        let result = try LiquidityEngine(pivotSpan: 1).analyze(
            bars: bars,
            timeframe: "H1"
        )

        let pool = try XCTUnwrap(result.buySide.first(where: { $0.price == 100 }))
        XCTAssertEqual(pool.status, .consumed)
    }

    func testNamedONSHighLowBecomeExternalLiquidity() throws {
        let start = Date(timeIntervalSince1970: 0)
        let end = Date(timeIntervalSince1970: 60)
        let state = TimePriceResult(
            tradingDay: "1970-01-01",
            canonicalClock: "UTC-4_FIXED",
            currentPrice: 100,
            asOf: end,
            opens: [:],
            ons: [
                "asia_ons": ONSRangeResult(
                    key: "asia_ons",
                    label: "Asia",
                    sourceTimeZone: "UTC",
                    rangeType: .wicks,
                    startTime: start,
                    endTime: end,
                    high: 110,
                    low: 90,
                    equilibrium: 100,
                    rangeSize: 20,
                    upperProjections: [],
                    lowerProjections: [],
                    status: .complete
                )
            ]
        )
        let bars = [
            bar(0, high: 101, low: 99, close: 100),
            bar(1, high: 102, low: 98, close: 101),
            bar(2, high: 103, low: 97, close: 102)
        ]

        let result = try LiquidityEngine(pivotSpan: 1).analyze(
            bars: bars,
            timeframe: "M15",
            orderFlowControl: .bullish,
            timePriceState: state
        )

        XCTAssertTrue(result.buySide.contains(where: {
            $0.sourceKind == "ASIA_ONS_HIGH" && $0.price == 110 && $0.liquidityClass == .external
        }))
        XCTAssertTrue(result.sellSide.contains(where: {
            $0.sourceKind == "ASIA_ONS_LOW" && $0.price == 90 && $0.liquidityClass == .external
        }))
    }

    private func bar(
        _ minute: Int,
        high: Double,
        low: Double,
        close: Double
    ) -> MarketCandle {
        let open = min(max(close, low), high)
        return MarketCandle(
            symbol: "NQ",
            timeframe: .oneHour,
            openTime: Date(timeIntervalSince1970: TimeInterval(minute * 60)),
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }
}
