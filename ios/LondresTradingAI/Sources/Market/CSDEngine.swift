import Foundation

enum CSDDirection: String, Codable, Hashable, Sendable {
    case bullish = "BULLISH"
    case bearish = "BEARISH"
    case unconfirmed = "UNCONFIRMED"

    var marketDirection: MarketDirection? {
        switch self {
        case .bullish: return .bullish
        case .bearish: return .bearish
        case .unconfirmed: return nil
        }
    }
}

struct CSDPivotReference: Codable, Hashable, Sendable {
    let side: String
    let price: Double
    let sourcePosition: Int
    let sourceTime: Date
    let confirmedPosition: Int
    let confirmedTime: Date
}

struct CSDEvent: Codable, Hashable, Sendable, Identifiable {
    let direction: CSDDirection
    let timeframe: String
    let liquiditySide: String
    let liquidityReference: CSDPivotReference
    let raidPosition: Int
    let raidTime: Date
    let thresholdPosition: Int
    let thresholdTime: Date
    let thresholdOpen: Double
    let confirmationPosition: Int
    let confirmationTime: Date
    let confirmationClose: Double
    let protectedExtreme: Double
    let reasonCodes: [String]

    var id: String {
        "\(timeframe)|\(direction.rawValue)|\(confirmationPosition)|\(confirmationTime.timeIntervalSince1970)"
    }
}

struct CSDResult: Codable, Hashable, Sendable {
    let timeframe: String
    let confirmed: Bool
    let direction: CSDDirection
    let latestEvent: CSDEvent?
    let events: [CSDEvent]
    let reasonCodes: [String]

    func latestEvent(atOrAfter referenceTime: Date) -> CSDEvent? {
        events.filter { $0.confirmationTime >= referenceTime }.last
    }
}

enum CSDError: Error, Equatable {
    case noBars
    case invalidBar
    case mixedSymbols
    case invalidPivotSpan
}

private struct PendingCSD {
    let direction: CSDDirection
    let liquiditySide: String
    let liquidityReference: CSDPivotReference
    let raidPosition: Int
    let raidTime: Date
    var thresholdPosition: Int
    var thresholdTime: Date
    var thresholdOpen: Double
    var protectedExtreme: Double
}

struct CSDEngine: Sendable {
    let pivotSpan: Int

    init(pivotSpan: Int = 2) {
        self.pivotSpan = pivotSpan
    }

