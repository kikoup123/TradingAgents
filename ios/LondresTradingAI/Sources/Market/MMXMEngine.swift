import Foundation

enum MMXMType: String, Codable, Hashable, Sendable {
    case mmbm = "MMBM"
    case mmsm = "MMSM"
    case unresolved = "UNRESOLVED"
}

enum MMXMStage: String, Codable, Hashable, Sendable {
    case noModel = "NO_MODEL"
    case curveToMatrix = "CURVE_TO_MATRIX"
    case atMatrixWaitReversal = "AT_MATRIX_WAIT_REVERSAL"
    case atMatrixWaitSignature = "AT_MATRIX_WAIT_SIGNATURE"
    case smartMoneyReversalConfirmed = "SMART_MONEY_REVERSAL_CONFIRMED"
    case continuationPhase = "CONTINUATION_PHASE"
    case terminalReached = "TERMINAL_REACHED"
    case invalidated = "INVALIDATED"
}

enum SMRSignatureType: String, Codable, Hashable, Sendable {
    case failureSwing = "FAILURE_SWING"
    case breaker = "BREAKER"
    case none = "NONE"
}

enum MMXMEntryState: String, Codable, Hashable, Sendable {
    case wait = "WAIT"
    case reversalReady = "REVERSAL_READY"
    case continuationReady = "CONTINUATION_READY"
    case invalidated = "INVALIDATED"
}

struct MMXMDealingRange: Codable, Hashable, Sendable {
    let low: Double
    let high: Double
    let equilibrium: Double
    let timeframe: String
}

struct FailureSwingEvidence: Codable, Hashable, Sendable {
    let direction: MarketDirection
    let firstExtreme: CSDPivotReference
    let failedExtreme: CSDPivotReference
    let interveningSwing: CSDPivotReference
    let triggerTime: Date
    let rule: String
}

struct BreakerEvidence: Codable, Hashable, Sendable {
    let direction: MarketDirection
    let failedOrderFlowRange: OrderFlowRange
    let retestPosition: Int
    let retestTime: Date
    let rule: String
}

struct SMRSignature: Codable, Hashable, Sendable {
    let type: SMRSignatureType
    let failureSwing: FailureSwingEvidence?
    let breaker: BreakerEvidence?
}

struct MMXMSmartMoneyReversal: Codable, Hashable, Sendable {
    let confirmed: Bool
    let signature: SMRSignature
    let csd: CSDEvent?
    let postCSDIOFC: IOFCResult?
}

struct MMXMTerminal: Codable, Hashable, Sendable {
    let price: Double
    let side: LiquiditySide
    let timeframe: String
    let sourceKind: String
    let reached: Bool
    let purpose: String
}

struct MMXMDetection: Codable, Hashable, Sendable {
    let timeframe: String
    let model: MMXMType
    let stage: MMXMStage
    let approachDirection: DirectionalControl
    let finalDirection: DirectionalControl
    let originalConsolidation: OriginalConsolidation?
    let dealingRange: MMXMDealingRange?
    let matrix: NarrativeMatrix?
    let matrixLocation: String
    let smartMoneyReversal: MMXMSmartMoneyReversal
    let terminal: MMXMTerminal?
    let parentRelationship: String
    let parentTimeframe: String?
    let reasonCodes: [String]
}

struct MMXMEntryContract: Codable, Hashable, Sendable {
    let state: MMXMEntryState
    let direction: DirectionalControl
    let model: MMXMType
    let stage: MMXMStage
    let parentRelationship: String
    let reasonCodes: [String]
    let orderAuthorized: Bool
}

enum MMXMError: Error, Equatable {
    case timeframeMissingFromNarrative(String)
    case noBars
    case invalidPivotSpan
}

struct MMXMEngine: Sendable {
    let pivotSpan: Int

    init(pivotSpan: Int = 2) {
        self.pivotSpan = pivotSpan
    }

