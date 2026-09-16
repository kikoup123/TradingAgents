import Foundation

enum FairValueGapStatus: String, Codable, Hashable, Sendable {
    case open = "OPEN"
    case partiallyRebalanced = "PARTIALLY_REBALANCED"
    case rebalanced = "REBALANCED"
    case invalidated = "INVALIDATED"
}

struct FairValueGap: Codable, Hashable, Sendable, Identifiable {
    let timeframe: String
    let direction: MarketDirection
    let low: Double
    let high: Double
    let consequentEncroachment: Double
    let sourcePosition: Int
    let formationPosition: Int
    let formationTime: Date
    let sourceLow: Double
    let sourceHigh: Double
    let structuralReference: CSDPivotReference?
    let fairValuationPoint: Double?
    var status: FairValueGapStatus = .open
    var firstTouchPosition: Int?
    var firstTouchTime: Date?
    var filledPosition: Int?
    var invalidatedPosition: Int?
    var invalidatedTime: Date?
    var pairingReturnPosition: Int?
    var pairingReturnTime: Date?
    var pairingRejectionPosition: Int?
    var reasonCodes: [String]

    var id: String { "\(timeframe)|\(direction.rawValue)|\(formationPosition)|\(low)|\(high)" }
}

struct FairValueResult: Codable, Hashable, Sendable {
    let timeframe: String
    let gaps: [FairValueGap]
    let activeGaps: [FairValueGap]
    let structuralFVGCandidates: [FairValueGap]
    let reasonCodes: [String]
}

enum FairValueError: Error, Equatable {
    case noBars
    case invalidBar
    case invalidPivotSpan
}

struct FairValueEngine: Sendable {
    let pivotSpan: Int

    init(pivotSpan: Int = 2) {
        self.pivotSpan = pivotSpan
    }

    func analyze(
        bars: [MarketCandle],
        timeframe: String,
        asOf: Date? = nil
    ) throws -> FairValueResult {
        guard pivotSpan >= 1 else { throw FairValueError.invalidPivotSpan }
        guard !bars.isEmpty else { throw FairValueError.noBars }
        guard bars.allSatisfy(\.isValid) else { throw FairValueError.invalidBar }

        var data = bars.sorted { $0.openTime < $1.openTime }
        if let asOf { data = data.filter { $0.openTime <= asOf } }
        let pivots = StructuralPivotEngine(pivotSpan: pivotSpan).confirmedPivots(data)
        var gaps: [FairValueGap] = []

        guard data.count >= 3 else {
            return FairValueResult(
                timeframe: timeframe,
                gaps: [],
                activeGaps: [],
                structuralFVGCandidates: [],
                reasonCodes: ["PAIRING_IS_PRICE_ACTION_PROXY", "FVG_IS_CONTEXT_NOT_ENTRY_SIGNAL"]
            )
        }

        for position in 2..<data.count {
            let left = data[position - 2]
            let impulse = data[position - 1]
            let right = data[position]
            let direction: MarketDirection
            let low: Double
            let high: Double

            if right.low > left.high && impulse.close > impulse.open {
                direction = .bullish
                low = left.high
                high = right.low
            } else if right.high < left.low && impulse.close < impulse.open {
                direction = .bearish
                low = right.high
                high = left.low
            } else {
                continue
            }

            let source = position - 1
            let refs = direction == .bullish ? pivots.highs : pivots.lows
            let eligible = refs.filter { reference in
                guard reference.confirmedPosition < source else { return false }
                let earlier = data[reference.confirmedPosition..<source].map(\.close)
                let traded = impulse.low <= reference.price && reference.price <= impulse.high
                if direction == .bullish {
                    return traded
                        && impulse.close > reference.price
                        && (earlier.max() ?? -Double.infinity) <= reference.price
                }
                return traded
                    && impulse.close < reference.price
                    && (earlier.min() ?? Double.infinity) >= reference.price
            }
            let reference = eligible.max(by: { $0.sourcePosition < $1.sourcePosition })
            var gap = FairValueGap(
                timeframe: timeframe,
                direction: direction,
                low: low,
                high: high,
                consequentEncroachment: (low + high) / 2,
                sourcePosition: source,
                formationPosition: position,
                formationTime: data[position].openTime,
                sourceLow: impulse.low,
                sourceHigh: impulse.high,
                structuralReference: reference,
                fairValuationPoint: reference?.price,
                reasonCodes: [
                    "THREE_CANDLE_FVG",
                    reference == nil ? "NO_STRUCTURAL_CLOSE_THROUGH" : "STRUCTURAL_CLOSE_THROUGH"
                ]
            )

            if position + 1 < data.count {
                for j in (position + 1)..<data.count {
                    let row = data[j]
                    let touched = row.low <= high && row.high >= low
                    if touched && gap.firstTouchPosition == nil {
                        gap.firstTouchPosition = j
                        gap.firstTouchTime = row.openTime
                        gap.status = .partiallyRebalanced
                    }
                    let filled = direction == .bullish ? row.low <= low : row.high >= high
                    if touched && filled && gap.filledPosition == nil {
                        gap.filledPosition = j
                        gap.status = .rebalanced
                    }
                    if let reference,
                       row.low <= reference.price,
                       reference.price <= row.high,
                       gap.pairingReturnPosition == nil {
                        gap.pairingReturnPosition = j
                        gap.pairingReturnTime = row.openTime
                    }
                    let rejected = direction == .bullish ? row.close > high : row.close < low
                    if gap.pairingReturnPosition != nil,
                       rejected,
                       gap.pairingRejectionPosition == nil {
                        gap.pairingRejectionPosition = j
                    }
                    let invalid = direction == .bullish ? row.close < low : row.close > high
                    if invalid {
                        gap.status = .invalidated
                        gap.invalidatedPosition = j
                        gap.invalidatedTime = row.openTime
                        break
                    }
                }
            }
            gaps.append(gap)
        }

        let active = gaps.filter { $0.status == .open || $0.status == .partiallyRebalanced }
        return FairValueResult(
            timeframe: timeframe,
            gaps: gaps,
            activeGaps: active,
            structuralFVGCandidates: active.filter { $0.structuralReference != nil },
            reasonCodes: ["PAIRING_IS_PRICE_ACTION_PROXY", "FVG_IS_CONTEXT_NOT_ENTRY_SIGNAL"]
        )
    }
}
