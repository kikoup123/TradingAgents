import Foundation

enum LiquidityRunClass: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case lrlr = "LRLR"
    case hrlr = "HRLR"
}

struct LiquidityRunClassification: Codable, Hashable, Sendable {
    let classification: LiquidityRunClass
    let targetScope: String
    let reasonCodes: [String]
}

enum NarrativeMatrixKind: String, Codable, Hashable, Sendable {
    case iofRange = "IOF_RANGE"
    case fvg = "FVG"
}

struct NarrativeMatrix: Codable, Hashable, Sendable, Identifiable {
    let kind: NarrativeMatrixKind
    let low: Double
    let high: Double
    let availableTime: Date
    let timeframe: String
    let direction: MarketDirection
    let reached: Bool
    let reachedTime: Date?
    let distance: Double

    var id: String {
        "\(timeframe)|\(kind.rawValue)|\(direction.rawValue)|\(availableTime.timeIntervalSince1970)|\(low)|\(high)"
    }
}

enum NarrativeProfile: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case atParentMatrixWaitForShift = "AT_PARENT_MATRIX_WAIT_FOR_SHIFT"
    case retracement = "RETRACEMENT"
    case continuation = "CONTINUATION"
    case reversalConfirmed = "REVERSAL_CONFIRMED"
}

enum NarrativeDrawKind: String, Codable, Hashable, Sendable {
    case irlFVG = "IRL_FVG"
    case parentMatrixBoundary = "PARENT_MATRIX_BOUNDARY"
    case liquidityObjective = "LIQUIDITY_OBJECTIVE"
}

struct NarrativeDraw: Codable, Hashable, Sendable {
    let kind: NarrativeDrawKind
    let low: Double?
    let high: Double?
    let price: Double?
    let timeframe: String
    let purpose: String
    let sourceKind: String?
}

struct NarrativeReversal: Codable, Hashable, Sendable {
    let confirmed: Bool
    let csd: CSDEvent?
    let postCSDIOFC: IOFCResult?
    let matrix: NarrativeMatrix?
}

struct PreviousCandleDraw: Codable, Hashable, Sendable {
    let price: Double?
    let sourceTime: Date?
    let side: String?
    let reached: Bool?
}

struct NarrativeTimeframeState: Codable, Hashable, Sendable {
    let timeframe: String
    let asOf: Date
    let orderFlow: OrderFlowResult
    let control: OrderFlowDirection
    let curve: String
    let previousCandleDraw: PreviousCandleDraw
    let expectedDelivery: String
    let liquidity: LiquidityResult
    let fairValue: FairValueResult
    let priceDelivery: PriceDeliveryResult
    var parentTimeframe: String?
    var parentControl: OrderFlowDirection?
    var parentMatrices: [NarrativeMatrix]
    var liquidityRun: LiquidityRunClassification
    var parentRelativeRun: LiquidityRunClassification
    var profile: NarrativeProfile
    var reversal: NarrativeReversal
    var narrativeDraw: NarrativeDraw?
    var reasonCodes: [String]
}

struct NarrativeResult: Codable, Hashable, Sendable {
    let hierarchy: [String]
    let missingHigherTimeframes: [String]
    let bias: OrderFlowDirection
    let biasTimeframe: String
    let executionTimeframe: String
    let alignment: String
    let contextConfirmed: Bool
    let timeframes: [String: NarrativeTimeframeState]
    let reasonCodes: [String]
}

enum NarrativeError: Error, Equatable {
    case duplicateHierarchy
    case emptyInput
    case timeframeOutsideHierarchy(String)
    case emptyBars(String)
}

struct NarrativeEngine: Sendable {
    static let defaultHierarchy = ["6M", "3M", "1M", "1W", "1D", "4H", "1H", "15m", "5m", "1m"]

    let pivotSpan: Int

    init(pivotSpan: Int = 2) {
        self.pivotSpan = pivotSpan
    }

