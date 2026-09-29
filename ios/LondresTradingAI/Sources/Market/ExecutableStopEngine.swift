import Foundation

enum ExecutableStopStatus: String, Codable, Hashable, Sendable {
    case ready = "READY"
    case waitForEntry = "WAIT_FOR_ENTRY"
    case waitForSelectedStructuralStop = "WAIT_FOR_SELECTED_STRUCTURAL_STOP"
    case waitForTickSize = "WAIT_FOR_TICK_SIZE"
    case waitForBufferPolicy = "WAIT_FOR_BUFFER_POLICY"
    case invalidTickSize = "INVALID_TICK_SIZE"
    case invalidBufferPolicy = "INVALID_BUFFER_POLICY"
    case invalidDirectionalGeometry = "INVALID_DIRECTIONAL_GEOMETRY"
}

struct ExecutableStopResult: Codable, Hashable, Sendable {
    let status: ExecutableStopStatus
    let direction: MarketDirection?
    let entryPrice: Double?
    let selectedStopSource: StopSource?
    let structuralAnchorPrice: Double?
    let placement: String?
    let tickSize: Double?
    let bufferTicks: Int?
    let bufferPrice: Double?
    let executableStopPrice: Double?
    let stopDistancePrice: Double?
    let stopDistanceTicks: Int?
    let reasonCodes: [String]

    var riskSizingReady: Bool { status == .ready }
    var orderAuthorized: Bool { false }
    var bufferPolicyAuthority: String { "EXPLICIT_SYMBOL_STOP_BUFFER_POLICY" }
}

struct ExecutableStopEngine: Sendable {
    func calculate(
        entryExecution: EntryExecutionContext,
        stopSelection: ValidatedStopSelection?,
        tickSize: Double?,
        bufferTicks: Int?
    ) -> ExecutableStopResult {
        let direction = entryExecution.direction
        guard entryExecution.status == .entryTriggered,
              let entry = entryExecution.exactEntryPrice else {
            return wait(.waitForEntry, direction: direction, reason: "EXACT_PHASE13_ENTRY_REQUIRED")
        }
        guard let selection = stopSelection,
              selection.valid,
              let anchor = selection.selectedAnchorPrice else {
            return wait(
                .waitForSelectedStructuralStop,
                direction: direction,
                reason: "HARD_VALIDATED_TRADER_STOP_SELECTION_REQUIRED",
                entryPrice: entry
            )
        }
        guard let tickSize else {
            return wait(
                .waitForTickSize,
                direction: direction,
                reason: "SYMBOL_TICK_SIZE_REQUIRED",
                entryPrice: entry,
                selection: selection,
                anchor: anchor
            )
        }
        guard tickSize > 0 else {
            return wait(
                .invalidTickSize,
                direction: direction,
                reason: "TICK_SIZE_MUST_BE_POSITIVE",
                entryPrice: entry,
                selection: selection,
                anchor: anchor,
                tickSize: tickSize
            )
        }
        guard let bufferTicks else {
            return wait(
                .waitForBufferPolicy,
                direction: direction,
                reason: "EXPLICIT_STOP_BUFFER_TICKS_REQUIRED",
                entryPrice: entry,
                selection: selection,
                anchor: anchor,
                tickSize: tickSize
            )
        }
        guard bufferTicks >= 1 else {
            return wait(
                .invalidBufferPolicy,
                direction: direction,
                reason: "STOP_BUFFER_MUST_BE_AT_LEAST_ONE_WHOLE_TICK",
                entryPrice: entry,
                selection: selection,
                anchor: anchor,
                tickSize: tickSize,
                bufferTicks: bufferTicks
            )
        }
        guard let direction else {
            return invalidGeometry(
                direction: nil,
                entry: entry,
                selection: selection,
                anchor: anchor,
                tickSize: tickSize,
                bufferTicks: bufferTicks
            )
        }

        let rawStop: Double
        switch direction {
        case .bearish:
            guard anchor > entry else {
                return invalidGeometry(
                    direction: direction,
                    entry: entry,
                    selection: selection,
                    anchor: anchor,
                    tickSize: tickSize,
                    bufferTicks: bufferTicks
                )
            }
            rawStop = anchor + Double(bufferTicks) * tickSize
        case .bullish:
            guard anchor < entry else {
                return invalidGeometry(
                    direction: direction,
                    entry: entry,
                    selection: selection,
                    anchor: anchor,
                    tickSize: tickSize,
                    bufferTicks: bufferTicks
                )
            }
            rawStop = anchor - Double(bufferTicks) * tickSize
        }

        let executableStop = direction == .bearish
            ? ceilToTick(rawStop, tick: tickSize)
            : floorToTick(rawStop, tick: tickSize)
        let distance = abs(entry - executableStop)
        let ticks = max(1, Int(ceil((distance / tickSize) - 1e-12)))

        return ExecutableStopResult(
            status: .ready,
            direction: direction,
            entryPrice: entry,
            selectedStopSource: selection.selectedSource,
            structuralAnchorPrice: anchor,
            placement: selection.placement,
            tickSize: tickSize,
            bufferTicks: bufferTicks,
            bufferPrice: Double(bufferTicks) * tickSize,
            executableStopPrice: executableStop,
            stopDistancePrice: distance,
            stopDistanceTicks: ticks,
            reasonCodes: [
                "STRUCTURAL_STOP_SELECTED_BY_HARD_VALIDATED_TRADER_GATE",
                "EXECUTABLE_STOP_BUFFER_SUPPLIED_EXPLICITLY",
                "STOP_SNAPPED_OUTWARD_TO_SYMBOL_TICK_GRID",
                "RISK_SIZING_MAY_NOW_USE_ENTRY_TO_EXECUTABLE_STOP_RANGE"
            ]
        )
    }

