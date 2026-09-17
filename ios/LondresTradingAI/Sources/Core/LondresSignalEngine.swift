import Foundation

enum LondresSignalStatus: String, Codable, CaseIterable, Sendable {
    case waitingForSMT
    case waitingForCSD
    case waitingForIOF
    case invalidTimePrice
    case waitingForEntryZone
    case invalidGeometry
    case valid

    var isActionable: Bool { self == .valid }
}

struct LondresSignal: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    let context: LondresMarketContext
    let evidence: LondresSetupEvidence
    let geometry: TradeGeometry?
    let riskTier: RiskTier
    let status: LondresSignalStatus
    let reasons: [String]
    let createdAt: Date

    var isActionable: Bool { status.isActionable }
}

struct LondresSignalInput: Codable, Hashable, Sendable {
    let context: LondresMarketContext
    let evidence: LondresSetupEvidence
    let geometry: TradeGeometry?
    let riskTier: RiskTier
}

struct LondresSignalEngine: Sendable {
    /// Deterministic setup qualification. AI must never override this result.
    ///
    /// Canonical invariant:
    /// SMT_DETECTED + CSD_CONFIRMED + IOF_ALIGNED + TIME_PRICE_VALID + ENTRY_ZONE_VALID
    /// = VALID_LONDRES_SETUP
    func evaluate(_ input: LondresSignalInput, now: Date = Date()) -> LondresSignal {
        let evidence = input.evidence
        let status: LondresSignalStatus
        var reasons: [String] = []

        if !evidence.smtDetected {
            status = .waitingForSMT
            reasons.append("SMT has not been detected. SMT alone is not actionable, but it is required before CSD validation.")
        } else if !evidence.csdConfirmed {
            status = .waitingForCSD
            reasons.append("SMT is present, but price delivery has not confirmed CSD.")
        } else if !evidence.iofAligned {
            status = .waitingForIOF
            reasons.append("CSD is confirmed, but post-CSD institutional order flow is not aligned.")
        } else if !evidence.timePriceValid {
            status = .invalidTimePrice
            reasons.append("The setup fails Londres time-and-price context validation.")
        } else if !evidence.entryZoneValid {
            status = .waitingForEntryZone
            reasons.append("The directional model is valid, but price has not reached the deterministic entry zone.")
        } else if let geometry = input.geometry, !geometry.isValid(for: input.context.direction) {
            status = .invalidGeometry
            reasons.append("Entry, stop, and target geometry are inconsistent with the validated direction.")
        } else if input.geometry == nil {
            status = .invalidGeometry
            reasons.append("A valid setup requires deterministic entry, stop, and target geometry.")
        } else {
            status = .valid
            reasons.append("SMT detected.")
            reasons.append("CSD confirmed.")
            reasons.append("Post-CSD IOF aligned.")
            reasons.append("Time-and-price context valid.")
            reasons.append("Deterministic entry zone valid.")
        }

        return LondresSignal(
            id: UUID(),
            context: input.context,
            evidence: evidence,
            geometry: input.geometry,
            riskTier: input.riskTier,
            status: status,
            reasons: reasons,
            createdAt: now
        )
    }
}