    func analyze(
        bars: [MarketCandle],
        timeframe: String,
        narrative: NarrativeResult,
        asOf: Date? = nil,
        parentModel: MMXMDetection? = nil
    ) throws -> MMXMDetection {
        guard pivotSpan >= 1 else { throw MMXMError.invalidPivotSpan }
        guard let state = narrative.timeframes[timeframe] else {
            throw MMXMError.timeframeMissingFromNarrative(timeframe)
        }
        var data = bars.sorted { $0.openTime < $1.openTime }
        if let asOf { data = data.filter { $0.openTime <= asOf } }
        guard !data.isEmpty else { throw MMXMError.noBars }

        let oc = state.priceDelivery.originalConsolidation
        let approach = departureDirection(state: state, oc: oc)
        let final = opposite(approach)
        let model = modelType(final)
        let matrix = selectMatrix(state: state, final: final)
        let dealingRange = dealingRange(
            state: state,
            matrix: matrix,
            narrative: narrative
        )
        let matrixLocation = matrixLocation(matrix: matrix, dealingRange: dealingRange)
        let locationValid = locationValid(model: model, location: matrixLocation)
        let parent = parentRelationship(parentModel: parentModel, final: final)
        var reasons: [String] = []

        if oc == nil || oc?.status != "DISPLACEMENT_CONFIRMED" {
            reasons.append("WAIT_FOR_DISPLACEMENT_CONFIRMED_OC")
        }
        if !isDirectional(approach) {
            reasons.append("WAIT_FOR_OC_DEPARTURE_DIRECTION")
        }
        if dealingRange == nil {
            reasons.append("WAIT_FOR_DEFINED_DEALING_RANGE")
        }
        if matrix == nil {
            reasons.append("WAIT_FOR_PARENT_MATRIX")
        } else if !locationValid {
            reasons.append("MATRIX_NOT_IN_REQUIRED_PREMIUM_DISCOUNT_LOCATION")
        }

        let reversal = state.reversal
        let reversalAligned = reversal.confirmed
            && isDirectional(final)
            && reversal.csd?.direction.rawValue == final.rawValue
        let matrixReached = matrix?.reached == true || reversalAligned
        let matrixTouchPosition = matrixTouchPosition(
            data: data,
            matrix: matrix,
            reversal: reversal
        )

        var signature = SMRSignature(type: .none, failureSwing: nil, breaker: nil)
        if let matrix, reversalAligned, let finalMarket = marketDirection(final) {
            if let failure = failureSwing(
                data: data,
                matrix: matrix,
                finalDirection: finalMarket,
                startPosition: matrixTouchPosition
            ) {
                signature = SMRSignature(type: .failureSwing, failureSwing: failure, breaker: nil)
            } else if let breaker = try breaker(
                data: data,
                matrix: matrix,
                finalDirection: finalMarket,
                startPosition: matrixTouchPosition
            ) {
                signature = SMRSignature(type: .breaker, failureSwing: nil, breaker: breaker)
            }
        }

        let protectedBroken = protectedExtremeBroken(
            data: data,
            reversal: reversal,
            final: final
        )
        let continuation = continuationAfterReversal(
            delivery: state.priceDelivery,
            reversal: reversal,
            final: final
        )
        let terminal = terminal(state: state, final: final, reversal: reversal)
        let terminalReached = terminal?.reached == true

        let baseReady = oc?.status == "DISPLACEMENT_CONFIRMED"
            && isDirectional(approach)
            && dealingRange != nil
            && matrix != nil
            && locationValid

        let stage: MMXMStage
        if protectedBroken {
            stage = .invalidated
            reasons.append("POST_REVERSAL_PROTECTED_EXTREME_BROKEN")
        } else if !baseReady {
            stage = .noModel
        } else if !matrixReached {
            stage = .curveToMatrix
            reasons.append("DELIVERY_CURVE_HAS_NOT_REACHED_MATRIX")
        } else if !reversalAligned {
            stage = .atMatrixWaitReversal
            reasons.append("MATRIX_TOUCH_IS_NOT_SMART_MONEY_REVERSAL")
        } else if signature.type == .none {
            stage = .atMatrixWaitSignature
            reasons.append("WAIT_FOR_FAILURE_SWING_OR_BREAKER_AT_MATRIX")
        } else if terminalReached {
            stage = .terminalReached
            reasons.append("TERMINAL_LIQUIDITY_REACHED")
        } else if continuation {
            stage = .continuationPhase
            reasons.append("FIRST_POST_REVERSAL_REACCUMULATION_REDISTRIBUTION_OBSERVED")
        } else {
            stage = .smartMoneyReversalConfirmed
            reasons.append("MATRIX_SIGNATURE_CSD_IOFC_ALIGNED")
        }

        if parent.relationship == "COUNTER_MODEL_WITHIN_PARENT" {
            reasons.append("LOCAL_COUNTER_MODEL_DOES_NOT_INVALIDATE_PARENT_MMXM")
        }

        return MMXMDetection(
            timeframe: timeframe,
            model: baseReady ? model : .unresolved,
            stage: stage,
            approachDirection: approach,
            finalDirection: final,
            originalConsolidation: oc,
            dealingRange: dealingRange,
            matrix: matrix,
            matrixLocation: matrixLocation,
            smartMoneyReversal: MMXMSmartMoneyReversal(
                confirmed: reversalAligned && signature.type != .none && !protectedBroken,
                signature: signature,
                csd: reversal.csd,
                postCSDIOFC: reversal.postCSDIOFC
            ),
            terminal: terminal,
            parentRelationship: parent.relationship,
            parentTimeframe: parent.timeframe,
            reasonCodes: reasons
        )
    }

