import XCTest
@testable import LondresTradingAI

final class NarrativeMMXMTests: XCTestCase {
    func testLiquidityRunClassificationSeparatesLRLRFromHRLR() {
        let engine = NarrativeEngine()

        let lrlr = engine.classifyLiquidityRun(direction: .bullish, control: .bullish)
        XCTAssertEqual(lrlr.classification, .lrlr)
        XCTAssertEqual(lrlr.targetScope, "ERL")

        let counter = engine.classifyLiquidityRun(direction: .bullish, control: .bearish)
        XCTAssertEqual(counter.classification, .hrlr)
        XCTAssertEqual(counter.targetScope, "IRL_OR_PARENT_MATRIX")

        let matrixBound = engine.classifyLiquidityRun(
            direction: .bullish,
            control: .bullish,
            opposingMatrixReached: true
        )
        XCTAssertEqual(matrixBound.classification, .hrlr)
    }

    func testMMXMEntryContractRequiresNarrativeAndValidatedExecution() {
        let model = detection(stage: .smartMoneyReversalConfirmed, final: .bullish)
        let engine = MMXMEngine()

        let noNarrative = engine.entryContract(
            model: model,
            narrativeQualified: false,
            executionSMTValidated: true,
            executionDirection: .bullish
        )
        XCTAssertEqual(noNarrative.state, .wait)
        XCTAssertEqual(noNarrative.reasonCodes, ["WAIT_FOR_PHASE7_NARRATIVE_GATE"])

        let noSMT = engine.entryContract(
            model: model,
            narrativeQualified: true,
            executionSMTValidated: false,
            executionDirection: .bullish
        )
        XCTAssertEqual(noSMT.state, .wait)
        XCTAssertEqual(noSMT.reasonCodes, ["WAIT_FOR_ALIGNED_SMT_CSD_POST_CSD_IOFC"])

        let ready = engine.entryContract(
            model: model,
            narrativeQualified: true,
            executionSMTValidated: true,
            executionDirection: .bullish
        )
        XCTAssertEqual(ready.state, .reversalReady)
        XCTAssertFalse(ready.orderAuthorized)
    }

    func testCounterModelCannotBecomeEntryReady() {
        var model = detection(stage: .continuationPhase, final: .bearish)
        model = MMXMDetection(
            timeframe: model.timeframe,
            model: model.model,
            stage: model.stage,
            approachDirection: model.approachDirection,
            finalDirection: model.finalDirection,
            originalConsolidation: model.originalConsolidation,
            dealingRange: model.dealingRange,
            matrix: model.matrix,
            matrixLocation: model.matrixLocation,
            smartMoneyReversal: model.smartMoneyReversal,
            terminal: model.terminal,
            parentRelationship: "COUNTER_MODEL_WITHIN_PARENT",
            parentTimeframe: "1H",
            reasonCodes: []
        )

        let result = MMXMEngine().entryContract(
            model: model,
            narrativeQualified: true,
            executionSMTValidated: true,
            executionDirection: .bearish
        )
        XCTAssertEqual(result.state, .wait)
        XCTAssertEqual(result.reasonCodes, ["HTF_CONTROL_OVERRIDES_COUNTER_MODEL_ENTRY"])
    }

    func testInvalidatedMMXMAlwaysFailsClosed() {
        let model = detection(stage: .invalidated, final: .bullish)
        let result = MMXMEngine().entryContract(
            model: model,
            narrativeQualified: true,
            executionSMTValidated: true,
            executionDirection: .bullish
        )
        XCTAssertEqual(result.state, .invalidated)
        XCTAssertFalse(result.orderAuthorized)
    }

    private func detection(stage: MMXMStage, final: DirectionalControl) -> MMXMDetection {
        MMXMDetection(
            timeframe: "5m",
            model: final == .bullish ? .mmbm : .mmsm,
            stage: stage,
            approachDirection: final == .bullish ? .bearish : .bullish,
            finalDirection: final,
            originalConsolidation: nil,
            dealingRange: nil,
            matrix: nil,
            matrixLocation: "UNRESOLVED",
            smartMoneyReversal: MMXMSmartMoneyReversal(
                confirmed: stage == .smartMoneyReversalConfirmed || stage == .continuationPhase,
                signature: SMRSignature(type: .failureSwing, failureSwing: nil, breaker: nil),
                csd: nil,
                postCSDIOFC: nil
            ),
            terminal: nil,
            parentRelationship: "ROOT_MODEL",
            parentTimeframe: nil,
            reasonCodes: []
        )
    }
}