    func analyze(
        timeframeBars: [String: [MarketCandle]],
        hierarchy: [String] = Self.defaultHierarchy,
        asOf: Date? = nil
    ) throws -> NarrativeResult {
        guard Set(hierarchy).count == hierarchy.count else { throw NarrativeError.duplicateHierarchy }
        guard !timeframeBars.isEmpty else { throw NarrativeError.emptyInput }
        for key in timeframeBars.keys where !hierarchy.contains(key) {
            throw NarrativeError.timeframeOutsideHierarchy(key)
        }

        let ordered = hierarchy.filter { timeframeBars[$0] != nil }
        guard !ordered.isEmpty else { throw NarrativeError.emptyInput }
        var data: [String: [MarketCandle]] = [:]
        for timeframe in ordered {
            var bars = timeframeBars[timeframe]!.sorted { $0.openTime < $1.openTime }
            if let asOf { bars = bars.filter { $0.openTime <= asOf } }
            guard !bars.isEmpty else { throw NarrativeError.emptyBars(timeframe) }
            data[timeframe] = bars
        }

        var states: [String: NarrativeTimeframeState] = [:]
        for timeframe in ordered {
            let bars = data[timeframe]!
            let flow = try OrderFlowEngine().analyze(bars: bars, timeframe: timeframe)
            let liquidity = try LiquidityEngine(pivotSpan: pivotSpan).analyze(
                bars: bars,
                timeframe: timeframe,
                orderFlowControl: flow.control.directionalControl
            )
            let fairValue = try FairValueEngine(pivotSpan: pivotSpan).analyze(
                bars: bars,
                timeframe: timeframe
            )
            let delivery = try PriceDeliveryEngine(pivotSpan: pivotSpan).analyze(
                bars: bars,
                timeframe: timeframe
            )
            let current = bars[bars.count - 1]
            let previous = bars.count >= 2 ? bars[bars.count - 2] : nil
            let bullish = flow.control == .bullish
            let directional = flow.control == .bullish || flow.control == .bearish
            let target = directional ? (bullish ? previous?.high : previous?.low) : nil
            let reached: Bool? = target.map { bullish ? current.high >= $0 : current.low <= $0 }

            states[timeframe] = NarrativeTimeframeState(
                timeframe: timeframe,
                asOf: current.openTime,
                orderFlow: flow,
                control: flow.control,
                curve: bullish ? "BUY_SIDE_CURVE" : flow.control == .bearish ? "SELL_SIDE_CURVE" : "UNRESOLVED",
                previousCandleDraw: PreviousCandleDraw(
                    price: target,
                    sourceTime: previous?.openTime,
                    side: target == nil ? nil : bullish ? "HIGH" : "LOW",
                    reached: reached
                ),
                expectedDelivery: bullish ? "OLHC" : flow.control == .bearish ? "OHLC" : "UNRESOLVED",
                liquidity: liquidity,
                fairValue: fairValue,
                priceDelivery: delivery,
                parentTimeframe: nil,
                parentControl: nil,
                parentMatrices: [],
                liquidityRun: unresolvedRun(),
                parentRelativeRun: unresolvedRun(),
                profile: .unresolved,
                reversal: NarrativeReversal(confirmed: false, csd: nil, postCSDIOFC: nil, matrix: nil),
                narrativeDraw: nil,
                reasonCodes: []
            )
        }

        for (index, timeframe) in ordered.enumerated() {
            guard var state = states[timeframe], let bars = data[timeframe] else { continue }
            let direction = state.control
            let parent = index > 0 ? states[ordered[index - 1]] : nil
            state.parentTimeframe = parent?.timeframe
            state.parentControl = parent?.control

            var matrices: [NarrativeMatrix] = []
            if let localDirection = marketDirection(direction) {
                let desired = opposite(localDirection)
                for ancestorTF in ordered.prefix(index) {
                    guard let ancestor = states[ancestorTF] else { continue }
                    let arrays = matrixArrays(
                        state: ancestor,
                        direction: desired,
                        includeInvalidatedFVG: false
                    )
                    for array in arrays {
                        let subsequent = bars.filter { $0.openTime > array.availableTime }
                        let firstTouch = subsequent.first {
                            $0.low <= array.high && $0.high >= array.low
                        }
                        let price = bars[bars.count - 1].close
                        let ahead = localDirection == .bullish
                            ? array.high >= price
                            : array.low <= price
                        guard ahead || firstTouch != nil else { continue }
                        matrices.append(
                            NarrativeMatrix(
                                kind: array.kind,
                                low: array.low,
                                high: array.high,
                                availableTime: array.availableTime,
                                timeframe: ancestorTF,
                                direction: desired,
                                reached: firstTouch != nil,
                                reachedTime: firstTouch?.openTime,
                                distance: max(array.low - price, price - array.high, 0)
                            )
                        )
                    }
                }
            }
            state.parentMatrices = matrices
            let touched = matrices.filter(\.reached)
            let currentPrice = bars[bars.count - 1].close
            let crosses = state.liquidity.activeDraw.map { draw in
                matrices.contains {
                    min(currentPrice, draw.price) <= $0.high && max(currentPrice, draw.price) >= $0.low
                }
            } ?? false
            state.liquidityRun = classifyLiquidityRun(
                direction: direction,
                control: direction,
                opposingMatrixReached: !touched.isEmpty,
                crossesParentMatrix: crosses
            )
            state.parentRelativeRun = classifyLiquidityRun(
                direction: direction,
                control: parent?.control ?? .unconfirmed
            )

            if marketDirection(direction) == nil || (parent != nil && marketDirection(parent!.control) == nil) {
                state.profile = .unresolved
                state.reasonCodes.append("WAIT_FOR_DIRECTIONAL_CONTROL")
            } else if !touched.isEmpty {
                state.profile = .atParentMatrixWaitForShift
                state.reasonCodes.append("MATRIX_TOUCH_IS_NOT_REVERSAL")
            } else if let parent, parent.control != direction {
                state.profile = .retracement
                state.reasonCodes.append("LOCAL_COUNTER_FLOW_DOES_NOT_INVALIDATE_PARENT")
            } else {
                state.profile = .continuation
            }

            state.reversal = try reversal(
                bars: bars,
                timeframe: timeframe,
                states: states,
                ancestors: Array(ordered.prefix(index)),
                direction: direction
            )
            if state.reversal.confirmed { state.profile = .reversalConfirmed }
            state.narrativeDraw = narrativeDraw(state: state, currentPrice: currentPrice)
            states[timeframe] = state
        }

        let highest = states[ordered[0]]!
        let lowest = states[ordered[ordered.count - 1]]!
        let directionalBias = marketDirection(highest.control) != nil
        let aligned = directionalBias && ordered.allSatisfy { states[$0]?.control == highest.control }
        let ready = ordered.count >= 2
            && aligned
            && lowest.liquidityRun.classification == .lrlr
            && lowest.narrativeDraw != nil
        let firstIndex = hierarchy.firstIndex(of: ordered[0]) ?? 0

        return NarrativeResult(
            hierarchy: ordered,
            missingHigherTimeframes: Array(hierarchy.prefix(firstIndex)),
            bias: highest.control,
            biasTimeframe: ordered[0],
            executionTimeframe: ordered[ordered.count - 1],
            alignment: aligned ? "ALIGNED" : "MIXED_OR_UNRESOLVED",
            contextConfirmed: ready,
            timeframes: states,
            reasonCodes: ["HTF_WHAT_LTF_WHEN", "EXPECTATIONS_ARE_NOT_OBSERVED_DELIVERY"]
        )
    }

