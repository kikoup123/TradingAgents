import XCTest
@testable import LondresTradingAI

final class LondresClockTests: XCTestCase {
    private let clock = LondresClock()

    func testTradingDayRollsAt1800FixedUTCMinus4() throws {
        let before = try XCTUnwrap(clock.canonicalDate(year: 2026, month: 9, day: 14, hour: 17, minute: 59))
        let after = try XCTUnwrap(clock.canonicalDate(year: 2026, month: 9, day: 14, hour: 18, minute: 0))

        let beforeLabel = clock.tradingDayLabel(for: before)
        let afterLabel = clock.tradingDayLabel(for: after)

        XCTAssertEqual(beforeLabel.year, 2026)
        XCTAssertEqual(beforeLabel.month, 9)
        XCTAssertEqual(beforeLabel.day, 14)

        XCTAssertEqual(afterLabel.year, 2026)
        XCTAssertEqual(afterLabel.month, 9)
        XCTAssertEqual(afterLabel.day, 15)
    }

    func testDailyQuarterSessionsUseFixedClock() throws {
        XCTAssertEqual(clock.session(for: try date(hour: 20)), .asia)
        XCTAssertEqual(clock.session(for: try date(hour: 2)), .london)
        XCTAssertEqual(clock.session(for: try date(hour: 8)), .newYorkAM)
        XCTAssertEqual(clock.session(for: try date(hour: 14)), .newYorkPM)
    }

    private func date(hour: Int) throws -> Date {
        try XCTUnwrap(clock.canonicalDate(year: 2026, month: 9, day: 16, hour: hour))
    }
}
