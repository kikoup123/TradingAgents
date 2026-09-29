import Foundation

struct PerformanceSummary: Codable, Hashable, Sendable {
    let totalTrades: Int
    let closedTrades: Int
    let wins: Int
    let losses: Int
    let breakeven: Int
    let winRate: Double
    let netR: Double
    let averageR: Double
    let expectancyR: Double
    let profitFactor: Double?
    let averagePlanCompliance: Double
}

struct PerformanceBucket: Identifiable, Codable, Hashable, Sendable {
    let id: String
    let label: String
    let trades: Int
    let netR: Double
    let averageR: Double
    let winRate: Double
}

struct LondresPerformanceAnalyzer: Sendable {
    func summary(for trades: [JournalTrade]) -> PerformanceSummary {
        let closed = trades.filter { $0.outcome == .win || $0.outcome == .loss || $0.outcome == .breakeven }
        let wins = closed.filter { $0.outcome == .win }.count
        let losses = closed.filter { $0.outcome == .loss }.count
        let breakeven = closed.filter { $0.outcome == .breakeven }.count
        let rValues = closed.compactMap(\.execution.realizedR)
        let netR = rValues.reduce(0, +)
        let averageR = rValues.isEmpty ? 0 : netR / Double(rValues.count)
        let grossWins = rValues.filter { $0 > 0 }.reduce(0, +)
        let grossLosses = abs(rValues.filter { $0 < 0 }.reduce(0, +))
        let profitFactor = grossLosses > 0 ? grossWins / grossLosses : nil
        let compliance = trades.map { Double($0.review.planCompliance) }
        let averageCompliance = compliance.isEmpty ? 0 : compliance.reduce(0, +) / Double(compliance.count)

        return PerformanceSummary(
            totalTrades: trades.count,
            closedTrades: closed.count,
            wins: wins,
            losses: losses,
            breakeven: breakeven,
            winRate: closed.isEmpty ? 0 : Double(wins) / Double(closed.count),
            netR: netR,
            averageR: averageR,
            expectancyR: averageR,
            profitFactor: profitFactor,
            averagePlanCompliance: averageCompliance
        )
    }

    func byPlaybookModel(_ trades: [JournalTrade]) -> [PerformanceBucket] {
        buckets(trades: trades, key: { $0.playbookModel })
    }

    func bySymbol(_ trades: [JournalTrade]) -> [PerformanceBucket] {
        buckets(trades: trades, key: { $0.signal.context.symbol })
    }

    func bySession(_ trades: [JournalTrade]) -> [PerformanceBucket] {
        buckets(trades: trades, key: { $0.signal.context.session.rawValue })
    }

    private func buckets(
        trades: [JournalTrade],
        key: (JournalTrade) -> String
    ) -> [PerformanceBucket] {
        Dictionary(grouping: trades, by: key)
            .map { label, grouped in
                let closed = grouped.filter { $0.outcome == .win || $0.outcome == .loss || $0.outcome == .breakeven }
                let wins = closed.filter { $0.outcome == .win }.count
                let values = closed.compactMap(\.execution.realizedR)
                let net = values.reduce(0, +)
                return PerformanceBucket(
                    id: label,
                    label: label,
                    trades: grouped.count,
                    netR: net,
                    averageR: values.isEmpty ? 0 : net / Double(values.count),
                    winRate: closed.isEmpty ? 0 : Double(wins) / Double(closed.count)
                )
            }
            .sorted { $0.label.localizedCaseInsensitiveCompare($1.label) == .orderedAscending }
    }
}
