import SwiftUI
import StoreKit

struct RootTabView: View {
    var body: some View {
        TabView {
            NavigationStack { DashboardView() }
                .tabItem { Label("Dashboard", systemImage: "square.grid.2x2") }

            NavigationStack { SignalsView() }
                .tabItem { Label("Signals", systemImage: "waveform.path.ecg") }

            NavigationStack { JournalView() }
                .tabItem { Label("Journal", systemImage: "book.closed") }

            NavigationStack { AnalyticsView() }
                .tabItem { Label("Analytics", systemImage: "chart.xyaxis.line") }

            NavigationStack { SettingsView() }
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}

private struct DashboardView: View {
    @EnvironmentObject private var app: AppModel
    @EnvironmentObject private var journal: JournalStore
    @EnvironmentObject private var paper: PaperTradingStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let signal = app.signals.first {
                    SignalHeroCard(signal: signal)
                }

                let summary = app.performanceAnalyzer.summary(for: journal.trades)
                HStack(spacing: 12) {
                    MetricCard(title: "Net R", value: String(format: "%.2fR", summary.netR))
                    MetricCard(title: "Win rate", value: String(format: "%.0f%%", summary.winRate * 100))
                }
                HStack(spacing: 12) {
                    MetricCard(title: "Trades", value: "\(summary.totalTrades)")
                    MetricCard(title: "Plan", value: String(format: "%.0f%%", summary.averagePlanCompliance))
                }

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Demo Accounts")
                                .font(.headline)
                            Text("Simulated / Paper Performance")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        NavigationLink("Details") {
                            PaperPerformanceView()
                        }
                    }

                    ForEach(paper.accounts) { account in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(account.preset.displayName)
                                    .font(.subheadline.weight(.semibold))
                                Text("Start \(account.startingBalance.formatted(.currency(code: account.currencyCode)))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(account.balance.formatted(.currency(code: account.currencyCode)))
                                    .font(.headline)
                                Text(account.returnFraction.formatted(.percent.precision(.fractionLength(2))))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }

                    Text("Paper results are hypothetical and are not a guarantee of future returns.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
            }
            .padding()
        }
        .navigationTitle("Londres Trading AI")
    }
}

private struct SignalsView: View {
    @EnvironmentObject private var app: AppModel
    @EnvironmentObject private var journal: JournalStore
    @EnvironmentObject private var paper: PaperTradingStore

    var body: some View {
        List {
            ForEach(app.signals) { signal in
                Section {
                    SignalDetailContent(signal: signal)
                    Button {
                        journal.add(signal: signal)
                    } label: {
                        Label("Add to Journal", systemImage: "plus.circle")
                    }
                    .disabled(!signal.isActionable)

                    Button {
                        paper.register(signal: signal)
                    } label: {
                        Label("Track in $1K + $5K Demo Accounts", systemImage: "chart.line.uptrend.xyaxis")
                    }
                    .disabled(!signal.isActionable)
                } header: {
                    Text("\(signal.context.symbol) · \(signal.context.direction.rawValue.uppercased())")
                }
            }
        }
        .navigationTitle("Signals")
    }
}

private struct JournalView: View {
    @EnvironmentObject private var journal: JournalStore

    var body: some View {
        Group {
            if journal.trades.isEmpty {
                ContentUnavailableView(
                    "No Journal Trades",
                    systemImage: "book.closed",
                    description: Text("Add a validated Londres signal to start tracking execution and performance.")
                )
            } else {
                List {
                    ForEach(journal.trades) { trade in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(trade.signal.context.symbol).font(.headline)
                                Spacer()
                                Text(trade.outcome.rawValue.uppercased()).font(.caption.weight(.semibold))
                            }
                            Text(trade.playbookModel)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                            HStack {
                                Text("Plan \(trade.review.planCompliance)%")
                                Spacer()
                                Text(trade.execution.realizedR.map { String(format: "%.2fR", $0) } ?? "Open")
                            }
                            .font(.caption)
                        }
                    }
                    .onDelete(perform: journal.delete)
                }
            }
        }
        .navigationTitle("Trading Journal")
    }
}

private struct AnalyticsView: View {
    @EnvironmentObject private var app: AppModel
    @EnvironmentObject private var journal: JournalStore