    func analyzeHierarchy(
        timeframeBars: [String: [MarketCandle]],
        narrative: NarrativeResult,
        asOf: Date? = nil
    ) throws -> [String: MMXMDetection] {
        var result: [String: MMXMDetection] = [:]
        var parent: MMXMDetection?
        for timeframe in narrative.hierarchy {
            guard let bars = timeframeBars[timeframe] else { continue }
            let detection = try analyze(
                bars: bars,
                timeframe: timeframe,
                narrative: narrative,
                asOf: asOf,
                parentModel: parent
            )
            result[timeframe] = detection
            parent = detection
        }
        return result
    }

    func entryContract(
        model: MMXMDetection,
        narrativeQualified: Bool,
        executionSMTValidated: Bool,
        executionDirection: DirectionalControl
    ) -> MMXMEntryContract {
        var reasons: [String] = []
        let state: MMXMEntryState

        if model.stage == .invalidated {
            state = .invalidated
            reasons.append("MMXM_INVALIDATED")
        } else if model.parentRelationship == "COUNTER_MODEL_WITHIN_PARENT" {
            state = .wait
            reasons.append("HTF_CONTROL_OVERRIDES_COUNTER_MODEL_ENTRY")
        } else if !narrativeQualified {
            state = .wait
            reasons.append("WAIT_FOR_PHASE7_NARRATIVE_GATE")
        } else if !executionSMTValidated || executionDirection != model.finalDirection {
            state = .wait
            reasons.append("WAIT_FOR_ALIGNED_SMT_CSD_POST_CSD_IOFC")
        } else if model.stage == .smartMoneyReversalConfirmed {
            state = .reversalReady
            reasons.append("SMART_MONEY_REVERSAL_AND_EXECUTION_GATE_CONFIRMED")
        } else if model.stage == .continuationPhase {
            state = .continuationReady
            reasons.append("POST_REVERSAL_CONTINUATION_AND_EXECUTION_GATE_CONFIRMED")
        } else {
            state = .wait
            reasons.append("MMXM_STAGE_NOT_ENTRY_READY")
        }

        return MMXMEntryContract(
            state: state,
            direction: model.finalDirection,
            model: model.model,
            stage: model.stage,
            parentRelationship: model.parentRelationship,
            reasonCodes: reasons,
            orderAuthorized: false
        )
    }

    private func departureDirection(
        state: NarrativeTimeframeState,
        oc: OriginalConsolidation?
    ) -> DirectionalControl {
        guard let oc, let departure = oc.departurePosition else { return .unconfirmed }
        let exact = state.fairValue.gaps.filter { $0.formationPosition == departure }
        let candidates = exact.isEmpty
            ? state.fairValue.gaps.filter { $0.sourcePosition > oc.endPosition }
            : exact
        guard let first = candidates.first else { return .unconfirmed }
        return first.direction == .bullish ? .bullish : .bearish
    }

    private func selectMatrix(
        state: NarrativeTimeframeState,
        final: DirectionalControl
    ) -> NarrativeMatrix? {
        if state.reversal.confirmed, let matrix = state.reversal.matrix {
            return NarrativeMatrix(
                kind: matrix.kind,
                low: matrix.low,
                high: matrix.high,
                availableTime: matrix.availableTime,
                timeframe: matrix.timeframe,
                direction: matrix.direction,
                reached: true,
                reachedTime: matrix.reachedTime,
                distance: matrix.distance
            )
        }
        guard let finalMarket = marketDirection(final) else { return nil }
        let candidates = state.parentMatrices.filter { $0.direction == finalMarket }
        let reached = candidates.filter(\.reached)
        if !reached.isEmpty {
            return reached.max {
                ($0.reachedTime ?? .distantPast) < ($1.reachedTime ?? .distantPast)
            }
        }
        return candidates.min(by: { $0.distance < $1.distance })
    }

    private func dealingRange(
        state: NarrativeTimeframeState,
        matrix: NarrativeMatrix?,
        narrative: NarrativeResult
    ) -> MMXMDealingRange? {
        let source: NarrativeTimeframeState
        if let matrix, let matrixState = narrative.timeframes[matrix.timeframe] {
            source = matrixState
        } else {
            source = state
        }
        guard let low = source.liquidity.externalLow,
              let high = source.liquidity.externalHigh,
              low < high else {
            return nil
        }
        return MMXMDealingRange(
            low: low,
            high: high,
            equilibrium: (low + high) / 2,
            timeframe: source.timeframe
        )
    }

