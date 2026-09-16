import XCTest
@testable import LondresTradingAI

final class PostCSDIOFEntryEngineTests: XCTestCase {
    func testBullishFirstReturnFromAboveFillsAtZoneHigh() throws {
        let iofc = bullishIOFC(low: 100, high: 102, confirmedPosition: 1)
        let bars = [
            candle(0, open: 99, high: 100, low: 98, close: 99),
            candle(1, open: 101, high: 104, low: 100, close: 103),
            candle(2, open: 105, high: 106, low: 101.5, close: 104)
        ]

        let result = try PostCSDIOFEntryEngine().analyze(iofc: iofc, executionBars: bars)

        XCTAssertEqual(result.status, .entryTriggered)
        XCTAssertEqual(result.entry?.price, 102)
        XCTAssertEqual(result.entry?.position, 2)
        XCTAssertEqual(result.entry?.fillBasis, "FIRST_TOUCH_OF_BULLISH_IOF_RANGE_HIGH")
    }

    func testBearishFirstReturnFromBelowFillsAtZoneLow() throws {
        let iofc = bearishIOFC(low: 100, high: 102, confirmedPosition: 1)
        let bars = [
            candle(0, open: 103, high: 104, low: 102, close: 103),
            candle(1, open: 101, high: 102, low: 98, close: 99),
            candle(2, open: 97, high: 100.5, low: 96, close: 98)
        ]

        let result = try PostCSDIOFEntryEngine().analyze(iofc: iofc, executionBars: bars)

        XCTAssertEqual(result.status, .entryTriggered)
        XCTAssertEqual(result.entry?.price, 100)
        XCTAssertEqual(result.entry?.fillBasis, "FIRST_TOUCH_OF_BEARISH_IOF_RANGE_LOW")
    }

    func testOpenInsideRangeFreezesBarOpenAsEntry() throws {
        let iofc = bullishIOFC(low: 100, high: 102, confirmedPosition: 0)
        let bars = [
            candle(0, open: 103, high: 104, low: 102, close: 103),
            candle(1, open: 101.25, high: 102, low: 100.5, close: 101.5)
        ]

        let result = try PostCSDIOFEntryEngine().analyze(iofc: iofc, executionBars: bars)

        XCTAssertEqual(result.status, .entryTriggered)
        XCTAssertEqual(result.entry?.price, 101.25)
        XCTAssertEqual(result.entry?.fillBasis, "BAR_OPEN_ALREADY_INSIDE_IOF_RANGE")
    }

    func testGapThroughEntireBullishRangeIsRejectedWithoutInventedFill() throws {
        let iofc = bullishIOFC(low: 100, high: 102, confirmedPosition: 0)
        let bars = [
            candle(0, open: 103, high: 104, low: 102, close: 103),
            candle(1, open: 99, high: 100.5, low: 98, close: 100)
        ]

        let result = try PostCSDIOFEntryEngine().analyze(iofc: iofc, executionBars: bars)

        XCTAssertEqual(result.status, .rangeSkippedByGap)
        XCTAssertNil(result.entry)
        XCTAssertEqual(result.reasonCodes, ["BULLISH_IOF_RANGE_GAPPED_THROUGH_WITHOUT_CAUSAL_FILL"])
    }

    func testBarsAtOrBeforeConfirmationCannotTriggerEntry() throws {
        let iofc = bullishIOFC(low: 100, high: 102, confirmedPosition: 2)
        let bars = [
            candle(0, open: 101, high: 102, low: 100, close: 101),
            candle(1, open: 101, high: 102, low: 100, close: 101),
            candle(2, open: 103, high: 104, low: 102.5, close: 103),
            candle(3, open: 104, high: 105, low: 103, close: 104)
        ]

        let result = try PostCSDIOFEntryEngine().analyze(iofc: iofc, executionBars: bars)

        XCTAssertEqual(result.status, .waitForRetrace)
        XCTAssertNil(result.entry)
    }

    private func bullishIOFC(low: Double, high: Double, confirmedPosition: Int) -> IOFCResult {
        IOFCResult(
            expectedDirection: .bullish,
            confirmed: true,
            confirmationRange: range(
                direction: .bullish,
                role: .support,
                low: low,
                high: high,
                confirmedPosition: confirmedPosition
            ),
            reason: "BODY_CLOSE_CONFIRMED_POST_CSD_IOFC"
        )
    }

    private func bearishIOFC(low: Double, high: Double, confirmedPosition: Int) -> IOFCResult {
        IOFCResult(
            expectedDirection: .bearish,
            confirmed: true,
            confirmationRange: range(
                direction: .bearish,
                role: .resistance,
                low: low,
                high: high,
                confirmedPosition: confirmedPosition
            ),
            reason: "BODY_CLOSE_CONFIRMED_POST_CSD_IOFC"
        )
    }

    private func range(
        direction: OrderFlowDirection,
        role: RangeRole,
        low: Double,
        high: Double,
        confirmedPosition: Int
    ) -> OrderFlowRange {
        var range = OrderFlowRange(
            direction: direction,
            role: role,
            sourcePosition: 0,
            sourceTime: Date(timeIntervalSince1970: 0),
            low: low,
            high: high,
            sourceOpen: (low + high) / 2,
            sourceClose: (low + high) / 2
        )
        range.status = .confirmed
        range.confirmedPosition = confirmedPosition
        range.confirmedTime = Date(timeIntervalSince1970: TimeInterval(confirmedPosition * 300))
        return range
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
