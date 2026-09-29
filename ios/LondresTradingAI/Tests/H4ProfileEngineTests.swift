import XCTest
@testable import LondresTradingAI

final class H4ProfileEngineTests: XCTestCase {
    private let clock = LondresClock()

    func testPreDriverReversalTurnsSixAMDriverIntoContinuation() throws {
        let daily = bullishDailyProfile()
        let bars = [
            candle(2026, 9, 15, 18, open: 100, high: 102, low: 97, close: 99),
            candle(2026, 9, 15, 22, open: 99, high: 103, low: 98, close: 102),
            candle(2026, 9, 16, 2, open: 102, high: 105, low: 100, close: 104),
            candle(2026, 9, 16, 6, open: 104, high: 110, low: 103, close: 109),
            candle(2026, 9, 16, 10, open: 109, high: 111, low: 108, close: 110)
        ]
        let reversal = clock.canonicalDate(year: 2026, month: 9, day: 16, hour: 3)!

        let result = try H4ProfileEngine().analyze(
            intradayBars: bars,
            dailyProfile: daily,
            h4OrderFlowControl: .bullish,
            reversalConfirmedAt: reversal,
            locationContext: .irl
        )

        XCTAssertEqual(result.profile, .nyContinuation)
        XCTAssertEqual(result.status, .confirmed)
        XCTAssertEqual(result.phase, .postDriverExpansion)
        XCTAssertEqual(result.expectedDriverAction, "CONTINUATION")
        XCTAssertEqual(result.reversalBeforeDriver, true)
    }

    func testNoPreDriverReversalRequiresSixAMReversalEvidence() throws {
        let daily = bullishDailyProfile()
        let bars = [
            candle(2026, 9, 15, 18, open: 100, high: 103, low: 98, close: 101),
            candle(2026, 9, 15, 22, open: 101, high: 104, low: 97, close: 102),
            candle(2026, 9, 16, 2, open: 102, high: 105, low: 96, close: 103),
            candle(2026, 9, 16, 6, open: 103, high: 110, low: 95, close: 109),
            candle(2026, 9, 16, 10, open: 109, high: 112, low: 108, close: 111)
        ]

        let result = try H4ProfileEngine().analyze(
            intradayBars: bars,
            dailyProfile: daily,
            h4OrderFlowControl: .bullish
        )

        XCTAssertEqual(result.profile, .nyReversal)
        XCTAssertEqual(result.status, .confirmed)
        XCTAssertEqual(result.phase, .postDriverExpansion)
        XCTAssertEqual(result.expectedDriverAction, "REVERSAL")
        XCTAssertEqual(result.reversalBeforeDriver, false)
    }

    func testCompletedDriverThatFailsRequiredReversalInvalidates() throws {
        let daily = bullishDailyProfile()
        let bars = [
            candle(2026, 9, 15, 18, open: 100, high: 103, low: 98, close: 101),
            candle(2026, 9, 15, 22, open: 101, high: 104, low: 97, close: 102),
            candle(2026, 9, 16, 2, open: 102, high: 105, low: 96, close: 103),
            candle(2026, 9, 16, 6, open: 103, high: 104, low: 99, close: 100),
            candle(2026, 9, 16, 10, open: 100, high: 102, low: 99, close: 101)
        ]

        let result = try H4ProfileEngine().analyze(
            intradayBars: bars,
            dailyProfile: daily,
            h4OrderFlowControl: .bullish
        )

        XCTAssertEqual(result.status, .invalidated)
        XCTAssertEqual(result.phase, .invalidated)
        XCTAssertEqual(result.reasonCodes, ["0600_DRIVER_FAILED_TO_REVERSE"])
    }

    private func bullishDailyProfile() -> DailyProfileResult {
        DailyProfileResult(
            tradingDay: "2026-09-16",
            direction: .bullish,
            dayType: .continuationCandidate,
            expectedDelivery: .olhc,
            observedDelivery: .olhc,
            status: .confirmed,
            phase: .expansion,
            dailyOpen: 100,
            dailyHigh: 110,
            dailyLow: 95,
            currentClose: 109,
            highTime: Date(),
            lowTime: Date(),
            protectedExtreme: true,
            expectedNextPhase: nil,
            reasonCodes: []
        )
    }

    private func candle(
        _ year: Int,
        _ month: Int,
        _ day: Int,
        _ hour: Int,
        open: Double,
        high: Double,
        low: Double,
        close: Double
    ) -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: .oneHour,
            openTime: clock.canonicalDate(year: year, month: month, day: day, hour: hour)!,
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }
}