    private func matrixLocation(
        matrix: NarrativeMatrix?,
        dealingRange: MMXMDealingRange?
    ) -> String {
        guard let matrix, let dealingRange else { return "UNRESOLVED" }
        let center = (matrix.low + matrix.high) / 2
        if center > dealingRange.equilibrium { return "PREMIUM" }
        if center < dealingRange.equilibrium { return "DISCOUNT" }
        return "EQUILIBRIUM"
    }

    private func locationValid(model: MMXMType, location: String) -> Bool {
        (model == .mmsm && location == "PREMIUM")
            || (model == .mmbm && location == "DISCOUNT")
    }

    private func matrixTouchPosition(
        data: [MarketCandle],
        matrix: NarrativeMatrix?,
        reversal: NarrativeReversal
    ) -> Int {
        if let raidPosition = reversal.csd?.raidPosition { return raidPosition }
        if let reachedTime = matrix?.reachedTime,
           let index = data.firstIndex(where: { $0.openTime >= reachedTime }) {
            return index
        }
        return 0
    }

    private func failureSwing(
        data: [MarketCandle],
        matrix: NarrativeMatrix,
        finalDirection: MarketDirection,
        startPosition: Int
    ) -> FailureSwingEvidence? {
        let pivots = StructuralPivotEngine(pivotSpan: pivotSpan).confirmedPivots(data)
        if finalDirection == .bearish {
            let firstCandidates = pivots.highs.filter { reference in
                reference.sourcePosition >= startPosition
                    && data[reference.sourcePosition].low <= matrix.high
                    && reference.price >= matrix.low
            }
            for first in firstCandidates {
                for second in pivots.highs where second.sourcePosition > first.sourcePosition && second.price < first.price {
                    let intervening = pivots.lows.filter {
                        first.sourcePosition < $0.sourcePosition && $0.sourcePosition < second.sourcePosition
                    }
                    guard let trigger = intervening.min(by: { $0.price < $1.price }) else { continue }
                    if let triggerBar = data.enumerated().first(where: {
                        $0.offset >= second.confirmedPosition && $0.element.close < trigger.price
                    }) {
                        return FailureSwingEvidence(
                            direction: finalDirection,
                            firstExtreme: first,
                            failedExtreme: second,
                            interveningSwing: trigger,
                            triggerTime: triggerBar.element.openTime,
                            rule: "LOWER_HIGH_THEN_BODY_CLOSE_BELOW_INTERVENING_LOW"
                        )
                    }
                }
            }
        } else {
            let firstCandidates = pivots.lows.filter { reference in
                reference.sourcePosition >= startPosition
                    && data[reference.sourcePosition].high >= matrix.low
                    && reference.price <= matrix.high
            }
            for first in firstCandidates {
                for second in pivots.lows where second.sourcePosition > first.sourcePosition && second.price > first.price {
                    let intervening = pivots.highs.filter {
                        first.sourcePosition < $0.sourcePosition && $0.sourcePosition < second.sourcePosition
                    }
                    guard let trigger = intervening.max(by: { $0.price < $1.price }) else { continue }
                    if let triggerBar = data.enumerated().first(where: {
                        $0.offset >= second.confirmedPosition && $0.element.close > trigger.price
                    }) {
                        return FailureSwingEvidence(
                            direction: finalDirection,
                            firstExtreme: first,
                            failedExtreme: second,
                            interveningSwing: trigger,
                            triggerTime: triggerBar.element.openTime,
                            rule: "HIGHER_LOW_THEN_BODY_CLOSE_ABOVE_INTERVENING_HIGH"
                        )
                    }
                }
            }
        }
        return nil
    }

