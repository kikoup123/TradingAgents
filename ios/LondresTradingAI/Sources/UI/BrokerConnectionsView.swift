import SwiftUI

struct BrokerConnectionsView: View {
    @EnvironmentObject private var broker: CTraderBrokerStore
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            Section("cTrader Open API") {
                HStack {
                    Label("cTrader", systemImage: "link.circle")
                    Spacer()
                    Text(broker.state.displayName)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(broker.state == .connected ? .primary : .secondary)
                }

                if broker.state == .connected {
                    ForEach(broker.accounts) { account in
                        Button {
                            Task { await broker.loadAccount(account) }
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(account.broker ?? "cTrader Broker")
                                    .font(.headline)
                                Text(account.account)
                                    .font(.subheadline.monospacedDigit())
                                Label("View-only access", systemImage: "eye")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                    }

                    Button(role: .destructive) {
                        broker.disconnectDevice()
                    } label: {
                        Label("Disconnect This Device", systemImage: "link.badge.minus")
                    }
                } else {
                    Button {
                        guard let url = broker.authorizationStartURL() else { return }
                        openURL(url)
                    } label: {
                        Label("Connect cTrader", systemImage: "link.badge.plus")
                    }
                    .disabled(!broker.isConfigured || broker.state == .connecting)

                    if broker.state == .connecting {
                        ProgressView("Completing cTrader authorization…")
                    }
                }

                if let message = broker.errorMessage {
                    Text(message)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if let snapshot = broker.selectedAccountSnapshot {
                Section("Account Snapshot") {
                    if let brokerName = snapshot.broker {
                        LabeledContent("Broker", value: brokerName)
                    }
                    LabeledContent("Account", value: snapshot.maskedAccount)
                    if let currency = snapshot.currency {
                        LabeledContent("Currency", value: currency)
                    }
                    LabeledContent("Balance", value: snapshot.balance.formatted(.number.precision(.fractionLength(2))))
                    LabeledContent("Equity", value: snapshot.equity.formatted(.number.precision(.fractionLength(2))))
                    LabeledContent("Used margin", value: snapshot.usedMargin.formatted(.number.precision(.fractionLength(2))))
                    LabeledContent("Free margin", value: snapshot.freeMargin.formatted(.number.precision(.fractionLength(2))))
                }
            }

            Section("Safety Boundary") {
                Label("OAuth scope: accounts", systemImage: "lock.shield")
                Label("Order submission disabled", systemImage: "hand.raised.fill")
                Text("This phase can read authorized cTrader account information for the Trading Sand app. It cannot place, modify or close broker orders.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Text("Disconnecting here removes the encrypted broker session from this iPhone. cTrader access can also be revoked from your cTrader account settings.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Broker Connections")
        .task {
            await broker.refreshStatus()
        }
        .refreshable {
            await broker.refreshStatus()
        }
    }
}
