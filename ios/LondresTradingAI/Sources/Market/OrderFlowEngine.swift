import Foundation

enum OrderFlowDirection: String, Codable, Hashable, Sendable {
    case bullish = "BULLISH"
    case bearish = "BEARISH"
    case transition = "TRANSITION"
    case unconfirmed = "UNCONFIRMED"

    var directionalControl: DirectionalControl {
        switch self {
        case .bullish: return .bullish
        case .bearish: return .bearish
        case .transition, .unconfirmed: return .unconfirmed
        }
    }
}

enum RangeRole: String, Codable, Hashable, Sendable {
    case support = "SUPPORT"
    case resistance = "RESISTANCE"
}

enum RangeStatus: String, Codable, Hashable, Sendable {
    case candidate = "CANDIDATE"
    case confirmed = "CONFIRMED"
    case invalidated = "INVALIDATED"
}

struct OrderFlowRange: Codable, Hashable, Sendable, Identifiable {
    let direction: OrderFlowDirection
    let role: RangeRole
    let sourcePosition: Int
    let sourceTime: Date
    let low: Double
    let high: Double
    let sourceOpen: Double
    let sourceClose: Double
    var status: RangeStatus = .candidate
    var confirmedPosition: Int?
    var confirmedTime: Date?
    var invalidatedPosition: Int?
    var invalidatedTime: Date?

    var id: String {
        "\(direction.rawValue)|\(sourcePosition)|\(sourceTime.timeIntervalSince1970)|\(low)|\(high)"
    }

    var priceRange: PriceRange { PriceRange(low: low, high: high) }
}

struct OrderFlowResult: Codable, Hashable, Sendable {
    let timeframe: String
    let control: OrderFlowDirection
    let latestEvent: OrderFlowRange?
    let activeSupportRanges: [OrderFlowRange]
    let activeResistanceRanges: [OrderFlowRange]
    let confirmedEvents: [OrderFlowRange]
    let transitionReason: String?
}

struct IOFCResult: Codable, Hashable, Sendable {
    let expectedDirection: MarketDirection
    let confirmed: Bool
    let confirmationRange: OrderFlowRange?
    let reason: String?
}

enum OrderFlowError: Error, Equatable {
    case noBars
    case invalidBar
    case mixedSymbols
    case invalidMaxCandidateRanges
    case invalidAnchorPosition
}

struct OrderFlowEngine: Sendable {
    let maxCandidateRanges: Int

    init(maxCandidateRanges: Int = 200) {
        self.maxCandidateRanges = maxCandidateRanges
    }

