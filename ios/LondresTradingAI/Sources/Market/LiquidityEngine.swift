import Foundation

enum DirectionalControl: String, Codable, Hashable, Sendable {
    case bullish = "BULLISH"
    case bearish = "BEARISH"
    case unconfirmed = "UNCONFIRMED"
}

enum LiquiditySide: String, Codable, Hashable, Sendable {
    case buySide = "BUY_SIDE"
    case sellSide = "SELL_SIDE"
}

enum LiquidityClass: String, Codable, Hashable, Sendable {
    case external = "EXTERNAL"
    case `internal` = "INTERNAL"
}

enum LiquidityStatus: String, Codable, Hashable, Sendable {
    case active = "ACTIVE"
    case raidedReclaimed = "RAIDED_RECLAIMED"
    case consumed = "CONSUMED"
}

struct LiquidityPool: Codable, Hashable, Sendable, Identifiable {
    let side: LiquiditySide
    var liquidityClass: LiquidityClass
    let price: Double
    let timeframe: String
    let sourceKind: String
    let sourcePosition: Int
    let sourceTime: Date?
    var status: LiquidityStatus = .active
    var isProtected: Bool = false
    var eventPosition: Int?
    var eventTime: Date?
    var eventClose: Double?

    var id: String {
        "\(side.rawValue)|\(timeframe)|\(sourceKind)|\(sourcePosition)|\(price)"
    }
}

struct LiquidityResult: Codable, Hashable, Sendable {
    let timeframe: String
    let orderFlowControl: DirectionalControl
    let currentPrice: Double
    let buySide: [LiquidityPool]
    let sellSide: [LiquidityPool]
    let protectedPool: LiquidityPool?
    let activeDraw: LiquidityPool?
    let externalHigh: Double?
    let externalLow: Double?
    let dealingRangeEquilibrium: Double?
    let dealingRangeLocation: String
    let reasonCodes: [String]
}

enum LiquidityError: Error, Equatable {
    case noBars
    case invalidBar
    case mixedSymbols
    case invalidPivotSpan
    case invalidTolerance
}

struct LiquidityEngine: Sendable {
    let pivotSpan: Int
    let tolerance: Double

    init(pivotSpan: Int = 2, tolerance: Double = 0) {
        self.pivotSpan = pivotSpan
        self.tolerance = tolerance
    }

    func analyze(
        bars: [MarketCandle],
        timeframe: String,
        orderFlowControl: DirectionalControl = .unconfirmed,
        timePriceState: TimePriceResult? = nil
    ) throws -> LiquidityResult {
        guard pivotSpan >= 1 else { throw LiquidityError.invalidPivotSpan }
        guard tolerance >= 0 else { throw LiquidityError.invalidTolerance }
        guard !bars.isEmpty else { throw LiquidityError.noBars }
        guard bars.allSatisfy(\.isValid) else { throw LiquidityError.invalidBar }
        guard Set(bars.map(\.symbol)).count == 1 else { throw LiquidityError.mixedSymbols }

        let data = bars.sorted { $0.openTime < $1.openTime }
        var pools = structuralPools(data, timeframe: timeframe)
        pools.append(contentsOf: namedSessionPools(timePriceState, timeframe: timeframe))

        for index in pools.indices {
            resolvePoolStatus(data, pool: &pools[index])
        }

        if let protectedIndex = protectedPoolIndex(pools, control: orderFlowControl) {
            pools[protectedIndex].isProtected = true
        }

        let currentPrice = data[data.count - 1].close
        let protected = pools.first(where: \.isProtected)
        let draw = activeDraw(pools, currentPrice: currentPrice, control: orderFlowControl)
        let buySide = pools.filter { $0.side == .buySide }
        let sellSide = pools.filter { $0.side == .sellSide }
        let externalHigh = externalPrice(buySide, high: true)
        let externalLow = externalPrice(sellSide, high: false)
        let location = dealingRangeLocation(
            currentPrice: currentPrice,
            externalHigh: externalHigh,
            externalLow: externalLow
        )

        let structuralCount = pools.filter { $0.sourcePosition >= 0 }.count
        let namedCount = pools.filter { $0.sourcePosition < 0 }.count
        var reasons = [
            "ORDER_FLOW_\(orderFlowControl.rawValue)",
            "STRUCTURAL_POOLS_\(structuralCount)",
            "NAMED_SESSION_POOLS_\(namedCount)"
        ]
        if let protected {
            reasons.append("PROTECTED_\(protected.side.rawValue)_\(protected.sourceKind)")
        }
        if let draw {
            reasons.append("ACTIVE_DRAW_\(draw.side.rawValue)_\(draw.liquidityClass.rawValue)")
        } else {
            reasons.append("ACTIVE_DRAW_UNRESOLVED")
        }

        return LiquidityResult(
            timeframe: timeframe,
            orderFlowControl: orderFlowControl,
            currentPrice: currentPrice,
            buySide: buySide,
            sellSide: sellSide,
            protectedPool: protected,
            activeDraw: draw,
            externalHigh: externalHigh,
            externalLow: externalLow,
            dealingRangeEquilibrium: location.equilibrium,
            dealingRangeLocation: location.location,
            reasonCodes: reasons
        )
    }

