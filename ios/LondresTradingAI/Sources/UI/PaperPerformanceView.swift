import SwiftUI

struct PaperPerformanceView: View {
    @EnvironmentObject private var paper: PaperTradingStore

    var body: some View {
        List {
            Section {
                Text(PaperPerformanceDisclosure.body)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } header: {
                Text(PaperPerformanceDisclosure.title)
            }

            ForEach(paper.accounts) { account in
                Section(account.preset.displayName) {
                    AnalyticsLine(label: "Starting balance", value: money(account.startingBalance))
                    AnalyticsLine(label: "Current balance", value: money(account.balance))
                    AnalyticsLine(label: "Net P&L", value: signedMoney(account.netProfit))
                    AnalyticsLine(label: "Return", value: account.returnFraction.formatted(.percent.precision(.fractionLength(2))))
                    AnalyticsLine(label: "Closed trades", value: "\(account.closedTrades.count)")
                    AnalyticsLine(label: "Win rate", value: account.winRate.formatted(.percent.precision(.fractionLength(1))))

                    if account.trades.isEmpty {
                        Text("No validated Londres signal has been tracked yet.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(account.trades.prefix(10)) { trade in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text("\(trade.symbol) · \(trade.direction.rawValue.uppercased())")
                                        .font(.subheadline.weight(.semibold))
                                    Spacer()
                                    Text(trade.state.rawValue)
                                        .font(.caption)
                                }
                                if let pnl = trade.profitLoss {
                                    Text("P&L \(signedMoney(pnl)) · \(trade.realizedR.map { String(format: "%.2fR", $0) } ?? "—")")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }

#if DEBUG
            Section {
                Button("Reset paper accounts", role: .destructive) {
                    paper.reset()
                }
            } footer: {
                Text("Development-only reset. This control is excluded from release builds.")
            }
#endif
        }
        .navigationTitle("Demo Performance")
    }

    private func money(_ value: Double) -> String {
        value.formatted(.currency(code: "USD"))
    }

    private func signedMoney(_ value: Double) -> String {
        let formatted = abs(value).formatted(.currency(code: "USD"))
        if value > 0 { return "+\(formatted)" }
        if value < 0 { return "-\(formatted)" }
        return formatted
    }
}

private struct AnalyticsLine: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
            Spacer()
            Text(value).fontWeight(.semibold)
        }
    }
}
