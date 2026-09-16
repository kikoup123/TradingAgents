import Foundation

enum LondresMarketDataRuntimeState: String, Sendable {
    case notConfigured = "NOT_CONFIGURED"
    case idle = "IDLE"
    case refreshing = "REFRESHING"
    case running = "RUNNING"
    case failed = "FAILED"
    case stopped = "STOPPED"
}

@MainActor
final class LondresMarketDataCoordinator: ObservableObject {
    @Published private(set) var state: LondresMarketDataRuntimeState
    @Published private(set) var providerID: String?
    @Published private(set) var lastRefreshAt: Date?
    @Published private(set) var lastError: String?

    private let provider: (any MarketDataProvider)?
    private let snapshotBuilder: LondresMarketSnapshotBuilder
    private var refreshTask: Task<Void, Never>?

    init(
        provider: (any MarketDataProvider)?,
        snapshotBuilder: LondresMarketSnapshotBuilder = LondresMarketSnapshotBuilder()
    ) {
        self.provider = provider
        self.snapshotBuilder = snapshotBuilder
        providerID = provider?.providerID
        state = provider == nil ? .notConfigured : .idle
    }

    convenience init(runtimeConfiguration: MarketDataGatewayConfiguration?) {
        let provider = runtimeConfiguration.map {
            CanonicalHTTPMarketDataAdapter(configuration: $0) as any MarketDataProvider
        }
        self.init(provider: provider)
    }

    @discardableResult
    func refresh(
        plan: LondresMarketFeedPlan,
        appModel: AppModel,
        paperTradingStore: PaperTradingStore
    ) async -> LondresStrategyResult? {
        guard let provider else {
            state = .notConfigured
            lastError = "LONDRES_MARKET_DATA_BASE_URL is not configured with an HTTPS endpoint."
            return nil
        }

        state = .refreshing
        do {
            let snapshot = try await snapshotBuilder.build(provider: provider, plan: plan)
            providerID = snapshot.providerID
            let result = appModel.evaluateStrategy(
                snapshot.strategyInput,
                paperTradingStore: paperTradingStore
            )
            appModel.processFiveMinuteMarketCandles(
                snapshot.executionBars,
                paperTradingStore: paperTradingStore
            )
            lastRefreshAt = snapshot.quote.timestamp
            lastError = nil
            state = .running
            return result
        } catch {
            lastError = String(describing: error)
            state = .failed
            return nil
        }
    }

    func start(
        plan: LondresMarketFeedPlan,
        appModel: AppModel,
        paperTradingStore: PaperTradingStore,
        refreshInterval: TimeInterval = 30
    ) {
        stop()
        guard provider != nil else {
            state = .notConfigured
            lastError = "LONDRES_MARKET_DATA_BASE_URL is not configured with an HTTPS endpoint."
            return
        }

        let interval = max(5, refreshInterval)
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                _ = await self.refresh(
                    plan: plan,
                    appModel: appModel,
                    paperTradingStore: paperTradingStore
                )
                do {
                    try await Task.sleep(
                        nanoseconds: UInt64(interval * 1_000_000_000)
                    )
                } catch {
                    return
                }
            }
        }
    }

    func stop() {
        refreshTask?.cancel()
        refreshTask = nil
        if provider == nil {
            state = .notConfigured
        } else {
            state = .stopped
        }
    }

    deinit {
        refreshTask?.cancel()
    }
}
