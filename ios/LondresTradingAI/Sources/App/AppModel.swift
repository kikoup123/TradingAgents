import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var signals: [LondresSignal]
    @Published var localization: LocalizationPreferences

    let signalEngine = LondresSignalEngine()
    let performanceAnalyzer = LondresPerformanceAnalyzer()
    let aiService: any LondresAIService

    init(
        signals: [LondresSignal] = [],
        localization: LocalizationPreferences = .default,
        aiService: any LondresAIService = DeterministicExplanationService()
    ) {
        self.localization = localization
        self.aiService = aiService
        self.signals = signals
    }

    func evaluate(_ input: LondresSignalInput) {
        signals.insert(signalEngine.evaluate(input), at: 0)
    }

    static func demoSignal() -> LondresSignal {
        let context = LondresMarketContext(
            symbol: "NQ",
            direction: .bullish,
            session: .newYorkAM,
            higherTimeframeControl: .bullish,
            weeklyProfile: "Expansion",
            dailyProfile: "OHLC",
            h4Profile: "Bullish Expansion",
            liquidityNarrative: "Sell-side liquidity raid followed by bullish delivery shift.",
            timestamp: Date()
        )
        let evidence = LondresSetupEvidence(
            smtDetected: true,
            csdConfirmed: true,
            iofAligned: true,
            timePriceValid: true,
            entryZoneValid: true,
            liquidityRaidConfirmed: true,
            mmxmNarrativeAligned: true,
            iofRange: PriceRange(low: 24_575.00, high: 24_585.00)
        )
        let geometry = TradeGeometry(
            entry: 24_580.25,
            stop: 24_560.00,
            target: 24_721.75
        )
        return LondresSignalEngine().evaluate(
            LondresSignalInput(
                context: context,
                evidence: evidence,
                geometry: geometry,
                riskTier: .conservative
            )
        )
    }
}
