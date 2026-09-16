import Foundation

enum PaperAccountPreset: String, Codable, CaseIterable, Identifiable, Sendable {
    case account1000
    case account5000

    var id: String { rawValue }

    var startingBalance: Double {
        switch self {
        case .account1000: return 1_000
        case .account5000: return 5_000
        }
    }

    var displayName: String {
        switch self {
        case .account1000: return "$1,000 App Demo"
        case .account5000: return "$5,000 App Demo"
        }
    }
}

enum PaperTradeState: String, Codable, CaseIterable, Sendable {
    case waitingForEntry
    case open
    case won
    case lost
    case ambiguous
}

struct PaperTrade: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    let signalID: UUID
    let symbol: String
    let direction: MarketDirection
    let geometry: TradeGeometry
    let riskTier: RiskTier
    let signaledAt: Date
    var state: PaperTradeState
    var openedAt: Date?
    var closedAt: Date?
    var riskAmount: Double?
    var entryPrice: Double?
    var exitPrice: Double?
    var realizedR: Double?
    var profitLoss: Double?

    init(signal: LondresSignal) {
        precondition(signal.status == .valid)
        precondition(signal.geometry != nil)

        id = UUID()
        signalID = signal.id
        symbol = signal.context.symbol
        direction = signal.context.direction
        geometry = signal.geometry!
        riskTier = signal.riskTier
        signaledAt = signal.createdAt
        state = .waitingForEntry
    }
}

struct PaperAccount: Identifiable, Codable, Hashable, Sendable {
    let preset: PaperAccountPreset
    let currencyCode: String
    var balance: Double
    var trades: [PaperTrade]

    var id: String { preset.id }
    var startingBalance: Double { preset.startingBalance }
    var netProfit: Double { balance - startingBalance }
    var returnFraction: Double {
        guard startingBalance > 0 else { return 0 }
        return netProfit / startingBalance
    }
    var closedTrades: [PaperTrade] {
        trades.filter { $0.state == .won || $0.state == .lost }
    }
    var wins: Int { closedTrades.filter { $0.state == .won }.count }
    var losses: Int { closedTrades.filter { $0.state == .lost }.count }
    var winRate: Double {
        guard !closedTrades.isEmpty else { return 0 }
        return Double(wins) / Double(closedTrades.count)
    }

    init(preset: PaperAccountPreset, currencyCode: String = "USD") {
        self.preset = preset
        self.currencyCode = currencyCode
        balance = preset.startingBalance
        trades = []
    }
}

struct PaperPerformanceDisclosure: Sendable {
    static let title = "Internal App Demo Performance"
    static let body = "These are internal simulated accounts owned by the app. No brokerage account, broker credentials, broker API, or order-routing connection is used. Validated Londres signals are paper-executed against market-price data to demonstrate entry, risk, stop, target and resulting simulated P&L. Results are hypothetical, are not broker statements, and do not guarantee future returns. Commissions, slippage, latency, financing and taxes are excluded until explicitly modeled."
}
