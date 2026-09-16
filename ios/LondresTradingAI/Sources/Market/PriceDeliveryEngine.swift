import Foundation

enum DeliveryPhase: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case consolidation = "CONSOLIDATION"
    case expansion = "EXPANSION"
    case transition = "TRANSITION"
    case retracement = "RETRACEMENT"
}

enum DeliverySequence: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case engineer = "ENGINEER"
    case neutralize = "NEUTRALIZE"
    case distribute = "DISTRIBUTE"
    case rebalance = "REBALANCE"
    case redistribute = "REDISTRIBUTE"
    case invalidated = "INVALIDATED"
}

enum DeliveryCycle: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case stopsToImbalance = "STOPS_TO_IMBALANCE"
    case imbalanceToStops = "IMBALANCE_TO_STOPS"
}

enum DeliveryEventKind: String, Codable, Hashable, Sendable {
    case engineer = "ENGINEER"
    case neutralize = "NEUTRALIZE"
    case ambiguousTwoSidedRaid = "AMBIGUOUS_TWO_SIDED_RAID"
    case distribute = "DISTRIBUTE"
    case expansion = "EXPANSION"
    case deliveryArrayNegated = "DELIVERY_ARRAY_NEGATED"
    case rebalance = "REBALANCE"
    case reaccumulation = "REACCUMULATION"
    case redistribution = "REDISTRIBUTION"
}

struct OriginalConsolidation: Codable, Hashable, Sendable {
    let low: Double
    let high: Double
    let overlapLow: Double
    let overlapHigh: Double
    let startPosition: Int
    let endPosition: Int
    var status: String
    var departurePosition: Int?
}

struct DeliveryRaid: Codable, Hashable, Sendable {
    let position: Int
    let reference: CSDPivotReference
}

struct PriceDeliveryEvent: Codable, Hashable, Sendable, Identifiable {
    let position: Int
    let time: Date
    let event: DeliveryEventKind
    let structuralReference: CSDPivotReference?
    let gapFormationPosition: Int?
    var id: String { "\(position)|\(event.rawValue)|\(time.timeIntervalSince1970)" }
}

struct PriceDeliveryResult: Codable, Hashable, Sendable {
    let timeframe: String
    let phase: DeliveryPhase
    let sequence: DeliverySequence
    let direction: DirectionalControl
    let control: OrderFlowDirection
    let cycle: DeliveryCycle
    let originalConsolidation: OriginalConsolidation?
    let events: [PriceDeliveryEvent]
    let expectedNext: DeliverySequence?
    let reasonCodes: [String]
}

enum PriceDeliveryError: Error, Equatable {
    case noBars
    case invalidBar
    case invalidConsolidationWindow
}

struct PriceDeliveryEngine: Sendable {
    let pivotSpan: Int
    let consolidationWindow: Int

    init(pivotSpan: Int = 2, consolidationWindow: Int = 3) {
        self.pivotSpan = pivotSpan
        self.consolidationWindow = consolidationWindow
    }

