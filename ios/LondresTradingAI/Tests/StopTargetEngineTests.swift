import XCTest
@testable import LondresTradingAI

final class StopTargetEngineTests: XCTestCase {
    func testStopSelectorExposesIOFAndSMTProtectedAnchors() throws {
        let csd = bullishCSD(protected: 95, threshold: 100, confirmationPosition: 1)
        let iofRange = confirmedRange(
            direction: .bullish,
            role: .support,
            low: 97,
            high: 99,
            sourcePosition: 2,
            confirmedPosition: 3
        )
        let orderFlow = OrderFlowResult(
            timeframe: "M5",
            control: .bullish,
            latestEvent: iofRange,
            activeSupportRanges: [iofRange],
            activeResistanceRanges: [],
            confirmedEvents: [iofRange],
            transitionReason: nil
        )
        let iofc = IOFCResult(
            expectedDirection: .bullish,
            confirmed: true,
            confirmationRange: iofRange,
            reason: "BODY_CLOSE_CONFIRMED_POST_CSD_IOFC"
        )

        let result = StopSelectionEngine().analyze(
            direction: .bullish,
            currentPrice: 103,
            orderFlow: orderFlow,
            validationCSD: csd,
            postCSDIOFC: iofc
        )

        XCTAssertEqual(Set(result.allowedSources), Set([.iofRange, .smtProtected]))
        XCTAssertTrue(result.selectionRequired)
        XCTAssertEqual(result.candidates.first(where: { $0.source == .iofRange })?.anchorPrice, 97)
        XCTAssertEqual(result.candidates.first(where: { $0.source == .smtProtected })?.anchorPrice, 95)
    }

    func testExecutableBullishStopBuffersBelowAnchorAndSnapsOutward() throws {
        let entry = EntryExecutionContext(
            status: .entryTriggered,
            direction: .bullish,
            zone: nil,
            entry: EntryEvent(
                price: 101.13,
                position: 4,
                time: Date(),
                fillBasis: "TEST",
                zoneLow: 100,
                zoneHigh: 102
            ),
            reasonCodes: []
        )
        let selection = ValidatedStopSelection(
            valid: true,
            selectedSource: .iofRange,
            selectedAnchorPrice: 99.12,
            placement: "BELOW_RANGE_LOW",
            reason: "TEST"
        )

        let result = ExecutableStopEngine().calculate(
            entryExecution: entry,
            stopSelection: selection,
            tickSize: 0.25,
            bufferTicks: 1
        )

        XCTAssertEqual(result.status, .ready)
        XCTAssertEqual(result.executableStopPrice, 98.75)
        XCTAssertEqual(result.stopDistanceTicks, 10)
    }

    func testInvalidStopGeometryFailsClosed() throws {
        let entry = EntryExecutionContext(
            status: .entryTriggered,
            direction: .bullish,
            zone: nil,
            entry: EntryEvent(
                price: 100,
                position: 2,
                time: Date(),
                fillBasis: "TEST",
                zoneLow: 99,
                zoneHigh: 101
            ),
            reasonCodes: []
        )
        let selection = ValidatedStopSelection(
            valid: true,
            selectedSource: .smtProtected,
            selectedAnchorPrice: 101,
            placement: "BELOW_PROTECTED_LOW",
            reason: "TEST"
        )

        let result = ExecutableStopEngine().calculate(
            entryExecution: entry,
            stopSelection: selection,
            tickSize: 0.25,
            bufferTicks: 1
        )

        XCTAssertEqual(result.status, .invalidDirectionalGeometry)
        XCTAssertNil(result.executableStopPrice)
    }

    func testBullishCSDProjectsMinusTwoAndMinusTwoPointFive() throws {
        let csd = bullishCSD(protected: 95, threshold: 100, confirmationPosition: 4)
        let result = CSDTargetEngine().analyze(validationCSD: csd)

        XCTAssertEqual(result.status, .ready)
        XCTAssertEqual(result.rangeSize, 5)
        XCTAssertEqual(result.sd2?.price, 110)
        XCTAssertEqual(result.sd2_5?.price, 112.5)
        XCTAssertFalse(result.holdAvailable)
    }

    func testHTFExternalLiquidityBeyondSDTwoPointFiveBecomesRunner() throws {
        let csd = bullishCSD(protected: 95, threshold: 100, confirmationPosition: 4)
        let pool = LiquidityPool(
            side: .buySide,
            liquidityClass: .external,
            price: 115,
            timeframe: "H1",
            sourceKind: "SWING_HIGH",
            sourcePosition: 1,
            sourceTime: Date(),
            status: .active
        )
        let liquidity = LiquidityResult(
            timeframe: "H1",
            orderFlowControl: .bullish,
            currentPrice: 105,
            buySide: [pool],
            sellSide: [],
            protectedPool: nil,
            activeDraw: pool,
            externalHigh: 115,
            externalLow: nil,
            dealingRangeEquilibrium: nil,
            dealingRangeLocation: "UNRESOLVED",
            reasonCodes: []
        )

        let result = CSDTargetEngine().analyze(validationCSD: csd, htfLiquidity: liquidity)
        XCTAssertTrue(result.holdAvailable)
        XCTAssertEqual(result.htfRunnerTarget?.price, 115)

        let selection = try CSDTargetEngine().select(result, mode: .holdHTFLiquidity)
        XCTAssertEqual(selection.partialFraction, 0.60)
        XCTAssertEqual(selection.runnerFraction, 0.40)
        XCTAssertEqual(selection.originalTargetPrice, 115)
    }

    private func bullishCSD(
        protected: Double,
        threshold: Double,
        confirmationPosition: Int
    ) -> CSDEvent {
        let reference = CSDPivotReference(
            side: "LOW",
            price: protected + 1,
            sourcePosition: 0,
            sourceTime: Date(timeIntervalSince1970: 0),
            confirmedPosition: 1,
            confirmedTime: Date(timeIntervalSince1970: 300)
        )
        return CSDEvent(
            direction: .bullish,
            timeframe: "M5",
            liquiditySide: "SELL_SIDE",
            liquidityReference: reference,
            raidPosition: 2,
            raidTime: Date(timeIntervalSince1970: 600),
            thresholdPosition: 3,
            thresholdTime: Date(timeIntervalSince1970: 900),
            thresholdOpen: threshold,
            confirmationPosition: confirmationPosition,
            confirmationTime: Date(timeIntervalSince1970: TimeInterval(confirmationPosition * 300)),
            confirmationClose: threshold + 1,
            protectedExtreme: protected,
            reasonCodes: []
        )
    }

    private func confirmedRange(
        direction: OrderFlowDirection,
        role: RangeRole,
        low: Double,
        high: Double,
        sourcePosition: Int,
        confirmedPosition: Int
    ) -> OrderFlowRange {
        var range = OrderFlowRange(
            direction: direction,
            role: role,
            sourcePosition: sourcePosition,
            sourceTime: Date(timeIntervalSince1970: TimeInterval(sourcePosition * 300)),
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
}