    func analyze(bars: [MarketCandle], timeframe: String) throws -> OrderFlowResult {
        guard maxCandidateRanges >= 1 else { throw OrderFlowError.invalidMaxCandidateRanges }
        guard !bars.isEmpty else { throw OrderFlowError.noBars }
        guard bars.allSatisfy(\.isValid) else { throw OrderFlowError.invalidBar }
        guard Set(bars.map(\.symbol)).count == 1 else { throw OrderFlowError.mixedSymbols }

        let data = bars.sorted { $0.openTime < $1.openTime }
        var bullishCandidates: [OrderFlowRange] = []
        var bearishCandidates: [OrderFlowRange] = []
        var confirmedEvents: [OrderFlowRange] = []

        for (position, row) in data.enumerated() {
            let close = row.close

            for index in confirmedEvents.indices where confirmedEvents[index].status == .confirmed {
                let invalidated = confirmedEvents[index].direction == .bullish
                    ? close < confirmedEvents[index].low
                    : close > confirmedEvents[index].high
                if invalidated {
                    confirmedEvents[index].status = .invalidated
                    confirmedEvents[index].invalidatedPosition = position
                    confirmedEvents[index].invalidatedTime = row.openTime
                }
            }

            for index in bullishCandidates.indices {
                if bullishCandidates[index].status == .candidate && close > bullishCandidates[index].high {
                    bullishCandidates[index].status = .confirmed
                    bullishCandidates[index].confirmedPosition = position
                    bullishCandidates[index].confirmedTime = row.openTime
                    confirmedEvents.append(bullishCandidates[index])
                } else if bullishCandidates[index].status == .confirmed && close < bullishCandidates[index].low {
                    bullishCandidates[index].status = .invalidated
                    bullishCandidates[index].invalidatedPosition = position
                    bullishCandidates[index].invalidatedTime = row.openTime
                }
            }

            for index in bearishCandidates.indices {
                if bearishCandidates[index].status == .candidate && close < bearishCandidates[index].low {
                    bearishCandidates[index].status = .confirmed
                    bearishCandidates[index].confirmedPosition = position
                    bearishCandidates[index].confirmedTime = row.openTime
                    confirmedEvents.append(bearishCandidates[index])
                } else if bearishCandidates[index].status == .confirmed && close > bearishCandidates[index].high {
                    bearishCandidates[index].status = .invalidated
                    bearishCandidates[index].invalidatedPosition = position
                    bearishCandidates[index].invalidatedTime = row.openTime
                }
            }

            if close < row.open {
                bullishCandidates.append(
                    OrderFlowRange(
                        direction: .bullish,
                        role: .support,
                        sourcePosition: position,
                        sourceTime: row.openTime,
                        low: row.low,
                        high: row.high,
                        sourceOpen: row.open,
                        sourceClose: close
                    )
                )
                if bullishCandidates.count > maxCandidateRanges {
                    bullishCandidates.removeFirst(bullishCandidates.count - maxCandidateRanges)
                }
            } else if close > row.open {
                bearishCandidates.append(
                    OrderFlowRange(
                        direction: .bearish,
                        role: .resistance,
                        sourcePosition: position,
                        sourceTime: row.openTime,
                        low: row.low,
                        high: row.high,
                        sourceOpen: row.open,
                        sourceClose: close
                    )
                )
                if bearishCandidates.count > maxCandidateRanges {
                    bearishCandidates.removeFirst(bearishCandidates.count - maxCandidateRanges)
                }
            }
        }

        confirmedEvents.sort {
            let left = $0.confirmedPosition ?? -1
            let right = $1.confirmedPosition ?? -1
            return left == right ? $0.sourcePosition < $1.sourcePosition : left < right
        }
        let latestEvent = confirmedEvents.last
        let activeSupport = confirmedEvents.filter {
            $0.status == .confirmed && $0.direction == .bullish
        }
        let activeResistance = confirmedEvents.filter {
            $0.status == .confirmed && $0.direction == .bearish
        }

        var control: OrderFlowDirection = .unconfirmed
        var transitionReason: String?
        if let latestEvent {
            if latestEvent.status == .confirmed {
                control = latestEvent.direction
            } else {
                control = .transition
                transitionReason = "Latest \(latestEvent.direction.rawValue) IOF range was invalidated without a later confirmed opposing IOFC."
            }
        }

        return OrderFlowResult(
            timeframe: timeframe,
            control: control,
            latestEvent: latestEvent,
            activeSupportRanges: activeSupport,
            activeResistanceRanges: activeResistance,
            confirmedEvents: confirmedEvents,
            transitionReason: transitionReason
        )
    }

    func findIOFCAfter(
        bars: [MarketCandle],
        anchorPosition: Int,
        expectedDirection: MarketDirection
    ) throws -> IOFCResult {
        guard !bars.isEmpty else { throw OrderFlowError.noBars }
        guard anchorPosition >= -1 && anchorPosition < bars.count else {
            throw OrderFlowError.invalidAnchorPosition
        }

        let result = try analyze(bars: bars, timeframe: "POST_CSD")
        let expected: OrderFlowDirection = expectedDirection == .bullish ? .bullish : .bearish
        let eligible = result.confirmedEvents.filter {
            $0.sourcePosition > anchorPosition
                && $0.direction == expected
                && $0.status == .confirmed
        }

        if let first = eligible.first, result.control == expected {
            return IOFCResult(
                expectedDirection: expectedDirection,
                confirmed: true,
                confirmationRange: first,
                reason: "BODY_CLOSE_CONFIRMED_POST_CSD_IOFC"
            )
        }

        return IOFCResult(
            expectedDirection: expectedDirection,
            confirmed: false,
            confirmationRange: nil,
            reason: expectedDirection == .bullish
                ? "WAITING_FOR_NEW_DOWN_CLOSE_RANGE_AND_BODY_ACCEPTANCE_ABOVE"
                : "WAITING_FOR_NEW_UP_CLOSE_RANGE_AND_BODY_ACCEPTANCE_BELOW"
        )
    }
}
