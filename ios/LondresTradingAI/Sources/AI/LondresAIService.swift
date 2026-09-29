import Foundation

struct AITradeContext: Codable, Hashable, Sendable {
    let symbol: String
    let direction: String
    let session: String
    let higherTimeframeControl: String
    let weeklyProfile: String
    let dailyProfile: String
    let h4Profile: String
    let liquidityNarrative: String
    let smtDetected: Bool
    let csdConfirmed: Bool
    let iofAligned: Bool
    let timePriceValid: Bool
    let entryZoneValid: Bool
    let status: String
    let entry: Double?
    let stop: Double?
    let target: Double?
    let rewardToRisk: Double?
    let deterministicReasons: [String]

    init(signal: LondresSignal) {
        symbol = signal.context.symbol
        direction = signal.context.direction.rawValue
        session = signal.context.session.rawValue
        higherTimeframeControl = signal.context.higherTimeframeControl.rawValue
        weeklyProfile = signal.context.weeklyProfile
        dailyProfile = signal.context.dailyProfile
        h4Profile = signal.context.h4Profile
        liquidityNarrative = signal.context.liquidityNarrative
        smtDetected = signal.evidence.smtDetected
        csdConfirmed = signal.evidence.csdConfirmed
        iofAligned = signal.evidence.iofAligned
        timePriceValid = signal.evidence.timePriceValid
        entryZoneValid = signal.evidence.entryZoneValid
        status = signal.status.rawValue
        entry = signal.geometry?.entry
        stop = signal.geometry?.stop
        target = signal.geometry?.target
        rewardToRisk = signal.geometry?.rewardToRisk
        deterministicReasons = signal.reasons
    }
}

struct AIJournalContext: Codable, Hashable, Sendable {
    let trade: JournalTrade
    let summary: PerformanceSummary
}

protocol LondresAIService: Sendable {
    func explainSignal(_ context: AITradeContext, language: AppLanguage) async throws -> String
    func reviewTrade(_ context: AIJournalContext, language: AppLanguage) async throws -> String
}

/// Offline deterministic explanation used when no remote AI provider is configured.
/// It never changes setup validity, geometry, or risk.
struct DeterministicExplanationService: LondresAIService {
    func explainSignal(_ context: AITradeContext, language: AppLanguage) async throws -> String {
        let rr = context.rewardToRisk.map { String(format: "%.2fR", $0) } ?? "n/a"
        return "\(context.symbol) \(context.direction.uppercased()) — status: \(context.status). "
            + context.deterministicReasons.joined(separator: " ")
            + " Planned reward/risk: \(rr)."
    }

    func reviewTrade(_ context: AIJournalContext, language: AppLanguage) async throws -> String {
        let realized = context.trade.execution.realizedR.map { String(format: "%.2fR", $0) } ?? "n/a"
        return "Trade result: \(realized). Plan compliance: \(context.trade.review.planCompliance)%. "
            + "Strategy statistics remain separate from execution-quality statistics."
    }
}

/// Security boundary for future cloud AI implementations.
/// Implementations may explain or summarize structured evidence, but must never
/// mutate LondresSignalStatus, TradeGeometry, RiskTier, or deterministic evidence.
protocol LondresAIProviderTransport: Sendable {
    func complete(system: String, user: String) async throws -> String
}