    func classifyLiquidityRun(
        direction: OrderFlowDirection,
        control: OrderFlowDirection,
        opposingMatrixReached: Bool = false,
        crossesParentMatrix: Bool = false
    ) -> LiquidityRunClassification {
        guard marketDirection(direction) != nil, marketDirection(control) != nil else {
            return unresolvedRun()
        }
        if direction != control || opposingMatrixReached || crossesParentMatrix {
            return LiquidityRunClassification(
                classification: .hrlr,
                targetScope: "IRL_OR_PARENT_MATRIX",
                reasonCodes: ["AGAINST_CONTROL_OR_PROTECTED_PARENT_ARRAY"]
            )
        }
        return LiquidityRunClassification(
            classification: .lrlr,
            targetScope: "ERL",
            reasonCodes: ["WITH_CONFIRMED_IOF_WITHIN_PARENT_BOUNDARY"]
        )
    }

    private struct MatrixArray {
        let kind: NarrativeMatrixKind
        let low: Double
        let high: Double
        let availableTime: Date
    }

    private func matrixArrays(
        state: NarrativeTimeframeState,
        direction: MarketDirection,
        includeInvalidatedFVG: Bool
    ) -> [MatrixArray] {
        let orderDirection: OrderFlowDirection = direction == .bullish ? .bullish : .bearish
        var arrays: [MatrixArray] = []
        for range in state.orderFlow.activeSupportRanges + state.orderFlow.activeResistanceRanges
            where range.direction == orderDirection {
            guard let confirmedTime = range.confirmedTime else { continue }
            arrays.append(MatrixArray(kind: .iofRange, low: range.low, high: range.high, availableTime: confirmedTime))
        }
        for gap in state.fairValue.gaps
            where gap.direction == direction && (includeInvalidatedFVG || gap.status != .invalidated) {
            arrays.append(MatrixArray(kind: .fvg, low: gap.low, high: gap.high, availableTime: gap.formationTime))
        }
        return arrays
    }

