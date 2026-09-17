import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var signals: [LondresSignal]
    @Published var localization: LocalizationPreferences
    @Published private(set) var latestStrategyResult: LondresStrategyResult?
    @Published private(set) var latestStrategyError: String?
    @Published private(set) var strategyBlockers: [String] = []

    let signalEngine = LondresSignalEngine()
    let strategyPipeline = LondresStrategyPipeline()
    let performanceAnalyzer = LondresPerformanceAnalyzer()
    let aiService: any LondresAIService
    let marketDataCoordinator: LondresMarketDataCoordinator

    private var emittedStrategyKeys = Set<String>()

    init(
        signals: [LondresSignal] = [],
        localization: LocalizationPreferences = .default,
        aiService: any LondresAIService = DeterministicExplanationService(),
        marketDataCoordinator: LondresMarketDataCoordinator? = nil
    ) {
        self.localization = localization
        self.aiService = aiService
        self.signals = signals
        self.marketDataCoordinator = marketDataCoordinator
            ?? LondresMarketDataCoordinator(
                runtimeConfiguration: MarketDataGatewayConfiguration.fromRuntime()
            )
    }

    func evaluate(_ input: LondresSignalInput) {
        signals.insert(signalEngine.evaluate(input), at: 0)
    }

    @discardableResult
    func evaluateStrategy(
        _ input: LondresStrategyInput,
        paperTradingStore: PaperTradingStore
    ) -> LondresStrategyResult? {
        do {
            let result = try strategyPipeline.analyze(input)
            latestStrategyResult = result
            latestStrategyError = nil
            strategyBlockers = result.blockerCodes

            guard let signal = result.signal, signal.status == .valid else {
                return result
            }
            let key = strategyKey(signal: signal, result: result)
            guard emittedStrategyKeys.insert(key).inserted else {
                return result
            }

            signals.insert(signal, at: 0)
            paperTradingStore.register(
                signal: signal,
                signaledAt: result.entryExecution?.entry?.time
            )
            return result
        } catch {
            latestStrategyResult = nil
            latestStrategyError = String(describing: error)
            strategyBlockers = ["STRATEGY_PIPELINE_ERROR"]
            return nil
        }
    }

    func processFiveMinuteMarketCandles(
        _ candles: [MarketCandle],
        paperTradingStore: PaperTradingStore
    ) {
        for candle in candles.sorted(by: { $0.openTime < $1.openTime })
        where candle.timeframe == .fiveMinute {
            paperTradingStore.process(candle: candle)
        }
    }

    func startMarketData(
        paperTradingStore: PaperTradingStore,
        plan: LondresMarketFeedPlan = .nq,
        refreshInterval: TimeInterval = 30
    ) {
        marketDataCoordinator.start(
            plan: plan,
            appModel: self,
            paperTradingStore: paperTradingStore,
            refreshInterval: refreshInterval
        )
    }

    func stopMarketData() {
        marketDataCoordinator.stop()
    }

    private func strategyKey(signal: LondresSignal, result: LondresStrategyResult) -> String {
        let entryTime = result.entryExecution?.entry?.time.timeIntervalSince1970
            ?? signal.createdAt.timeIntervalSince1970
        let geometry = signal.geometry
        return [
            signal.context.symbol,
            signal.context.direction.rawValue,
            String(entryTime),
            String(geometry?.entry ?? 0),
            String(geometry?.stop ?? 0),
            String(geometry?.target ?? 0)
        ].joined(separator: "|")
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