    var body: some View {
        let summary = app.performanceAnalyzer.summary(for: journal.trades)
        let models = app.performanceAnalyzer.byPlaybookModel(journal.trades)

        List {
            Section("Performance") {
                AnalyticsRow(label: "Closed trades", value: "\(summary.closedTrades)")
                AnalyticsRow(label: "Win rate", value: String(format: "%.1f%%", summary.winRate * 100))
                AnalyticsRow(label: "Net R", value: String(format: "%.2fR", summary.netR))
                AnalyticsRow(label: "Average R", value: String(format: "%.2fR", summary.averageR))
                AnalyticsRow(label: "Profit factor", value: summary.profitFactor.map { String(format: "%.2f", $0) } ?? "—")
                AnalyticsRow(label: "Plan compliance", value: String(format: "%.1f%%", summary.averagePlanCompliance))
            }

            Section("Playbook") {
                if models.isEmpty {
                    Text("Performance by setup appears after journal trades are completed.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(models) { bucket in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(bucket.label).font(.headline)
                            Text("\(bucket.trades) trades · \(String(format: "%.2fR", bucket.netR)) · \(String(format: "%.0f%%", bucket.winRate * 100)) win rate")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Analytics")
    }
}

private struct SettingsView: View {
    @EnvironmentObject private var app: AppModel
    @EnvironmentObject private var subscriptions: SubscriptionStore

    var body: some View {
        Form {
            Section("Language") {
                Text("Interface: \(app.localization.interfaceLanguage.displayName)")
                Text("AI: \(app.localization.aiLanguage.displayName)")
                Text("Terminology: \(app.localization.terminologyMode.rawValue)")
            }

            Section("Subscription") {
                if subscriptions.isLoading {
                    ProgressView("Loading App Store products…")
                } else if subscriptions.products.isEmpty {
                    Text("StoreKit products are not configured in this build yet.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(subscriptions.products, id: \.id) { product in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(product.displayName)
                            Text(product.displayPrice)
                                .font(.headline)
                            if subscriptions.hasConfiguredIntroOffer(for: product) {
                                Text(subscriptions.isEligibleForIntroOffer(product) ? "Eligible for introductory free trial" : "Introductory offer configured; this Apple ID is not eligible")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Text(subscriptions.hasProAccess ? "Londres Pro active" : "Londres Pro not active")
                Text("Target offer: 1 month free, then the normal subscription price. The free-trial duration and eligibility are controlled by App Store Connect and StoreKit.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Execution Boundary") {
                Text("This iOS product generates analysis, signals, paper performance and journal intelligence. Broker order execution remains disabled in V1.")
                    .font(.footnote)
            }
        }
        .navigationTitle("Settings")
    }
}

private struct SignalHeroCard: View {
    let signal: LondresSignal

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(signal.context.symbol).font(.largeTitle.bold())
                    Text(signal.context.direction.rawValue.uppercased()).font(.headline)
                }
                Spacer()
                Text(signal.status == .valid ? "VALID" : "WAIT")
                    .font(.caption.bold())
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(.thinMaterial, in: Capsule())
            }
            SignalDetailContent(signal: signal)
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
    }
}

private struct SignalDetailContent: View {
    let signal: LondresSignal

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            EvidenceRow(name: "SMT", passed: signal.evidence.smtDetected)
            EvidenceRow(name: "CSD", passed: signal.evidence.csdConfirmed)
            EvidenceRow(name: "IOF", passed: signal.evidence.iofAligned)
            EvidenceRow(name: "Time & Price", passed: signal.evidence.timePriceValid)
            EvidenceRow(name: "Entry Zone", passed: signal.evidence.entryZoneValid)

            if let geometry = signal.geometry {
                Divider()
                AnalyticsRow(label: "Entry", value: formatPrice(geometry.entry))
                AnalyticsRow(label: "Stop", value: formatPrice(geometry.stop))
                AnalyticsRow(label: "Target", value: formatPrice(geometry.target))
                AnalyticsRow(label: "R:R", value: String(format: "1:%.2f", geometry.rewardToRisk))
                AnalyticsRow(label: "Risk tier", value: signal.riskTier.percentLabel)
            }
        }
    }

    private func formatPrice(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(2)))
    }
}

private struct EvidenceRow: View {
    let name: String
    let passed: Bool

    var body: some View {
        HStack {
            Text(name)
            Spacer()
            Image(systemName: passed ? "checkmark.circle.fill" : "clock")
                .accessibilityLabel(passed ? "Confirmed" : "Waiting")
        }
    }
}

private struct MetricCard: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2.bold())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct AnalyticsRow: View {
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
