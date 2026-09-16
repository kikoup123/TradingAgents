import XCTest
@testable import LondresTradingAI

final class TimePriceEngineTests: XCTestCase {
    private let clock = LondresClock()

    func testNewYorkOpenRespectsDSTWhileCanonicalClockStaysFixedUTCMinus4() throws {
        // December New York is UTC-5. 08:30 New York therefore appears at
        // 09:30 on the strategy's fixed UTC-4 canonical clock.
        let candle = try bar(
            year: 2026,
            month: 12,
            day: 15,
            hour: 9,
            minute: 30,
            open: 100,
            high: 102,
            low: 99,
            close: 101
        )
        let engine = TimePriceEngine(
            openSpecs: [OpenSpec(key: "open_0830", label: "08:30", hour: 8, minute: 30)],
            onsConfigs: []
        )

        let result = try engine.analyze(minuteBars: [candle])
        let level = try XCTUnwrap(result.opens["open_0830"])

        XCTAssertEqual(result.canonicalClock, "UTC-4_FIXED")
        XCTAssertTrue(level.available)
        XCTAssertEqual(level.price, 100)
        XCTAssertEqual(level.timestamp, candle.openTime)
    }

    func testMissingExactOpeningMinuteIsUnavailableRatherThanGuessed() throws {
        let bars = [
            try bar(year: 2026, month: 9, day: 16, hour: 8, minute: 29, open: 100, high: 101, low: 99, close: 100),
            try bar(year: 2026, month: 9, day: 16, hour: 8, minute: 31, open: 102, high: 103, low: 101, close: 102)
        ]
        let engine = TimePriceEngine(
            openSpecs: [OpenSpec(key: "open_0830", label: "08:30", hour: 8, minute: 30)],
            onsConfigs: []
        )

        let result = try engine.analyze(minuteBars: bars)
        let level = try XCTUnwrap(result.opens["open_0830"])

        XCTAssertFalse(level.available)
        XCTAssertNil(level.price)
        XCTAssertEqual(level.relation, .unavailable)
    }

    func testOpeningLevelTracksTouchAndBodyCross() throws {
        let bars = [
            try bar(year: 2026, month: 9, day: 16, hour: 8, minute: 30, open: 100, high: 100.5, low: 99.5, close: 101),
            try bar(year: 2026, month: 9, day: 16, hour: 8, minute: 31, open: 101, high: 102, low: 99.8, close: 101.5),
            try bar(year: 2026, month: 9, day: 16, hour: 8, minute: 32, open: 101.5, high: 101.5, low: 98.5, close: 99)
        ]
        let engine = TimePriceEngine(
            openSpecs: [OpenSpec(key: "open_0830", label: "08:30", hour: 8, minute: 30)],
            onsConfigs: []
        )

        let result = try engine.analyze(minuteBars: bars, currentPrice: 99)
        let level = try XCTUnwrap(result.opens["open_0830"])

        XCTAssertTrue(level.touched)
        XCTAssertTrue(level.crossed)
        XCTAssertEqual(level.lastCrossTime, bars[2].openTime)
        XCTAssertEqual(level.relation, .below)
    }

    func testONSRangeAndHalfDeviationProjection() throws {
        let bars = [
            try bar(year: 2026, month: 9, day: 16, hour: 6, minute: 0, open: 100, high: 103, low: 98, close: 101),
            try bar(year: 2026, month: 9, day: 16, hour: 7, minute: 0, open: 101, high: 105, low: 95, close: 103),
            try bar(year: 2026, month: 9, day: 16, hour: 8, minute: 0, open: 103, high: 104, low: 102, close: 103)
        ]
        let config = ONSConfig(
            key: "test_ons",
            label: "Test ONS",
            timeZoneIdentifier: "America/New_York",
            startHour: 6,
            startMinute: 0,
            endHour: 8,
            endMinute: 0,
            rangeType: .wicks,
            showHalfDeviations: true,
            projectionCount: 2
        )
        let engine = TimePriceEngine(openSpecs: [], onsConfigs: [config])

        let result = try engine.analyze(minuteBars: bars, asOf: bars[2].openTime)
        let ons = try XCTUnwrap(result.ons["test_ons"])

        XCTAssertEqual(ons.status, .complete)
        XCTAssertEqual(ons.high, 105)
        XCTAssertEqual(ons.low, 95)
        XCTAssertEqual(ons.equilibrium, 100)
        XCTAssertEqual(ons.rangeSize, 10)
        XCTAssertEqual(ons.upperProjections.first?.deviation, 0.5)
        XCTAssertEqual(ons.upperProjections.first?.price, 110)
        XCTAssertEqual(ons.lowerProjections.first?.price, 90)
    }

    private func bar(
        year: Int,
        month: Int,
        day: Int,
        hour: Int,
        minute: Int,
        open: Double,
        high: Double,
        low: Double,
        close: Double
    ) throws -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: .oneMinute,
            openTime: try XCTUnwrap(
                clock.canonicalDate(year: year, month: month, day: day, hour: hour, minute: minute)
            ),
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }
}
