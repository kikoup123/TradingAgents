import Foundation

enum TradePlanState: String, Codable, Hashable, Sendable {
    case notReady = "NOT_READY"
    case readyForEntrySelection = "READY_FOR_ENTRY_SELECTION"
    case invalidated = "INVALIDATED"
    case objectiveReached = "OBJECTIVE_REACHED"
}

struct TradePlanInvalidation: Codable, Hashable, Sendable {
    let price: Double
    let source: String
    let kind: String
    let confirmationPosition: Int?
}

struct TradePlanTarget: Codable, Hashable, Sendable {
    let price: Double
    let kind: String
    let side: String?
    let reached: Bool
    let source: String
}

struct TradePlanLocation: Codable, Hashable, Sendable, Identifiable {
    let kind: String
    let low: Double
    let high: Double
    let sourcePosition: Int?
    let confirmedPosition: Int?
    let formationPosition: Int?
    let consequentEncroachment: Double?
    let fairValuationPoint: Double?
    let status: String?
    let entrySignal: Bool

    var id: String { "\(kind)|\(low)|\(high)|\(sourcePosition ?? -1)|\(formationPosition ?? -1)" }
}

struct TradePlanContext: Codable, Hashable, Sendable {
    let state: TradePlanState
    let direction: DirectionalControl
    let model: MMXMType
    let mmxmStage: MMXMStage
    let currentPrice: Double
    let invalidation: TradePlanInvalidation?
    let primaryTarget: TradePlanTarget?
    let liquidityRun: LiquidityRunClassification?
    let executionLocations: [TradePlanLocation]
    let entrySelectionRequired: Bool
    let reasonCodes: [String]
    let orderAuthorized: Bool
}

enum TradePlanError: Error, Equatable {
    case noBars
    case timeframeMissingFromNarrative(String)
}

struct TradePlanEngine: Sendable {
    private static let readyEntryStates: Set<MMXMEntryState> = [.reversalReady, .continuationReady]

    func analyze(
        bars: [MarketCandle],
        timeframe: String,
        narrative: NarrativeResult,
        mmxm: MMXMDetection,
        entryContract: MMXMEntryContract,
        asOf: Date? = nil
    ) throws -> TradePlanContext {
        var data = bars.sorted { $0.openTime < $1.openTime }
        if let asOf { data = data.filter { $0.openTime <= asOf } }
        guard let last = data.last else { throw TradePlanError.noBars }
        guard let local = narrative.timeframes[timeframe] else {
            throw TradePlanError.timeframeMissingFromNarrative(timeframe)
        }

        let currentPrice = last.close
        let direction = mmxm.finalDirection
        var reasons: [String] = []
        let reversal = local.reversal
        let protected = reversal.csd?.protectedExtreme
        let confirmationPosition = reversal.csd?.confirmationPosition
        let invalidation: TradePlanInvalidation?
        if let protected, direction == .bullish || direction == .bearish {
            invalidation = TradePlanInvalidation(
                price: protected,
                source: "CSD_PROTECTED_EXTREME",
                kind: direction == .bullish ? "PROTECTED_LOW" : "PROTECTED_HIGH",
                confirmationPosition: confirmationPosition
            )
        } else {
            invalidation = nil
            reasons.append("WAIT_FOR_CSD_PROTECTED_EXTREME")
        }

        let target = primaryTarget(mmxm: mmxm, local: local)
        if target == nil { reasons.append("WAIT_FOR_DIRECTIONAL_OBJECTIVE") }

        let locations = executionLocations(
            local: local,
            mmxm: mmxm,
            direction: direction,
            confirmationPosition: confirmationPosition
        )
        if locations.isEmpty { reasons.append("WAIT_FOR_POST_CONFIRMATION_EXECUTION_LOCATION") }

        let liquidityRun = local.parentRelativeRun.classification != .unresolved
            ? local.parentRelativeRun
            : local.liquidityRun
        if liquidityRun.classification == .hrlr {
            reasons.append("EXECUTION_PATH_IS_PARENT_RELATIVE_HRLR")
        }

        let invalidated = invalidationBreached(
            currentPrice: currentPrice,
            direction: direction,
            invalidation: invalidation
        )
        let objectiveReached = mmxm.stage == .terminalReached || target?.reached == true
        let geometryValid = geometryValid(
            currentPrice: currentPrice,
            direction: direction,
            invalidation: invalidation,
            target: target
        )

        let state: TradePlanState
        if invalidated || mmxm.stage == .invalidated || entryContract.state == .invalidated {
            state = .invalidated
            reasons.append("STRUCTURAL_INVALIDATION_REACHED")
        } else if objectiveReached {
            state = .objectiveReached
            reasons.append("MMXM_OBJECTIVE_ALREADY_REACHED")
        } else if !Self.readyEntryStates.contains(entryContract.state) {
            state = .notReady
            reasons.append("MMXM_ENTRY_GATE_NOT_READY")
        } else if !geometryValid {
            state = .notReady
            reasons.append("TARGET_INVALIDATION_GEOMETRY_UNRESOLVED")
        } else if locations.isEmpty {
            state = .notReady
        } else {
            state = .readyForEntrySelection
            reasons.append("STRUCTURAL_PLAN_READY_WAIT_EXACT_ENTRY_MODEL")
        }

        return TradePlanContext(
            state: state,
            direction: direction,
            model: mmxm.model,
            mmxmStage: mmxm.stage,
            currentPrice: currentPrice,
            invalidation: invalidation,
            primaryTarget: target,
            liquidityRun: liquidityRun,
            executionLocations: locations,
            entrySelectionRequired: state == .readyForEntrySelection,
            reasonCodes: reasons,
            orderAuthorized: false
        )
    }