    func analyze(
        bars: [MarketCandle],
        timeframe: String,
        asOf: Date? = nil
    ) throws -> CSDResult {
        guard pivotSpan >= 1 else { throw CSDError.invalidPivotSpan }
        guard !bars.isEmpty else { throw CSDError.noBars }
        guard bars.allSatisfy(\.isValid) else { throw CSDError.invalidBar }
        guard Set(bars.map(\.symbol)).count == 1 else { throw CSDError.mixedSymbols }

        var data = bars.sorted { $0.openTime < $1.openTime }
        if let asOf {
            data = data.filter { $0.openTime <= asOf }
        }
        if data.count < 2 * pivotSpan + 2 {
            return CSDResult(
                timeframe: timeframe,
                confirmed: false,
                direction: .unconfirmed,
                latestEvent: nil,
                events: [],
                reasonCodes: ["INSUFFICIENT_STRUCTURAL_DATA"]
            )
        }

        let pivots = confirmedPivots(data)
        var pending: [PendingCSD] = []
        var events: [CSDEvent] = []
        var ambiguousRaidSeen = false
        let start = 2 * pivotSpan + 1

        for position in start..<data.count {
            let highRef = latestReference(pivots.highs, beforePosition: position)
            let lowRef = latestReference(pivots.lows, beforePosition: position)
            let buySideRaid = highRef.map { firstHighTake(data, position: position, reference: $0) } ?? false
            let sellSideRaid = lowRef.map { firstLowTake(data, position: position, reference: $0) } ?? false

            if buySideRaid && sellSideRaid {
                ambiguousRaidSeen = true
            } else {
                if sellSideRaid, let lowRef,
                   let candidate = newBullishCandidate(
                       data,
                       position: position,
                       lowRef: lowRef,
                       latestHighRef: highRef
                   ) {
                    pending.append(candidate)
                }
                if buySideRaid, let highRef,
                   let candidate = newBearishCandidate(
                       data,
                       position: position,
                       highRef: highRef,
                       latestLowRef: lowRef
                   ) {
                    pending.append(candidate)
                }
            }

            let row = data[position]
            var survivors: [PendingCSD] = []
            for var candidate in pending {
                let confirmed: Bool
                if candidate.direction == .bullish {
                    candidate.protectedExtreme = min(candidate.protectedExtreme, row.low)
                    if row.close < row.open && row.open > candidate.thresholdOpen {
                        candidate.thresholdOpen = row.open
                        candidate.thresholdPosition = position
                        candidate.thresholdTime = row.openTime
                    }
                    confirmed = row.close > candidate.thresholdOpen
                } else {
                    candidate.protectedExtreme = max(candidate.protectedExtreme, row.high)
                    if row.close > row.open && row.open < candidate.thresholdOpen {
                        candidate.thresholdOpen = row.open
                        candidate.thresholdPosition = position
                        candidate.thresholdTime = row.openTime
                    }
                    confirmed = row.close < candidate.thresholdOpen
                }

                guard confirmed else {
                    survivors.append(candidate)
                    continue
                }

                events.append(
                    CSDEvent(
                        direction: candidate.direction,
                        timeframe: timeframe,
                        liquiditySide: candidate.liquiditySide,
                        liquidityReference: candidate.liquidityReference,
                        raidPosition: candidate.raidPosition,
                        raidTime: candidate.raidTime,
                        thresholdPosition: candidate.thresholdPosition,
                        thresholdTime: candidate.thresholdTime,
                        thresholdOpen: candidate.thresholdOpen,
                        confirmationPosition: position,
                        confirmationTime: row.openTime,
                        confirmationClose: row.close,
                        protectedExtreme: candidate.protectedExtreme,
                        reasonCodes: [
                            "\(candidate.liquiditySide)_LIQUIDITY_RAID",
                            "OPPOSING_CLOSE_CANDLE_OPEN_RECLAIM",
                            "BODY_CLOSE_CSD_CONFIRMATION",
                            candidate.direction == .bullish
                                ? "PROTECTED_LOW_CONFIRMED"
                                : "PROTECTED_HIGH_CONFIRMED"
                        ]
                    )
                )
            }
            pending = survivors
        }

        events.sort { $0.confirmationPosition < $1.confirmationPosition }
        let latest = events.last
        var reasons = ["CSD_EVENTS_\(events.count)"]
        if ambiguousRaidSeen {
            reasons.append("AMBIGUOUS_TWO_SIDED_LIQUIDITY_RAID_IGNORED")
        }
        if let latest {
            reasons.append("LATEST_CSD_\(latest.direction.rawValue)")
            reasons.append("CSD_REQUIRES_BODY_CLOSE_NOT_WICK")
        } else {
            reasons.append("NO_CONFIRMED_CSD")
        }

        return CSDResult(
            timeframe: timeframe,
            confirmed: latest != nil,
            direction: latest?.direction ?? .unconfirmed,
            latestEvent: latest,
            events: events,
            reasonCodes: reasons
        )
    }

