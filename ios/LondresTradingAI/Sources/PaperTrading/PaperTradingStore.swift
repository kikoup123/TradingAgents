import Foundation

@MainActor
final class PaperTradingStore: ObservableObject {
    @Published private(set) var accounts: [PaperAccount]
    @Published private(set) var lastError: String?

    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(fileURL: URL? = nil) {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let folder = base.appendingPathComponent("LondresTradingAI", isDirectory: true)
        self.fileURL = fileURL ?? folder.appendingPathComponent("paper_accounts.json")

        encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601

        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        accounts = PaperAccountPreset.allCases.map { PaperAccount(preset: $0) }
        load()
        ensurePresets()
    }

    func register(signal: LondresSignal) {
        guard signal.status == .valid, signal.geometry != nil else { return }

        for index in accounts.indices {
            guard !accounts[index].trades.contains(where: { $0.signalID == signal.id }) else { continue }
            accounts[index].trades.insert(PaperTrade(signal: signal), at: 0)
        }
        persist()
    }

    func process(candle: MarketCandle) {
        guard candle.isValid else { return }

        var changed = false
        for accountIndex in accounts.indices {
            var account = accounts[accountIndex]

            for tradeIndex in account.trades.indices {
                var trade = account.trades[tradeIndex]
                guard trade.symbol == candle.symbol else { continue }
                guard candle.openTime >= trade.signaledAt else { continue }

                switch trade.state {
                case .waitingForEntry:
                    guard touches(trade.geometry.entry, candle: candle) else { continue }
                    trade.openedAt = candle.openTime
                    trade.entryPrice = trade.geometry.entry
                    trade.riskAmount = account.balance * trade.riskTier.rawValue

                    let stopTouched = touches(trade.geometry.stop, candle: candle)
                    let targetTouched = touches(trade.geometry.target, candle: candle)
                    if stopTouched || targetTouched {
                        trade.state = .ambiguous
                    } else {
                        trade.state = .open
                    }
                    account.trades[tradeIndex] = trade
                    changed = true

                case .open:
                    let stopTouched = touches(trade.geometry.stop, candle: candle)
                    let targetTouched = touches(trade.geometry.target, candle: candle)

                    if stopTouched && targetTouched {
                        trade.state = .ambiguous
                        account.trades[tradeIndex] = trade
                        changed = true
                    } else if targetTouched {
                        close(&trade, in: &account, at: trade.geometry.target, rMultiple: trade.geometry.rewardToRisk, time: candle.openTime)
                        account.trades[tradeIndex] = trade
                        changed = true
                    } else if stopTouched {
                        close(&trade, in: &account, at: trade.geometry.stop, rMultiple: -1, time: candle.openTime)
                        account.trades[tradeIndex] = trade
                        changed = true
                    }

                case .won, .lost, .ambiguous:
                    continue
                }
            }

            accounts[accountIndex] = account
        }

        if changed { persist() }
    }

    func reset() {
        accounts = PaperAccountPreset.allCases.map { PaperAccount(preset: $0) }
        persist()
    }

    private func close(
        _ trade: inout PaperTrade,
        in account: inout PaperAccount,
        at exitPrice: Double,
        rMultiple: Double,
        time: Date
    ) {
        guard let riskAmount = trade.riskAmount else {
            trade.state = .ambiguous
            return
        }
        let pnl = riskAmount * rMultiple
        trade.closedAt = time
        trade.exitPrice = exitPrice
        trade.realizedR = rMultiple
        trade.profitLoss = pnl
        trade.state = rMultiple >= 0 ? .won : .lost
        account.balance += pnl
    }

    private func touches(_ price: Double, candle: MarketCandle) -> Bool {
        candle.low <= price && price <= candle.high
    }

    private func ensurePresets() {
        for preset in PaperAccountPreset.allCases where !accounts.contains(where: { $0.preset == preset }) {
            accounts.append(PaperAccount(preset: preset))
        }
        accounts.sort { $0.startingBalance < $1.startingBalance }
    }

    private func load() {
        do {
            guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
            let data = try Data(contentsOf: fileURL)
            accounts = try decoder.decode([PaperAccount].self, from: data)
            lastError = nil
        } catch {
            lastError = "Paper account load failed: \(error.localizedDescription)"
            accounts = PaperAccountPreset.allCases.map { PaperAccount(preset: $0) }
        }
    }

    private func persist() {
        do {
            let directory = fileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try encoder.encode(accounts)
            try data.write(to: fileURL, options: .atomic)
            lastError = nil
        } catch {
            lastError = "Paper account save failed: \(error.localizedDescription)"
        }
    }
}