    private func primaryTarget(
        mmxm: MMXMDetection,
        local: NarrativeTimeframeState
    ) -> TradePlanTarget? {
        if let terminal = mmxm.terminal {
            return TradePlanTarget(
                price: terminal.price,
                kind: terminal.purpose,
                side: terminal.side.rawValue,
                reached: terminal.reached,
                source: "MMXM_TERMINAL"
            )
        }
        if let draw = local.narrativeDraw, let price = draw.price {
            return TradePlanTarget(
                price: price,
                kind: draw.kind.rawValue,
                side: nil,
                reached: false,
                source: "NARRATIVE_DRAW"
            )
        }
        return nil
    }

    private func executionLocations(
        local: NarrativeTimeframeState,
        mmxm: MMXMDetection,
        direction: DirectionalControl,
        confirmationPosition: Int?
    ) -> [TradePlanLocation] {
        var locations: [TradePlanLocation] = []
        if let iofc = local.reversal.postCSDIOFC,
           iofc.confirmed,
           let range = iofc.confirmationRange {
            locations.append(
                TradePlanLocation(
                    kind: "POST_CSD_IOFC_RANGE",
                    low: range.low,
                    high: range.high,
                    sourcePosition: range.sourcePosition,
                    confirmedPosition: range.confirmedPosition,
                    formationPosition: nil,
                    consequentEncroachment: nil,
                    fairValuationPoint: nil,
                    status: range.status.rawValue,
                    entrySignal: false
                )
            )
        }

        let marketDirection: MarketDirection? = direction == .bullish ? .bullish : direction == .bearish ? .bearish : nil
        if let marketDirection {
            for gap in local.fairValue.structuralFVGCandidates where gap.direction == marketDirection {
                if let confirmationPosition, gap.formationPosition <= confirmationPosition { continue }
                locations.append(
                    TradePlanLocation(
                        kind: "POST_CSD_STRUCTURAL_FVG",
                        low: gap.low,
                        high: gap.high,
                        sourcePosition: nil,
                        confirmedPosition: nil,
                        formationPosition: gap.formationPosition,
                        consequentEncroachment: gap.consequentEncroachment,
                        fairValuationPoint: gap.fairValuationPoint,
                        status: gap.status.rawValue,
                        entrySignal: false
                    )
                )
            }
        }

        if mmxm.smartMoneyReversal.signature.type == .breaker,
           let failed = mmxm.smartMoneyReversal.signature.breaker?.failedOrderFlowRange {
            locations.append(
                TradePlanLocation(
                    kind: "MMXM_BREAKER_RETEST_RANGE",
                    low: failed.low,
                    high: failed.high,
                    sourcePosition: failed.sourcePosition,
                    confirmedPosition: failed.confirmedPosition,
                    formationPosition: nil,
                    consequentEncroachment: nil,
                    fairValuationPoint: nil,
                    status: failed.status.rawValue,
                    entrySignal: false
                )
            )
        }
        return locations
    }

    private func invalidationBreached(
        currentPrice: Double,
        direction: DirectionalControl,
        invalidation: TradePlanInvalidation?
    ) -> Bool {
        guard let invalidation else { return false }
        if direction == .bullish { return currentPrice < invalidation.price }
        if direction == .bearish { return currentPrice > invalidation.price }
        return false
    }

    private func geometryValid(
        currentPrice: Double,
        direction: DirectionalControl,
        invalidation: TradePlanInvalidation?,
        target: TradePlanTarget?
    ) -> Bool {
        guard let invalidation, let target else { return false }
        if direction == .bullish {
            return invalidation.price < currentPrice && currentPrice < target.price
        }
        if direction == .bearish {
            return target.price < currentPrice && currentPrice < invalidation.price
        }
        return false
    }
}