    private func reversal(
        bars: [MarketCandle],
        timeframe: String,
        states: [String: NarrativeTimeframeState],
        ancestors: [String],
        direction: OrderFlowDirection
    ) throws -> NarrativeReversal {
        guard let expected = marketDirection(direction) else {
            return NarrativeReversal(confirmed: false, csd: nil, postCSDIOFC: nil, matrix: nil)
        }
        let csdResult = try CSDEngine(pivotSpan: pivotSpan).analyze(bars: bars, timeframe: timeframe)
        guard let csd = csdResult.latestEvent,
              csd.direction.marketDirection == expected else {
            return NarrativeReversal(confirmed: false, csd: nil, postCSDIOFC: nil, matrix: nil)
        }
        let after = bars.enumerated().filter { $0.offset > csd.confirmationPosition }.map(\.element)
        let broken = expected == .bullish
            ? after.contains { $0.close < csd.protectedExtreme }
            : after.contains { $0.close > csd.protectedExtreme }
        guard !broken else {
            return NarrativeReversal(confirmed: false, csd: nil, postCSDIOFC: nil, matrix: nil)
        }
        let iofc = try OrderFlowEngine().findIOFCAfter(
            bars: bars,
            anchorPosition: csd.confirmationPosition,
            expectedDirection: expected
        )
        guard iofc.confirmed else {
            return NarrativeReversal(confirmed: false, csd: nil, postCSDIOFC: nil, matrix: nil)
        }

        let raidTime = csd.raidTime
        for ancestorTF in ancestors.reversed() {
            guard let parent = states[ancestorTF], marketDirection(parent.control) == expected else { continue }
            for array in matrixArrays(state: parent, direction: expected, includeInvalidatedFVG: true) {
                guard array.availableTime < raidTime else { continue }
                let raid = bars[csd.raidPosition]
                guard raid.low <= array.high && raid.high >= array.low else { continue }
                let matrix = NarrativeMatrix(
                    kind: array.kind,
                    low: array.low,
                    high: array.high,
                    availableTime: array.availableTime,
                    timeframe: ancestorTF,
                    direction: expected,
                    reached: true,
                    reachedTime: raidTime,
                    distance: 0
                )
                return NarrativeReversal(confirmed: true, csd: csd, postCSDIOFC: iofc, matrix: matrix)
            }
        }
        return NarrativeReversal(confirmed: false, csd: nil, postCSDIOFC: nil, matrix: nil)
    }

    private func narrativeDraw(
        state: NarrativeTimeframeState,
        currentPrice: Double
    ) -> NarrativeDraw? {
        if state.priceDelivery.cycle == .stopsToImbalance,
           let direction = marketDirection(state.control) {
            let candidates = state.fairValue.activeGaps.filter {
                $0.direction == direction && (direction == .bullish ? $0.high < currentPrice : $0.low > currentPrice)
            }
            if let gap = candidates.min(by: {
                min(abs(currentPrice - $0.low), abs(currentPrice - $0.high))
                    < min(abs(currentPrice - $1.low), abs(currentPrice - $1.high))
            }) {
                return NarrativeDraw(
                    kind: .irlFVG,
                    low: gap.low,
                    high: gap.high,
                    price: nil,
                    timeframe: gap.timeframe,
                    purpose: "REBALANCE",
                    sourceKind: "FVG"
                )
            }
            return nil
        }

        if state.liquidityRun.classification == .hrlr {
            let ahead = state.parentMatrices.filter { !$0.reached }
            if let matrix = ahead.min(by: { $0.distance < $1.distance }) {
                return NarrativeDraw(
                    kind: .parentMatrixBoundary,
                    low: matrix.low,
                    high: matrix.high,
                    price: nil,
                    timeframe: matrix.timeframe,
                    purpose: "PARENT_MATRIX_BOUNDARY",
                    sourceKind: matrix.kind.rawValue
                )
            }
            return nil
        }

        if let draw = state.liquidity.activeDraw {
            return NarrativeDraw(
                kind: .liquidityObjective,
                low: nil,
                high: nil,
                price: draw.price,
                timeframe: draw.timeframe,
                purpose: "LIQUIDITY_OBJECTIVE",
                sourceKind: draw.sourceKind
            )
        }
        return nil
    }

    private func marketDirection(_ direction: OrderFlowDirection) -> MarketDirection? {
        switch direction {
        case .bullish: return .bullish
        case .bearish: return .bearish
        case .transition, .unconfirmed: return nil
        }
    }

    private func opposite(_ direction: MarketDirection) -> MarketDirection {
        direction == .bullish ? .bearish : .bullish
    }

    private func unresolvedRun() -> LiquidityRunClassification {
        LiquidityRunClassification(
            classification: .unresolved,
            targetScope: "UNRESOLVED",
            reasonCodes: ["DIRECTIONAL_IOF_REQUIRED"]
        )
    }
}
