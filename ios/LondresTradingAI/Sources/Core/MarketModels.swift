import Foundation

enum MarketDirection: String, Codable, CaseIterable, Sendable {
    case bullish
    case bearish
}

enum LondresTimeframe: String, Codable, CaseIterable, Sendable {
    case weekly = "W"
    case daily = "D"
    case fourHour = "H4"
    case oneHour = "H1"
    case fifteenMinute = "M15"
    case fiveMinute = "M5"
    case threeMinute = "M3"
    case oneMinute = "M1"
}

enum LondresSession: String, Codable, CaseIterable, Sendable {
    case asia
    case london
    case newYorkAM
    case newYorkPM
    case outsideModel
}

enum RiskTier: Double, Codable, CaseIterable, Sendable {
    case conservative = 0.03
    case standard = 0.05
    case maximum = 0.10

    var percentLabel: String {
        String(format: "%.0f%%", rawValue * 100)
    }
}

struct PriceRange: Codable, Hashable, Sendable {
    let low: Double
    let high: Double

    init(low: Double, high: Double) {
        self.low = min(low, high)
        self.high = max(low, high)
    }

    func contains(_ price: Double) -> Bool {
        price >= low && price <= high
    }
}

struct LondresMarketContext: Codable, Hashable, Sendable {
    let symbol: String
    let direction: MarketDirection
    let session: LondresSession
    let higherTimeframeControl: MarketDirection
    let weeklyProfile: String
    let dailyProfile: String
    let h4Profile: String
    let liquidityNarrative: String
    let timestamp: Date
}

struct LondresSetupEvidence: Codable, Hashable, Sendable {
    let smtDetected: Bool
    let csdConfirmed: Bool
    let iofAligned: Bool
    let timePriceValid: Bool
    let entryZoneValid: Bool
    let liquidityRaidConfirmed: Bool
    let mmxmNarrativeAligned: Bool
    let iofRange: PriceRange?

    static let empty = LondresSetupEvidence(
        smtDetected: false,
        csdConfirmed: false,
        iofAligned: false,
        timePriceValid: false,
        entryZoneValid: false,
        liquidityRaidConfirmed: false,
        mmxmNarrativeAligned: false,
        iofRange: nil
    )
}

struct TradeGeometry: Codable, Hashable, Sendable {
    let entry: Double
    let stop: Double
    let target: Double

    var riskDistance: Double {
        abs(entry - stop)
    }

    var rewardDistance: Double {
        abs(target - entry)
    }

    var rewardToRisk: Double {
        guard riskDistance > 0 else { return 0 }
        return rewardDistance / riskDistance
    }

    func isValid(for direction: MarketDirection) -> Bool {
        let values = [entry, stop, target]
        guard values.allSatisfy({ $0.isFinite && $0 > 0 }) else { return false }

        switch direction {
        case .bullish:
            return stop < entry && entry < target
        case .bearish:
            return target < entry && entry < stop
        }
    }
}
