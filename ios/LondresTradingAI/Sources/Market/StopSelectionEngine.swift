import Foundation

enum StopSource: String, Codable, Hashable, Sendable {
    case iofRange = "IOF_RANGE"
    case smtProtected = "SMT_PROTECTED"
    case none = "NONE"
}

struct StopCandidate: Codable, Hashable, Sendable, Identifiable {
    let source: StopSource
    let anchorPrice: Double
    let placement: String
    let direction: MarketDirection
    let valid: Bool
    let distanceFromCurrent: Double
    let structuralSource: String
    let rangeLow: Double?
    let rangeHigh: Double?
    let sourcePosition: Int?
    let confirmedPosition: Int?

    var id: String { "\(source.rawValue)|\(anchorPrice)|\(sourcePosition ?? -1)" }
    var requiresBufferBeyondAnchor: Bool { true }
}

struct StopSelectionContext: Codable, Hashable, Sendable {
    let direction: MarketDirection
    let currentPrice: Double
    let candidates: [StopCandidate]
    let selectionRequired: Bool
    let reasonCodes: [String]

    var allowedSources: [StopSource] {
        candidates.filter(\.valid).map(\.source)
    }
}

struct ValidatedStopSelection: Codable, Hashable, Sendable {
    let valid: Bool
    let selectedSource: StopSource
    let selectedAnchorPrice: Double?
    let placement: String?
    let reason: String
}

struct StopSelectionEngine: Sendable {
    func analyze(
        direction: MarketDirection,
        currentPrice: Double,
        orderFlow: OrderFlowResult,
        validationCSD: CSDEvent,
        postCSDIOFC: IOFCResult
    ) -> StopSelectionContext {
        var reasons: [String] = []
        var candidates: [StopCandidate] = []

        if let candidate = iofCandidate(
            direction: direction,
            currentPrice: currentPrice,
            orderFlow: orderFlow,
            validationCSD: validationCSD,
            postCSDIOFC: postCSDIOFC
        ) {
            candidates.append(candidate)
        } else {
            reasons.append("NO_VALID_POST_CSD_DIRECTIONAL_IOF_RANGE")
        }

        let protected = validationCSD.protectedExtreme
        let protectedValid = direction == .bullish ? protected < currentPrice : protected > currentPrice
        candidates.append(
            StopCandidate(
                source: .smtProtected,
                anchorPrice: protected,
                placement: direction == .bullish ? "BELOW_PROTECTED_LOW" : "ABOVE_PROTECTED_HIGH",
                direction: direction,
                valid: protectedValid,
                distanceFromCurrent: abs(currentPrice - protected),
                structuralSource: "POST_SMT_CSD_PROTECTED_EXTREME",
                rangeLow: nil,
                rangeHigh: nil,
                sourcePosition: nil,
                confirmedPosition: validationCSD.confirmationPosition
            )
        )

        let valid = candidates.filter(\.valid)
        if valid.isEmpty {
            reasons.append("NO_VALID_STRUCTURAL_STOP_OPTION")
        } else if valid.count == 1 {
            reasons.append("SINGLE_STRUCTURAL_STOP_OPTION_AVAILABLE")
        } else {
            reasons.append("TRADER_MAY_SELECT_IOF_OR_SMT_PROTECTED_STOP")
        }

        return StopSelectionContext(
            direction: direction,
            currentPrice: currentPrice,
            candidates: candidates,
            selectionRequired: !valid.isEmpty,
            reasonCodes: reasons
        )
    }

    func validateSelection(
        _ context: StopSelectionContext,
        selectedSource: StopSource
    ) -> ValidatedStopSelection {
        let valid = context.candidates.filter(\.valid)
        guard !valid.isEmpty else {
            return ValidatedStopSelection(
                valid: false,
                selectedSource: .none,
                selectedAnchorPrice: nil,
                placement: nil,
                reason: "NO_VALID_STRUCTURAL_STOP_OPTION"
            )
        }

        if valid.count == 1, let only = valid.first {
            return ValidatedStopSelection(
                valid: true,
                selectedSource: only.source,
                selectedAnchorPrice: only.anchorPrice,
                placement: only.placement,
                reason: "ONLY_VALID_STRUCTURAL_STOP_SELECTED"
            )
        }

        guard let selected = valid.first(where: { $0.source == selectedSource }) else {
            return ValidatedStopSelection(
                valid: false,
                selectedSource: .none,
                selectedAnchorPrice: nil,
                placement: nil,
                reason: "TRADER_SELECTED_STOP_OUTSIDE_ALLOWED_CANDIDATES"
            )
        }
        return ValidatedStopSelection(
            valid: true,
            selectedSource: selected.source,
            selectedAnchorPrice: selected.anchorPrice,
            placement: selected.placement,
            reason: "TRADER_STRUCTURAL_STOP_SELECTION_ACCEPTED"
        )
    }

    private func iofCandidate(
        direction: MarketDirection,
        currentPrice: Double,
        orderFlow: OrderFlowResult,
        validationCSD: CSDEvent,
        postCSDIOFC: IOFCResult
    ) -> StopCandidate? {
        let ranges = direction == .bullish
            ? orderFlow.activeSupportRanges
            : orderFlow.activeResistanceRanges
        let expected: OrderFlowDirection = direction == .bullish ? .bullish : .bearish

        let eligible = ranges.filter {
            $0.direction == expected
                && $0.sourcePosition > validationCSD.confirmationPosition
                && $0.status == .confirmed
        }
        let selected = eligible.max { left, right in
            let lConfirmed = left.confirmedPosition ?? -1
            let rConfirmed = right.confirmedPosition ?? -1
            if lConfirmed != rConfirmed { return lConfirmed < rConfirmed }
            return left.sourcePosition < right.sourcePosition
        } ?? postCSDIOFC.confirmationRange

        guard let selected else { return nil }
        let anchor = direction == .bullish ? selected.low : selected.high
        let valid = direction == .bullish ? anchor < currentPrice : anchor > currentPrice
        return StopCandidate(
            source: .iofRange,
            anchorPrice: anchor,
            placement: direction == .bullish ? "BELOW_RANGE_LOW" : "ABOVE_RANGE_HIGH",
            direction: direction,
            valid: valid,
            distanceFromCurrent: abs(currentPrice - anchor),
            structuralSource: "LATEST_VALID_DIRECTIONAL_IOF_RANGE_AFTER_CSD",
            rangeLow: selected.low,
            rangeHigh: selected.high,
            sourcePosition: selected.sourcePosition,
            confirmedPosition: selected.confirmedPosition
        )
    }
}
