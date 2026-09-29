import XCTest
@testable import LondresTradingAI

final class SMTEngineTests: XCTestCase {
    func testUSIndexLowSideNonconfirmationCreatesBullishSMT() throws {
        let input = [
            "NQ": triadBars(symbol: "NQ", finalHigh: 106, finalLow: 89),
            "ES": triadBars(symbol: "ES", finalHigh: 106, finalLow: 91),
            "YM": triadBars(symbol: "YM", finalHigh: 106, finalLow: 92)
        ]

        let result = try SMTEngine(pivotSpan: 1).analyze(
            instrumentBars: input,
            group: "US_INDEX",
            timeframe: "M5"
        )

        XCTAssertTrue(result.detected)
        XCTAssertFalse(result.validated)
        XCTAssertEqual(result.direction, .bullish)
        XCTAssertEqual(result.divergenceType, "LOW_SIDE_NONCONFIRMATION")
        XCTAssertEqual(result.leaderSymbols, ["NQ"])
        XCTAssertEqual(result.validationState, .detectedWaitCSD)
    }

    func testSMTOnlyValidatesAfterSameDirectionCSDAndIOF() throws {
        let input = [
            "NAS100": triadBars(symbol: "NQ", finalHigh: 106, finalLow: 89),
            "US500": triadBars(symbol: "ES", finalHigh: 106, finalLow: 91),
            "US30": triadBars(symbol: "YM", finalHigh: 106, finalLow: 92)
        ]

        let result = try SMTEngine(pivotSpan: 1).analyze(
            instrumentBars: input,
            group: "US_INDEX",
            timeframe: "M5",
            csdDirection: .bullish,
            iofDirection: .bullish
        )

        XCTAssertTrue(result.detected)
        XCTAssertTrue(result.validated)
        XCTAssertEqual(result.validationState, .validated)
    }

    func testDXYInversePolarityIsNormalizedBeforeDivergenceComparison() throws {
        let input = [
            "EURUSD": triadBars(symbol: "EURUSD", finalHigh: 111, finalLow: 92),
            "GBPUSD": triadBars(symbol: "GBPUSD", finalHigh: 109, finalLow: 92),
            "DXY": triadBars(symbol: "DXY", finalHigh: 106, finalLow: 89)
        ]

        let result = try SMTEngine(pivotSpan: 1).analyze(
            instrumentBars: input,
            group: "FX_DXY",
            timeframe: "M5",
            csdDirection: .bearish,
            iofDirection: .bearish
        )

        XCTAssertTrue(result.detected)
        XCTAssertEqual(result.direction, .bearish)
        XCTAssertEqual(result.divergenceType, "HIGH_SIDE_NONCONFIRMATION")
        XCTAssertTrue(result.leaderSymbols.contains("EURUSD"))
        XCTAssertTrue(result.leaderSymbols.contains("DXY"))
        XCTAssertEqual(result.polarityMap["DXY"], .inverse)
        XCTAssertTrue(result.reasonCodes.contains("INVERSE_POLARITY_NORMALIZED"))
        XCTAssertTrue(result.validated)
    }

    private func triadBars(symbol: String, finalHigh: Double, finalLow: Double) -> [MarketCandle] {
        [
            candle(symbol, 0, high: 105, low: 95),
            candle(symbol, 1, high: 110, low: 90),
            candle(symbol, 2, high: 105, low: 95),
            candle(symbol, 3, high: finalHigh, low: finalLow)
        ]
    }

    private func candle(_ symbol: String, _ index: Int, high: Double, low: Double) -> MarketCandle {
        let mid = (high + low) / 2
        return MarketCandle(
            symbol: symbol,
            timeframe: .fiveMinute,
            openTime: Date(timeIntervalSince1970: TimeInterval(index * 300)),
            open: mid,
            high: high,
            low: low,
            close: mid,
            volume: 100
        )
    }
}
