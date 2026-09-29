import Foundation

enum EntryExecutionStatus: String, Codable, Hashable, Sendable {
    case waitForValidatedIOFRange = "WAIT_FOR_VALIDATED_IOF_RANGE"
    case waitForRetrace = "WAIT_FOR_RETRACE"
    case entryTriggered = "ENTRY_TRIGGERED"
    case rangeSkippedByGap = "RANGE_SKIPPED_BY_GAP"
}

struct EntryZone: Codable, Hashable, Sendable {
    let low: Double
    let high: Double
    let direction: MarketDirection
    let sourcePosition: Int
    let confirmedPosition: Int
    let sourceTime: Date
    let confirmedTime: Date?
}

struct EntryEvent: Codable, Hashable, Sendable {
    let price: Double
    let position: Int
    let time: Date
    let fillBasis: String
    let zoneLow: Double
    let zoneHigh: Double
}

struct EntryExecutionContext: Codable, Hashable, Sendable {
    let status: EntryExecutionStatus
    let direction: MarketDirection?
    let zone: EntryZone?
    let entry: EntryEvent?
    let reasonCodes: [String]

    var exactEntryPrice: Double? { entry?.price }
    var entryTriggered: Bool { entry != nil }
    var entryRule: String { "FIRST_RETURN_INTO_CONFIRMED_POST_CSD_IOF_RANGE" }
    var orderAuthorized: Bool { false }
}

enum EntryExecutionError: Error, Equatable {
    case noBars
    case invalidBar
    case invalidRange
}

struct PostCSDIOFEntryEngine: Sendable {
    func analyze(
        iofc: IOFCResult,
        executionBars: [MarketCandle],
        executionDirection: MarketDirection? = nil,
        asOf: Date? = nil
    ) throws -> EntryExecutionContext {
        guard !executionBars.isEmpty else { throw EntryExecutionError.noBars }
        guard executionBars.allSatisfy(\.isValid) else { throw EntryExecutionError.invalidBar }

        let data = executionBars
            .filter { asOf == nil || $0.openTime <= asOf! }
            .sorted { $0.openTime < $1.openTime }
        guard !data.isEmpty else { throw EntryExecutionError.noBars }

        let direction = executionDirection ?? iofc.expectedDirection
        guard iofc.confirmed, let confirmationRange = iofc.confirmationRange else {
            return EntryExecutionContext(
                status: .waitForValidatedIOFRange,
                direction: direction,
                zone: nil,
                entry: nil,
                reasonCodes: ["CONFIRMED_POST_CSD_IOF_RANGE_REQUIRED"]
            )
        }

        guard confirmationRange.low < confirmationRange.high else {
            throw EntryExecutionError.invalidRange
        }
        let zone = EntryZone(
            low: confirmationRange.low,
            high: confirmationRange.high,
            direction: direction,
            sourcePosition: confirmationRange.sourcePosition,
            confirmedPosition: confirmationRange.confirmedPosition ?? Int.max,
            sourceTime: confirmationRange.sourceTime,
            confirmedTime: confirmationRange.confirmedTime
        )

        guard zone.confirmedPosition < data.count else {
            return EntryExecutionContext(
                status: .waitForRetrace,
                direction: direction,
                zone: zone,
                entry: nil,
                reasonCodes: ["WAIT_FOR_BARS_AFTER_IOFC_CONFIRMATION"]
            )
        }

        let start = zone.confirmedPosition + 1
        guard start < data.count else {
            return EntryExecutionContext(
                status: .waitForRetrace,
                direction: direction,
                zone: zone,
                entry: nil,
                reasonCodes: ["WAIT_FOR_PRICE_RETURN_INTO_POST_CSD_IOF_RANGE"]
            )
        }

        for position in start..<data.count {
            let row = data[position]
            let price: Double
            let basis: String

            if direction == .bearish {
                if row.open > zone.high {
                    return EntryExecutionContext(
                        status: .rangeSkippedByGap,
                        direction: direction,
                        zone: zone,
                        entry: nil,
                        reasonCodes: ["BEARISH_IOF_RANGE_GAPPED_THROUGH_WITHOUT_CAUSAL_FILL"]
                    )
                }
                if zone.low <= row.open && row.open <= zone.high {
                    price = row.open
                    basis = "BAR_OPEN_ALREADY_INSIDE_IOF_RANGE"
                } else if row.open < zone.low && row.high >= zone.low {
                    price = zone.low
                    basis = "FIRST_TOUCH_OF_BEARISH_IOF_RANGE_LOW"
                } else {
                    continue
                }
            } else {
                if row.open < zone.low {
                    return EntryExecutionContext(
                        status: .rangeSkippedByGap,
                        direction: direction,
                        zone: zone,
                        entry: nil,
                        reasonCodes: ["BULLISH_IOF_RANGE_GAPPED_THROUGH_WITHOUT_CAUSAL_FILL"]
                    )
                }
                if zone.low <= row.open && row.open <= zone.high {
                    price = row.open
                    basis = "BAR_OPEN_ALREADY_INSIDE_IOF_RANGE"
                } else if row.open > zone.high && row.low <= zone.high {
                    price = zone.high
                    basis = "FIRST_TOUCH_OF_BULLISH_IOF_RANGE_HIGH"
                } else {
                    continue
                }
            }

            let entry = EntryEvent(
                price: price,
                position: position,
                time: row.openTime,
                fillBasis: basis,
                zoneLow: zone.low,
                zoneHigh: zone.high
            )
            return EntryExecutionContext(
                status: .entryTriggered,
                direction: direction,
                zone: zone,
                entry: entry,
                reasonCodes: [
                    "SMT_CSD_IOF_SEQUENCE_CONFIRMED",
                    "PRICE_RETURNED_TO_POST_CSD_IOF_RANGE",
                    "EXACT_ENTRY_PRICE_FROZEN_AT_FIRST_CAUSAL_RANGE_RETURN"
                ]
            )
        }

        return EntryExecutionContext(
            status: .waitForRetrace,
            direction: direction,
            zone: zone,
            entry: nil,
            reasonCodes: ["WAIT_FOR_PRICE_RETURN_INTO_POST_CSD_IOF_RANGE"]
        )
    }
}
