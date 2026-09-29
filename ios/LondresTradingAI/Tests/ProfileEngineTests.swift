import XCTest
@testable import LondresTradingAI

final class ProfileEngineTests: XCTestCase {
    private let clock = LondresClock()

    func testClassicExpansionFridayBecomesRetracementCandidate() throws {
        let bars = [
            daily(2026, 9, 14, open: 100, high: 106, low: 90, close: 104),
            daily(2026, 9, 15, open: 104, high: 110, low: 101, close: 108),
            daily(2026, 9, 16, open: 108, high: 114, low: 105, close: 112),
            daily(2026, 9, 17, open: 112, high: 118, low: 109, close: 116),
            daily(2026, 9, 18, open: 116, high: 120, low: 113, close: 115)
        ]

        let result = try WeeklyProfileEngine().analyze(
            dailyBars: bars,
            htfControl: .bullish,
            protectedWeeklyExtreme: true
        )

        XCTAssertEqual(result.profile, .classicExpansion)
        XCTAssertEqual(result.status, .confirmed)
        XCTAssertEqual(result.weeklyExtreme?.day, "MONDAY")
        XCTAssertEqual(result.currentDayType, .retracementCandidate)
        XCTAssertEqual(result.weekPhase, "FRIDAY_REBALANCE")
        XCTAssertEqual(result.expectedDailyDelivery, "OLHC")
    }

    func testThursdayReversalFridayBranchesByOpposingDraw() throws {
        let bars = [
            daily(2026, 9, 14, open: 100, high: 104, low: 98, close: 102),
            daily(2026, 9, 15, open: 102, high: 106, low: 99, close: 104),
            daily(2026, 9, 16, open: 104, high: 108, low: 101, close: 106),
            daily(2026, 9, 17, open: 106, high: 107, low: 90, close: 103),
            daily(2026, 9, 18, open: 103, high: 112, low: 101, close: 110)
        ]

        let continuation = try WeeklyProfileEngine().analyze(
            dailyBars: bars,
            htfControl: .bullish,
            protectedWeeklyExtreme: true,
            opposingDrawReached: false
        )
        XCTAssertEqual(continuation.profile, .thursdayReversal)
        XCTAssertEqual(continuation.currentDayType, .continuationCandidate)
        XCTAssertEqual(continuation.weekPhase, "FRIDAY_CONTINUATION")

        let rebalance = try WeeklyProfileEngine().analyze(
            dailyBars: bars,
            htfControl: .bullish,
            protectedWeeklyExtreme: true,
            opposingDrawReached: true
        )
        XCTAssertEqual(rebalance.currentDayType, .returnToRangeCandidate)
        XCTAssertEqual(rebalance.weekPhase, "FRIDAY_RETURN_TO_RANGE")
    }

    func testBullishDailyOLHCConfirmsWithOrderFlowAndProtectedExtreme() throws {
        let weekly = WeeklyProfileResult(
            profile: .classicExpansion,
            status: .confirmed,
            direction: .bullish,
            currentDay: "WEDNESDAY",
            currentDayType: .continuationCandidate,
            weekPhase: "WEEKLY_EXPANSION",
            weeklyExtreme: WeeklyExtreme(extremeType: "LOW", day: "MONDAY", price: 90, isProtected: true),
            expectedDailyDelivery: "OLHC",
            expectedNextPhase: nil,
            reasonCodes: []
        )
        let bars = [
            intraday(2026, 9, 15, 18, open: 100, high: 102, low: 98, close: 99),
            intraday(2026, 9, 15, 22, open: 99, high: 101, low: 95, close: 100),
            intraday(2026, 9, 16, 2, open: 100, high: 108, low: 99, close: 107),
            intraday(2026, 9, 16, 6, open: 107, high: 110, low: 105, close: 109)
        ]

        let result = try DailyProfileEngine().analyze(
            intradayBars: bars,
            weeklyProfile: weekly,
            dailyOrderFlowControl: .bullish,
            protectedDailyExtreme: true,
            opposingDrawReached: false
        )

        XCTAssertEqual(result.tradingDay, "2026-09-16")
        XCTAssertEqual(result.expectedDelivery, .olhc)
        XCTAssertEqual(result.observedDelivery, .olhc)
        XCTAssertEqual(result.status, .confirmed)
        XCTAssertEqual(result.phase, .expansion)
        XCTAssertEqual(result.expectedNextPhase, "CONTINUE_TOWARD_OPPOSING_DRAW")
    }

    func testFailedProtectedDailyExtremeInvalidatesProfile() throws {
        let weekly = WeeklyProfileResult(
            profile: .classicExpansion,
            status: .confirmed,
            direction: .bullish,
            currentDay: "WEDNESDAY",
            currentDayType: .continuationCandidate,
            weekPhase: "WEEKLY_EXPANSION",
            weeklyExtreme: nil,
            expectedDailyDelivery: "OLHC",
            expectedNextPhase: nil,
            reasonCodes: []
        )
        let bars = [
            intraday(2026, 9, 15, 18, open: 100, high: 101, low: 97, close: 99),
            intraday(2026, 9, 16, 2, open: 99, high: 108, low: 95, close: 106)
        ]

        let result = try DailyProfileEngine().analyze(
            intradayBars: bars,
            weeklyProfile: weekly,
            dailyOrderFlowControl: .bullish,
            protectedDailyExtreme: false
        )

        XCTAssertEqual(result.status, .invalidated)
        XCTAssertEqual(result.phase, .invalidated)
        XCTAssertEqual(result.expectedNextPhase, "RECLASSIFY_DAILY_PROFILE")
    }

    private func daily(
        _ year: Int,
        _ month: Int,
        _ day: Int,
        open: Double,
        high: Double,
        low: Double,
        close: Double
    ) -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: .daily,
            openTime: clock.canonicalDate(year: year, month: month, day: day, hour: 12)!,
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }

    private func intraday(
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
