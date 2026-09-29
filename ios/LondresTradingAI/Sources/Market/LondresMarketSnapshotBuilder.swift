import Foundation

struct LondresMarketFeedPlan: Hashable, Sendable {
    let symbol: String
    let smtGroup: String
    let smtSymbols: [String]
    let tickSize: Double?
    let demoPolicy: LondresDemoExecutionPolicy

    init(
        symbol: String,
        smtGroup: String,
        smtSymbols: [String],
        tickSize: Double?,
        demoPolicy: LondresDemoExecutionPolicy = .conservative
    ) {
        self.symbol = symbol.uppercased()
        self.smtGroup = smtGroup.uppercased()
        self.smtSymbols = smtSymbols.map { $0.uppercased() }
        self.tickSize = tickSize
        self.demoPolicy = demoPolicy
    }

    static let nq = LondresMarketFeedPlan(
        symbol: "NQ",
        smtGroup: "US_INDEX",
        smtSymbols: ["NQ", "ES", "YM"],
        tickSize: 0.25
    )
}

struct LondresMarketSnapshot: Sendable {
    let strategyInput: LondresStrategyInput
    let executionBars: [MarketCandle]
    let quote: MarketQuote
    let providerID: String
}

enum LondresMarketSnapshotError: Error, Equatable {
    case missingPrimaryBars(String)
    case missingSMTBars(String)
    case invalidQuote
}

struct LondresMarketSnapshotBuilder: Sendable {
    static let hierarchy = ["1W", "1D", "4H", "1H", "15m", "5m"]

    private static let frameRequests: [(key: String, timeframe: LondresTimeframe, limit: Int)] = [
        ("1W", .weekly, 104),
        ("1D", .daily, 260),
        ("4H", .fourHour, 600),
        ("1H", .oneHour, 1_000),
        ("15m", .fifteenMinute, 1_500),
        ("5m", .fiveMinute, 2_000)
    ]

    init() {}

    func build(
        provider: any MarketDataProvider,
        plan: LondresMarketFeedPlan
    ) async throws -> LondresMarketSnapshot {
        var timeframeBars: [String: [MarketCandle]] = [:]
        for request in Self.frameRequests {
            let bars = try await provider.candles(
                symbol: plan.symbol,
                timeframe: request.timeframe,
                limit: request.limit
            )
            guard !bars.isEmpty else {
                throw LondresMarketSnapshotError.missingPrimaryBars(request.key)
            }
            timeframeBars[request.key] = bars
        }

        guard let executionBars = timeframeBars[LondresStrategyPipeline.executionTimeframe],
              !executionBars.isEmpty else {
            throw LondresMarketSnapshotError.missingPrimaryBars(
                LondresStrategyPipeline.executionTimeframe
            )
        }
        guard let intradayBars = timeframeBars["1H"], !intradayBars.isEmpty else {
            throw LondresMarketSnapshotError.missingPrimaryBars("1H")
        }

        let minuteBars = try await provider.candles(
            symbol: plan.symbol,
            timeframe: .oneMinute,
            limit: 2_160
        )
        guard !minuteBars.isEmpty else {
            throw LondresMarketSnapshotError.missingPrimaryBars("1m")
        }

        var smtBars: [String: [MarketCandle]] = [:]
        for symbol in plan.smtSymbols {
            if symbol == plan.symbol {
                smtBars[symbol] = executionBars
            } else {
                let bars = try await provider.candles(
                    symbol: symbol,
                    timeframe: .fiveMinute,
                    limit: 2_000
                )
                guard !bars.isEmpty else {
                    throw LondresMarketSnapshotError.missingSMTBars(symbol)
                }
                smtBars[symbol] = bars
            }
        }

        let quote = try await provider.quote(symbol: plan.symbol)
        guard let currentPrice = quote.mid, currentPrice.isFinite, currentPrice > 0 else {
            throw LondresMarketSnapshotError.invalidQuote
        }

        let input = LondresStrategyInput(
            timeframeBars: timeframeBars,
            intradayBars: intradayBars,
            minuteBars: minuteBars,
            csdBars: executionBars,
            smtBars: smtBars,
            smtGroup: plan.smtGroup,
            smtTimeframe: LondresStrategyPipeline.executionTimeframe,
            csdTimeframe: LondresStrategyPipeline.executionTimeframe,
            hierarchy: Self.hierarchy,
            liquidityTimeframe: "4H",
            weeklyProfileTimeframe: "1D",
            weeklyControlTimeframe: "4H",
            dailyOrderFlowTimeframe: "1D",
            h4OrderFlowTimeframe: "4H",
            asOf: quote.timestamp,
            currentPrice: currentPrice,
            instrument: LondresInstrumentMetadata(
                symbol: plan.symbol,
                tickSize: plan.tickSize
            ),
            demoPolicy: plan.demoPolicy
        )

        return LondresMarketSnapshot(
            strategyInput: input,
            executionBars: executionBars,
            quote: quote,
            providerID: provider.providerID
        )
    }
}
