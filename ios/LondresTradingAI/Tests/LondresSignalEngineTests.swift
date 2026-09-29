import XCTest
@testable import LondresTradingAI

final class LondresSignalEngineTests: XCTestCase {
    private let engine = LondresSignalEngine()

    func testSMTAloneIsNeverActionable() {
        let signal = engine.evaluate(
            input(
                evidence: LondresSetupEvidence(
                    smtDetected: true,
                    csdConfirmed: false,
                    iofAligned: false,
                    timePriceValid: true,
                    entryZoneValid: true,
                    liquidityRaidConfirmed: true,
                    mmxmNarrativeAligned: true,
                    iofRange: nil
                )
            )
        )

        XCTAssertEqual(signal.status, .waitingForCSD)
        XCTAssertFalse(signal.isActionable)
    }

    func testFullInvariantProducesValidSignal() {
        let signal = engine.evaluate(input(evidence: validEvidence()))

        XCTAssertEqual(signal.status, .valid)
        XCTAssertTrue(signal.isActionable)
        XCTAssertEqual(signal.geometry?.rewardToRisk ?? 0, 7.0, accuracy: 0.0001)
    }

    func testInvalidBullishGeometryFailsClosed() {
        let signal = engine.evaluate(
            input(
                evidence: validEvidence(),
                geometry: TradeGeometry(entry: 100, stop: 110, target: 170)
            )
        )

        XCTAssertEqual(signal.status, .invalidGeometry)
        XCTAssertFalse(signal.isActionable)
    }

    func testIOFMustFollowCSD() {
        var evidence = validEvidence()
        evidence = LondresSetupEvidence(
            smtDetected: evidence.smtDetected,
            csdConfirmed: evidence.csdConfirmed,
            iofAligned: false,
            timePriceValid: evidence.timePriceValid,
            entryZoneValid: evidence.entryZoneValid,
            liquidityRaidConfirmed: evidence.liquidityRaidConfirmed,
            mmxmNarrativeAligned: evidence.mmxmNarrativeAligned,
            iofRange: evidence.iofRange
        )

        let signal = engine.evaluate(input(evidence: evidence))
        XCTAssertEqual(signal.status, .waitingForIOF)
    }

    private func input(
        evidence: LondresSetupEvidence,
        geometry: TradeGeometry = TradeGeometry(entry: 100, stop: 90, target: 170)
    ) -> LondresSignalInput {
        LondresSignalInput(
            context: LondresMarketContext(
                symbol: "NQ",
                direction: .bullish,
                session: .newYorkAM,
                higherTimeframeControl: .bullish,
                weeklyProfile: "Expansion",
                dailyProfile: "OHLC",
                h4Profile: "Bullish Expansion",
                liquidityNarrative: "SSL raid",
                timestamp: Date(timeIntervalSince1970: 1_700_000_000)
            ),
            evidence: evidence,
            geometry: geometry,
            riskTier: .conservative
        )
    }

    private func validEvidence() -> LondresSetupEvidence {
        LondresSetupEvidence(
            smtDetected: true,
            csdConfirmed: true,
            iofAligned: true,
            timePriceValid: true,
            entryZoneValid: true,
            liquidityRaidConfirmed: true,
            mmxmNarrativeAligned: true,
            iofRange: PriceRange(low: 95, high: 105)
        )
    }
}