    private func breaker(
        data: [MarketCandle],
        matrix: NarrativeMatrix,
        finalDirection: MarketDirection,
        startPosition: Int
    ) throws -> BreakerEvidence? {
        let oldDirection: OrderFlowDirection = finalDirection == .bullish ? .bearish : .bullish
        let flow = try OrderFlowEngine().analyze(bars: data, timeframe: "MMXM_BREAKER")
        for event in flow.confirmedEvents {
            guard event.direction == oldDirection,
                  let invalidatedPosition = event.invalidatedPosition,
                  invalidatedPosition >= startPosition else {
                continue
            }
            let source = data[event.sourcePosition]
            guard source.low <= matrix.high && source.high >= matrix.low else { continue }
            guard invalidatedPosition + 1 < data.count else { continue }
            for position in (invalidatedPosition + 1)..<data.count {
                let row = data[position]
                guard row.low <= event.high && row.high >= event.low else { continue }
                let held = finalDirection == .bullish ? row.close > event.high : row.close < event.low
                if held {
                    return BreakerEvidence(
                        direction: finalDirection,
                        failedOrderFlowRange: event,
                        retestPosition: position,
                        retestTime: row.openTime,
                        rule: "INVALIDATED_OPPOSING_IOF_RANGE_RETESTED_FROM_NEW_SIDE"
                    )
                }
            }
        }
        return nil
    }

    private func protectedExtremeBroken(
        data: [MarketCandle],
        reversal: NarrativeReversal,
        final: DirectionalControl
    ) -> Bool {
        guard let csd = reversal.csd else { return false }
        let after = data.enumerated().filter { $0.offset > csd.confirmationPosition }.map(\.element)
        if final == .bullish { return after.contains { $0.close < csd.protectedExtreme } }
        if final == .bearish { return after.contains { $0.close > csd.protectedExtreme } }
        return false
    }

    private func continuationAfterReversal(
        delivery: PriceDeliveryResult,
        reversal: NarrativeReversal,
        final: DirectionalControl
    ) -> Bool {
        guard let position = reversal.csd?.confirmationPosition else { return false }
        let expected: DeliveryEventKind
        if final == .bullish {
            expected = .reaccumulation
        } else if final == .bearish {
            expected = .redistribution
        } else {
            return false
        }
        return delivery.events.contains { $0.position > position && $0.event == expected }
    }

    private func terminal(
        state: NarrativeTimeframeState,
        final: DirectionalControl,
        reversal: NarrativeReversal
    ) -> MMXMTerminal? {
        guard let confirmation = reversal.csd?.confirmationPosition else { return nil }
        let targetSide: LiquiditySide
        if final == .bullish {
            targetSide = .buySide
        } else if final == .bearish {
            targetSide = .sellSide
        } else {
            return nil
        }
        let pools = targetSide == .buySide ? state.liquidity.buySide : state.liquidity.sellSide
        let reached = pools.filter {
            $0.liquidityClass == .external
                && ($0.eventPosition ?? -1) > confirmation
                && ($0.status == .consumed || $0.status == .raidedReclaimed)
        }
        if let pool = reached.min(by: { ($0.eventPosition ?? Int.max) < ($1.eventPosition ?? Int.max) }) {
            return MMXMTerminal(
                price: pool.price,
                side: targetSide,
                timeframe: pool.timeframe,
                sourceKind: pool.sourceKind,
                reached: true,
                purpose: "MMXM_TERMINAL"
            )
        }
        if let draw = state.liquidity.activeDraw, draw.side == targetSide {
            return MMXMTerminal(
                price: draw.price,
                side: targetSide,
                timeframe: draw.timeframe,
                sourceKind: draw.sourceKind,
                reached: false,
                purpose: "MMXM_TERMINAL"
            )
        }
        return nil
    }

    private func parentRelationship(
        parentModel: MMXMDetection?,
        final: DirectionalControl
    ) -> (relationship: String, timeframe: String?) {
        guard let parentModel else { return ("ROOT_MODEL", nil) }
        let active = parentModel.stage != .noModel && parentModel.stage != .invalidated
        guard active, isDirectional(parentModel.finalDirection) else {
            return ("PARENT_UNRESOLVED", parentModel.timeframe)
        }
        guard isDirectional(final) else { return ("LOCAL_UNRESOLVED", parentModel.timeframe) }
        return final == parentModel.finalDirection
            ? ("ALIGNED_CHILD_MODEL", parentModel.timeframe)
            : ("COUNTER_MODEL_WITHIN_PARENT", parentModel.timeframe)
    }

    private func opposite(_ direction: DirectionalControl) -> DirectionalControl {
        if direction == .bullish { return .bearish }
        if direction == .bearish { return .bullish }
        return .unconfirmed
    }

    private func modelType(_ final: DirectionalControl) -> MMXMType {
        if final == .bullish { return .mmbm }
        if final == .bearish { return .mmsm }
        return .unresolved
    }

    private func isDirectional(_ direction: DirectionalControl) -> Bool {
        direction == .bullish || direction == .bearish
    }

    private func marketDirection(_ direction: DirectionalControl) -> MarketDirection? {
        if direction == .bullish { return .bullish }
        if direction == .bearish { return .bearish }
        return nil
    }
}