    private func structuralPools(_ data: [MarketCandle], timeframe: String) -> [LiquidityPool] {
        guard data.count >= (pivotSpan * 2) + 1 else { return [] }

        var highs: [LiquidityPool] = []
        var lows: [LiquidityPool] = []

        for position in pivotSpan..<(data.count - pivotSpan) {
            let row = data[position]
            let left = data[(position - pivotSpan)..<position]
            let right = data[(position + 1)...(position + pivotSpan)]

            if row.high > left.map(\.high).max()! && row.high > right.map(\.high).max()! {
                highs.append(
                    LiquidityPool(
                        side: .buySide,
                        liquidityClass: .internal,
                        price: row.high,
                        timeframe: timeframe,
                        sourceKind: "SWING_HIGH",
                        sourcePosition: position,
                        sourceTime: row.openTime
                    )
                )
            }

            if row.low < left.map(\.low).min()! && row.low < right.map(\.low).min()! {
                lows.append(
                    LiquidityPool(
                        side: .sellSide,
                        liquidityClass: .internal,
                        price: row.low,
                        timeframe: timeframe,
                        sourceKind: "SWING_LOW",
                        sourcePosition: position,
                        sourceTime: row.openTime
                    )
                )
            }
        }

        if let maxPrice = highs.map(\.price).max(), let index = highs.firstIndex(where: { $0.price == maxPrice }) {
            highs[index].liquidityClass = .external
        }
        if let minPrice = lows.map(\.price).min(), let index = lows.firstIndex(where: { $0.price == minPrice }) {
            lows[index].liquidityClass = .external
        }
        return highs + lows
    }

    private func namedSessionPools(_ state: TimePriceResult?, timeframe: String) -> [LiquidityPool] {
        guard let state else { return [] }
        var pools: [LiquidityPool] = []

        for (key, payload) in state.ons.sorted(by: { $0.key < $1.key }) {
            guard payload.status == .active || payload.status == .complete else { continue }
            let sourceTime = payload.endTime ?? payload.startTime
            if let high = payload.high {
                pools.append(
                    LiquidityPool(
                        side: .buySide,
                        liquidityClass: .external,
                        price: high,
                        timeframe: timeframe,
                        sourceKind: "\(key.uppercased())_HIGH",
                        sourcePosition: -1,
                        sourceTime: sourceTime
                    )
                )
            }
            if let low = payload.low {
                pools.append(
                    LiquidityPool(
                        side: .sellSide,
                        liquidityClass: .external,
                        price: low,
                        timeframe: timeframe,
                        sourceKind: "\(key.uppercased())_LOW",
                        sourcePosition: -1,
                        sourceTime: sourceTime
                    )
                )
            }
        }
        return pools
    }

    private func resolvePoolStatus(_ data: [MarketCandle], pool: inout LiquidityPool) {
        let indexedEvents: [(Int, MarketCandle)]
        if pool.sourcePosition >= 0 {
            let start = pool.sourcePosition + 1
            indexedEvents = start < data.count
                ? Array(data.enumerated().dropFirst(start)).map { ($0.offset, $0.element) }
                : []
        } else if let sourceTime = pool.sourceTime {
            indexedEvents = data.enumerated().compactMap { index, candle in
                candle.openTime >= sourceTime ? (index, candle) : nil
            }
        } else {
            indexedEvents = []
        }

        for (position, row) in indexedEvents {
            switch pool.side {
            case .buySide:
                guard row.high > pool.price + tolerance else { continue }
                pool.status = row.close < pool.price - tolerance ? .raidedReclaimed : .consumed
            case .sellSide:
                guard row.low < pool.price - tolerance else { continue }
                pool.status = row.close > pool.price + tolerance ? .raidedReclaimed : .consumed
            }
            pool.eventPosition = position
            pool.eventTime = row.openTime
            pool.eventClose = row.close
            return
        }
    }

    private func protectedPoolIndex(_ pools: [LiquidityPool], control: DirectionalControl) -> Int? {
        let candidates: [(Int, LiquidityPool)]
        switch control {
        case .bullish:
            candidates = pools.enumerated().filter {
                $0.element.side == .sellSide && $0.element.sourcePosition >= 0 && $0.element.status == .active
            }
        case .bearish:
            candidates = pools.enumerated().filter {
                $0.element.side == .buySide && $0.element.sourcePosition >= 0 && $0.element.status == .active
            }
        case .unconfirmed:
            return nil
        }
        return candidates.max(by: { $0.1.sourcePosition < $1.1.sourcePosition })?.0
    }

    private func activeDraw(
        _ pools: [LiquidityPool],
        currentPrice: Double,
        control: DirectionalControl
    ) -> LiquidityPool? {
        let candidates: [LiquidityPool]
        switch control {
        case .bullish:
            candidates = pools.filter {
                $0.side == .buySide && $0.status == .active && $0.price > currentPrice
            }
        case .bearish:
            candidates = pools.filter {
                $0.side == .sellSide && $0.status == .active && $0.price < currentPrice
            }
        case .unconfirmed:
            return nil
        }
        guard !candidates.isEmpty else { return nil }
        let external = candidates.filter { $0.liquidityClass == .external }
        let selection = external.isEmpty ? candidates : external
        return selection.min(by: { abs($0.price - currentPrice) < abs($1.price - currentPrice) })
    }

    private func externalPrice(_ pools: [LiquidityPool], high: Bool) -> Double? {
        let values = pools.filter { $0.liquidityClass == .external }.map(\.price)
        return high ? values.max() : values.min()
    }

    private func dealingRangeLocation(
        currentPrice: Double,
        externalHigh: Double?,
        externalLow: Double?
    ) -> (equilibrium: Double?, location: String) {
        guard let externalHigh, let externalLow, externalHigh > externalLow else {
            return (nil, "UNRESOLVED")
        }
        let equilibrium = (externalHigh + externalLow) / 2
        if currentPrice > equilibrium { return (equilibrium, "PREMIUM") }
        if currentPrice < equilibrium { return (equilibrium, "DISCOUNT") }
        return (equilibrium, "EQUILIBRIUM")
    }
}
