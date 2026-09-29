import XCTest
@testable import LondresTradingAI

final class LondresStrategyPipelineTests: XCTestCase {
    func testExecutionHierarchyStopsAtFiveMinuteAndExcludesLowerTimeframes() throws {
        let hierarchy = ["1W", "1D", "4H", "1H", "15m", "5m", "1m"]

        let executionHierarchy = try LondresStrategyPipeline.executionHierarchy(from: hierarchy)

        XCTAssertEqual(executionHierarchy, ["1W", "1D", "4H", "1H", "15m", "5m"])
        XCTAssertEqual(executionHierarchy.last, LondresStrategyPipeline.executionTimeframe)
        XCTAssertFalse(executionHierarchy.contains("1m"))
    }

    func testHierarchyWithoutFiveMinuteExecutionFailsClosed() {
        XCTAssertThrowsError(
            try LondresStrategyPipeline.executionHierarchy(from: ["1W", "1D", "4H", "1H", "15m", "1m"])
        ) { error in
            XCTAssertEqual(error as? LondresStrategyPipelineError, .invalidExecutionHierarchy)
        }
    }

    func testNonFiveMinuteCSDExecutionTimeframeFailsClosed() {
        let input = minimalInput(csdTimeframe: "1m")

        XCTAssertThrowsError(try LondresStrategyPipeline().analyze(input)) { error in
            XCTAssertEqual(
                error as? LondresStrategyPipelineError,
                .invalidExecutionTimeframe("1m")
            )
        }
    }

    func testFiveMinuteExecutionKeyRejectsNonFiveMinuteCandles() {
        let wrongTimeframeBar = candle(timeframe: .oneMinute)
        let input = LondresStrategyInput(
            timeframeBars: ["5m": [wrongTimeframeBar]],
            intradayBars: [],
            minuteBars: [],
            csdBars: [wrongTimeframeBar],
            smtBars: [:],
            smtGroup: "US_INDEXES",
            smtTimeframe: "5m",
            csdTimeframe: "5m",
            hierarchy: ["5m"],
            instrument: LondresInstrumentMetadata(symbol: "NQ", tickSize: 0.25)
        )

        XCTAssertThrowsError(try LondresStrategyPipeline().analyze(input)) { error in
            XCTAssertEqual(
                error as? LondresStrategyPipelineError,
                .invalidExecutionCandleTimeframe
            )
        }
    }

    private func minimalInput(csdTimeframe: String) -> LondresStrategyInput {
        LondresStrategyInput(
            timeframeBars: [:],
            intradayBars: [],
            minuteBars: [],
            csdBars: [],
            smtBars: [:],
            smtGroup: "US_INDEXES",
            smtTimeframe: "5m",
            csdTimeframe: csdTimeframe,
            instrument: LondresInstrumentMetadata(symbol: "NQ", tickSize: 0.25)
        )
    }

    private func candle(timeframe: LondresTimeframe) -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: timeframe,
            openTime: Date(timeIntervalSince1970: 0),
            open: 100,
            high: 101,
            low: 99,
            close: 100.5,
            volume: 100
        )
    }
}