    private func ceilToTick(_ price: Double, tick: Double) -> Double {
        ceil((price / tick) - 1e-12) * tick
    }

    private func floorToTick(_ price: Double, tick: Double) -> Double {
        floor((price / tick) + 1e-12) * tick
    }

    private func invalidGeometry(
        direction: MarketDirection?,
        entry: Double,
        selection: ValidatedStopSelection,
        anchor: Double,
        tickSize: Double,
        bufferTicks: Int
    ) -> ExecutableStopResult {
        ExecutableStopResult(
            status: .invalidDirectionalGeometry,
            direction: direction,
            entryPrice: entry,
            selectedStopSource: selection.selectedSource,
            structuralAnchorPrice: anchor,
            placement: selection.placement,
            tickSize: tickSize,
            bufferTicks: bufferTicks,
            bufferPrice: Double(bufferTicks) * tickSize,
            executableStopPrice: nil,
            stopDistancePrice: nil,
            stopDistanceTicks: nil,
            reasonCodes: ["STRUCTURAL_STOP_ANCHOR_IS_NOT_BEYOND_ENTRY_IN_INVALIDATION_DIRECTION"]
        )
    }

    private func wait(
        _ status: ExecutableStopStatus,
        direction: MarketDirection?,
        reason: String,
        entryPrice: Double? = nil,
        selection: ValidatedStopSelection? = nil,
        anchor: Double? = nil,
        tickSize: Double? = nil,
        bufferTicks: Int? = nil
    ) -> ExecutableStopResult {
        ExecutableStopResult(
            status: status,
            direction: direction,
            entryPrice: entryPrice,
            selectedStopSource: selection?.selectedSource,
            structuralAnchorPrice: anchor,
            placement: selection?.placement,
            tickSize: tickSize,
            bufferTicks: bufferTicks,
            bufferPrice: bufferTicks.flatMap { count in tickSize.map { Double(count) * $0 } },
            executableStopPrice: nil,
            stopDistancePrice: nil,
            stopDistanceTicks: nil,
            reasonCodes: [reason]
        )
    }
}