    private func confirmedPivots(_ data: [MarketCandle]) -> (highs: [CSDPivotReference], lows: [CSDPivotReference]) {
        guard data.count >= 2 * pivotSpan + 1 else { return ([], []) }
        var highs: [CSDPivotReference] = []
        var lows: [CSDPivotReference] = []

        for position in pivotSpan..<(data.count - pivotSpan) {
            let row = data[position]
            let left = data[(position - pivotSpan)..<position]
            let right = data[(position + 1)...(position + pivotSpan)]
            let confirmationPosition = position + pivotSpan

            if row.high > left.map(\.high).max()! && row.high > right.map(\.high).max()! {
                highs.append(
                    CSDPivotReference(
                        side: "HIGH",
                        price: row.high,
                        sourcePosition: position,
                        sourceTime: row.openTime,
                        confirmedPosition: confirmationPosition,
                        confirmedTime: data[confirmationPosition].openTime
                    )
                )
            }
            if row.low < left.map(\.low).min()! && row.low < right.map(\.low).min()! {
                lows.append(
                    CSDPivotReference(
                        side: "LOW",
                        price: row.low,
                        sourcePosition: position,
                        sourceTime: row.openTime,
                        confirmedPosition: confirmationPosition,
                        confirmedTime: data[confirmationPosition].openTime
                    )
                )
            }
        }
        return (highs, lows)
    }

    private func newBullishCandidate(
        _ data: [MarketCandle],
        position: Int,
        lowRef: CSDPivotReference,
        latestHighRef: CSDPivotReference?
    ) -> PendingCSD? {
        let start = max(0, latestHighRef?.sourcePosition ?? lowRef.confirmedPosition)
        let opposing = (start...position).filter { data[$0].close < data[$0].open }
        guard let thresholdPosition = opposing.max(by: {
            if data[$0].open == data[$1].open { return $0 > $1 }
            return data[$0].open < data[$1].open
        }) else { return nil }
        return PendingCSD(
            direction: .bullish,
            liquiditySide: "SELL_SIDE",
            liquidityReference: lowRef,
            raidPosition: position,
            raidTime: data[position].openTime,
            thresholdPosition: thresholdPosition,
            thresholdTime: data[thresholdPosition].openTime,
            thresholdOpen: data[thresholdPosition].open,
            protectedExtreme: data[position].low
        )
    }

    private func newBearishCandidate(
        _ data: [MarketCandle],
        position: Int,
        highRef: CSDPivotReference,
        latestLowRef: CSDPivotReference?
    ) -> PendingCSD? {
        let start = max(0, latestLowRef?.sourcePosition ?? highRef.confirmedPosition)
        let opposing = (start...position).filter { data[$0].close > data[$0].open }
        guard let thresholdPosition = opposing.min(by: {
            if data[$0].open == data[$1].open { return $0 < $1 }
            return data[$0].open < data[$1].open
        }) else { return nil }
        return PendingCSD(
            direction: .bearish,
            liquiditySide: "BUY_SIDE",
            liquidityReference: highRef,
            raidPosition: position,
            raidTime: data[position].openTime,
            thresholdPosition: thresholdPosition,
            thresholdTime: data[thresholdPosition].openTime,
            thresholdOpen: data[thresholdPosition].open,
            protectedExtreme: data[position].high
        )
    }

    private func latestReference(
        _ references: [CSDPivotReference],
        beforePosition: Int
    ) -> CSDPivotReference? {
        references.last(where: { $0.confirmedPosition < beforePosition })
    }

    private func firstHighTake(
        _ data: [MarketCandle],
        position: Int,
        reference: CSDPivotReference
    ) -> Bool {
        guard data[position].high > reference.price else { return false }
        guard reference.confirmedPosition < position else { return true }
        let previous = data[reference.confirmedPosition..<position]
        return previous.map(\.high).max().map { $0 <= reference.price } ?? true
    }

    private func firstLowTake(
        _ data: [MarketCandle],
        position: Int,
        reference: CSDPivotReference
    ) -> Bool {
        guard data[position].low < reference.price else { return false }
        guard reference.confirmedPosition < position else { return true }
        let previous = data[reference.confirmedPosition..<position]
        return previous.map(\.low).min().map { $0 >= reference.price } ?? true
    }
}
