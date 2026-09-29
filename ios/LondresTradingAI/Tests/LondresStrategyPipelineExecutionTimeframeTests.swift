import XCTest
@testable import LondresTradingAI

final class LondresStrategyPipelineExecutionTimeframeTests: XCTestCase {
    func testExecutionHierarchyStopsAtFiveMinutes() throws {
        let hierarchy = try LondresStrategyPipeline.executionHierarchy(
            from: NarrativeEngine.defaultHierarchy
        )

        XCTAssertEqual(hierarchy.last, "5m")
        XCTAssertFalse(hierarchy.contains("1m"))
        XCTAssertTrue(hierarchy.contains("4H"))
        XCTAssertTrue(hierarchy.contains("1H"))
        XCTAssertTrue(hierarchy.contains("15m"))
    }

    func testPipelineRejectsNonFiveMinuteCSDTimeframeBeforeAnalysis() {
        let input = LondresStrategyInput(
            timeframeBars: [:],
            intradayBars: [],
            minuteBars: [],
            csdBars: [],
            smtBars: [:],
            smtGroup: "indices",
            smtTimeframe: "5m",
            csdTimeframe: "1m",
            instrument: LondresInstrumentMetadata(symbol: "NQ", tickSize: 0.25)
        )

        XCTAssertThrowsError(try LondresStrategyPipeline().analyze(input)) { error in
            XCTAssertEqual(
                error as? LondresStrategyPipelineError,
                .invalidExecutionTimeframe("1m")
            )
        }
    }

    func testPipelineRejectsNonM5CandlesInsideExecutionStream() {
        let candle = MarketCandle(
            symbol: "NQ",
            timeframe: .oneMinute,
            openTime: Date(timeIntervalSince1970: 1_700_000_000),
            open: 20_000,
            high: 20_010,
            low: 19_990,
            close: 20_005,
            volume: 100
        )
        let input = LondresStrategyInput(
            timeframeBars: ["5m": [candle]],
            intradayBars: [],
            minuteBars: [],
            csdBars: [candle],
            smtBars: [:],
            smtGroup: "indices",
            smtTimeframe: "5m",
            csdTimeframe: "5m",
            instrument: LondresInstrumentMetadata(symbol: "NQ", tickSize: 0.25)
        )

        XCTAssertThrowsError(try LondresStrategyPipeline().analyze(input)) { error in
            XCTAssertEqual(
                error as? LondresStrategyPipelineError,
                .invalidExecutionCandleTimeframe
            )
        }
    }

    func testExecutionHierarchyMustContainFiveMinutes() {
        XCTAssertThrowsError(
            try LondresStrategyPipeline.executionHierarchy(
                from: ["1W", "1D", "4H", "1H", "15m", "1m"]
            )
        ) { error in
            XCTAssertEqual(
                error as? LondresStrategyPipelineError,
                .invalidExecutionHierarchy
            )
        }
    }
}