    func analyze(
        bars: [MarketCandle],
        timeframe: String,
        asOf: Date? = nil
    ) throws -> PriceDeliveryResult {
        guard consolidationWindow >= 2 else { throw PriceDeliveryError.invalidConsolidationWindow }
        guard !bars.isEmpty else { throw PriceDeliveryError.noBars }
        guard bars.allSatisfy(\.isValid) else { throw PriceDeliveryError.invalidBar }

        var data = bars.sorted { $0.openTime < $1.openTime }
        if let asOf { data = data.filter { $0.openTime <= asOf } }
        guard !data.isEmpty else { throw PriceDeliveryError.noBars }

        let valuation = try FairValueEngine(pivotSpan: pivotSpan).analyze(
            bars: data,
            timeframe: timeframe
        )
        let pivots = StructuralPivotEngine(pivotSpan: pivotSpan).confirmedPivots(data)
        let gaps = Dictionary(uniqueKeysWithValues: valuation.gaps.map { ($0.formationPosition, $0) })

        var sequence: DeliverySequence = .unresolved
        var phase: DeliveryPhase = .unresolved
        var direction: DirectionalControl = .unconfirmed
        var cycle: DeliveryCycle = .unresolved
        var events: [PriceDeliveryEvent] = []
        var raids: [DeliveryRaid] = []
        var distribution: FairValueGap?
        var rebalancedAt: Int?
        var originalConsolidation: OriginalConsolidation?
        var consolidations: [OriginalConsolidation] = []
        var consumed = Set<String>()

        func record(
            _ position: Int,
            _ kind: DeliveryEventKind,
            reference: CSDPivotReference? = nil,
            gapFormationPosition: Int? = nil
        ) {
            events.append(
                PriceDeliveryEvent(
                    position: position,
                    time: data[position].openTime,
                    event: kind,
                    structuralReference: reference,
                    gapFormationPosition: gapFormationPosition
                )
            )
        }

        for i in data.indices {
            let row = data[i]

            if i + 1 >= consolidationWindow && distribution == nil {
                let window = Array(data[(i + 1 - consolidationWindow)...i])
                let overlapLow = window.map(\.low).max() ?? row.low
                let overlapHigh = window.map(\.high).min() ?? row.high
                let hasUp = window.contains { $0.close > $0.open }
                let hasDown = window.contains { $0.close < $0.open }
                if overlapLow < overlapHigh && hasUp && hasDown {
                    let candidate = OriginalConsolidation(
                        low: window.map(\.low).min() ?? row.low,
                        high: window.map(\.high).max() ?? row.high,
                        overlapLow: overlapLow,
                        overlapHigh: overlapHigh,
                        startPosition: i + 1 - consolidationWindow,
                        endPosition: i,
                        status: "CANDIDATE",
                        departurePosition: nil
                    )
                    originalConsolidation = candidate
                    consolidations.append(candidate)
                    phase = .consolidation
                }
            }

            var newlyTaken: [CSDPivotReference] = []
            for reference in pivots.highs {
                let key = "HIGH|\(reference.sourcePosition)"
                guard reference.confirmedPosition < i, !consumed.contains(key) else { continue }
                if row.high > reference.price {
                    consumed.insert(key)
                    newlyTaken.append(reference)
                } else if sequence == .unresolved {
                    sequence = .engineer
                    record(i, .engineer, reference: reference)
                }
            }
            for reference in pivots.lows {
                let key = "LOW|\(reference.sourcePosition)"
                guard reference.confirmedPosition < i, !consumed.contains(key) else { continue }
                if row.low < reference.price {
                    consumed.insert(key)
                    newlyTaken.append(reference)
                } else if sequence == .unresolved {
                    sequence = .engineer
                    record(i, .engineer, reference: reference)
                }
            }

            if !newlyTaken.isEmpty {
                let sides = Set(newlyTaken.map(\.side))
                if sides.count == 2 {
                    record(i, .ambiguousTwoSidedRaid)
                    raids.removeAll()
                    cycle = .unresolved
                } else if let reference = newlyTaken.max(by: { $0.sourcePosition < $1.sourcePosition }) {
                    let raid = DeliveryRaid(position: i, reference: reference)
                    raids.append(raid)
                    cycle = .stopsToImbalance
                    if distribution == nil { sequence = .neutralize }
                    record(i, .neutralize, reference: reference)
                }
            }

            if let gap = gaps[i], distribution == nil {
                direction = gap.direction == .bullish ? .bullish : .bearish
                phase = .expansion
                let priorConsolidations = consolidations.filter { $0.endPosition < gap.sourcePosition }
                if var prior = priorConsolidations.last {
                    let sourceClose = data[gap.sourcePosition].close
                    if sourceClose > prior.high || sourceClose < prior.low {
                        prior.status = "DISPLACEMENT_CONFIRMED"
                        prior.departurePosition = i
                        originalConsolidation = prior
                    }
                }

                let desiredSide = gap.direction == .bullish ? "LOW" : "HIGH"
                let priorRaids = raids.filter {
                    $0.position < gap.sourcePosition && $0.reference.side == desiredSide
                }
                if let raid = priorRaids.last {
                    distribution = gap
                    sequence = .distribute
                    record(i, .distribute, reference: raid.reference, gapFormationPosition: i)
                } else {
                    record(i, .expansion, gapFormationPosition: i)
                }
            }

            if let activeDistribution = distribution,
               i > activeDistribution.formationPosition {
                let bullish = activeDistribution.direction == .bullish
                let broken = bullish
                    ? row.close < activeDistribution.low
                    : row.close > activeDistribution.high
                if broken {
                    phase = .transition
                    sequence = .invalidated
                    record(
                        i,
                        .deliveryArrayNegated,
                        gapFormationPosition: activeDistribution.formationPosition
                    )
                    distribution = nil
                    rebalancedAt = nil
                    raids.removeAll()
                    cycle = .unresolved
                    continue
                }

                let touched = row.low <= activeDistribution.high && row.high >= activeDistribution.low
                if touched && rebalancedAt == nil {
                    rebalancedAt = i
                    sequence = .rebalance
                    phase = .retracement
                    cycle = .imbalanceToStops
                    record(i, .rebalance, gapFormationPosition: activeDistribution.formationPosition)
                }

                if let rebalancedAt,
                   i > rebalancedAt,
                   sequence == .rebalance {
                    let continued = bullish
                        ? row.close > activeDistribution.sourceHigh
                        : row.close < activeDistribution.sourceLow
                    if continued {
                        sequence = .redistribute
                        phase = .expansion
                        record(
                            i,
                            bullish ? .reaccumulation : .redistribution,
                            gapFormationPosition: activeDistribution.formationPosition
                        )
                        distribution = nil
                        selfReset(&rebalancedAt, &raids)
                    }
                }
            }
        }

        let flow = try OrderFlowEngine().analyze(bars: data, timeframe: timeframe)
        return PriceDeliveryResult(
            timeframe: timeframe,
            phase: phase,
            sequence: sequence,
            direction: direction,
            control: flow.control,
            cycle: cycle,
            originalConsolidation: originalConsolidation,
            events: events,
            expectedNext: expectedNext(sequence),
            reasonCodes: [
                "CONSOLIDATION_IS_OVERLAP_PROXY",
                "NO_REVERSAL_FROM_ARRAY_TOUCH_ALONE"
            ]
        )
    }

    private func selfReset(_ rebalancedAt: inout Int?, _ raids: inout [DeliveryRaid]) {
        rebalancedAt = nil
        raids.removeAll()
    }

    private func expectedNext(_ sequence: DeliverySequence) -> DeliverySequence? {
        switch sequence {
        case .engineer: return .neutralize
        case .neutralize: return .distribute
        case .distribute: return .rebalance
        case .rebalance: return .redistribute
        default: return nil
        }
    }
}
