import Foundation

enum SMTPolarity: String, Codable, Hashable, Sendable {
    case same = "SAME"
    case inverse = "INVERSE"
}

enum SMTValidationState: String, Codable, Hashable, Sendable {
    case noSMT = "NO_SMT"
    case detectedWaitCSD = "SMT_DETECTED_WAIT_CSD"
    case detectedWaitIOF = "SMT_DETECTED_WAIT_IOF"
    case directionConflict = "SMT_DIRECTION_CONFLICT"
    case validated = "SMT_VALIDATED"
}

struct SMTLegConfig: Codable, Hashable, Sendable {
    let symbol: String
    let aliases: [String]
    let polarity: SMTPolarity

    init(symbol: String, aliases: [String], polarity: SMTPolarity = .same) {
        self.symbol = symbol
        self.aliases = aliases
        self.polarity = polarity
    }
}

struct SMTGroupConfig: Codable, Hashable, Sendable {
    let key: String
    let legs: [SMTLegConfig]
}

struct SMTReference: Codable, Hashable, Sendable {
    let side: String
    let price: Double
    let sourcePosition: Int
    let sourceTime: Date
    let confirmedPosition: Int
    let confirmedTime: Date
}

struct SMTComparedLevel: Codable, Hashable, Sendable {
    let polarity: SMTPolarity
    let highReference: SMTReference
    let lowReference: SMTReference
    let nativeHighTake: Bool
    let nativeLowTake: Bool
}

struct SMTResult: Codable, Hashable, Sendable {
    let group: String
    let timeframe: String
    let referenceTime: Date?
    let direction: CSDDirection
    let detected: Bool
    let validated: Bool
    let validationState: SMTValidationState
    let divergenceType: String?
    let leaderSymbols: [String]
    let nonconfirmingSymbols: [String]
    let polarityMap: [String: SMTPolarity]
    let comparedLevels: [String: SMTComparedLevel]
    let csdDirection: CSDDirection
    let iofDirection: OrderFlowDirection
    let reasonCodes: [String]
}

enum SMTError: Error, Equatable {
    case invalidPivotSpan
    case unknownGroup(String)
    case missingLegs([String])
    case invalidBars(String)
}

struct SMTEngine: Sendable {
    static let defaultGroups: [String: SMTGroupConfig] = [
        "US_INDEX": SMTGroupConfig(
            key: "US_INDEX",
            legs: [
                SMTLegConfig(symbol: "NQ", aliases: ["NQ", "NAS100", "US100", "NASDAQ"]),
                SMTLegConfig(symbol: "ES", aliases: ["ES", "US500", "SPX", "SP500"]),
                SMTLegConfig(symbol: "YM", aliases: ["YM", "US30", "DJI", "DOW"])
            ]
        ),
        "FX_DXY": SMTGroupConfig(
            key: "FX_DXY",
            legs: [
                SMTLegConfig(symbol: "EURUSD", aliases: ["EURUSD", "EUR/USD"]),
                SMTLegConfig(symbol: "GBPUSD", aliases: ["GBPUSD", "GBP/USD"]),
                SMTLegConfig(symbol: "DXY", aliases: ["DXY", "DX", "DX1!", "USDOLLAR"], polarity: .inverse)
            ]
        ),
        "GOLD_RELATIVE": SMTGroupConfig(
            key: "GOLD_RELATIVE",
            legs: [
                SMTLegConfig(symbol: "XAUUSD", aliases: ["XAUUSD", "XAU/USD", "GOLD"]),
                SMTLegConfig(symbol: "XAUAUD", aliases: ["XAUAUD", "XAU/AUD"]),
                SMTLegConfig(symbol: "XAUCAD", aliases: ["XAUCAD", "XAU/CAD"])
            ]
        )
    ]

    let pivotSpan: Int

    init(pivotSpan: Int = 2) {
        self.pivotSpan = pivotSpan
    }

    func analyze(
        instrumentBars: [String: [MarketCandle]],
        group: String,
        timeframe: String,
        csdDirection: CSDDirection = .unconfirmed,
        iofDirection: OrderFlowDirection = .unconfirmed,
        asOf: Date? = nil
    ) throws -> SMTResult {
        guard pivotSpan >= 1 else { throw SMTError.invalidPivotSpan }
        let key = group.uppercased()
        guard let config = Self.defaultGroups[key] else { throw SMTError.unknownGroup(group) }
        return try analyze(
            instrumentBars: instrumentBars,
            config: config,
            timeframe: timeframe,
            csdDirection: csdDirection,
            iofDirection: iofDirection,
            asOf: asOf
        )
    }

    func analyze(
        instrumentBars: [String: [MarketCandle]],
        config: SMTGroupConfig,
        timeframe: String,
        csdDirection: CSDDirection = .unconfirmed,
        iofDirection: OrderFlowDirection = .unconfirmed,
        asOf: Date? = nil
    ) throws -> SMTResult {
        guard pivotSpan >= 1 else { throw SMTError.invalidPivotSpan }
        let resolved = try resolveLegs(instrumentBars, config: config)
        let synchronized = synchronize(resolved, asOf: asOf)
        let polarityMap = Dictionary(uniqueKeysWithValues: config.legs.map { ($0.symbol, $0.polarity) })

        guard let firstCount = synchronized.values.first?.count,
              firstCount >= 2 * pivotSpan + 2 else {
            return emptyResult(
                config: config,
                timeframe: timeframe,
                polarityMap: polarityMap,
                csdDirection: csdDirection,
                iofDirection: iofDirection,
                reason: "INSUFFICIENT_SYNCHRONIZED_STRUCTURAL_DATA"
            )
        }

        let pivots = Dictionary(uniqueKeysWithValues: synchronized.map { symbol, bars in
            (symbol, confirmedPivots(bars))
        })
        let commonBars = synchronized[config.legs[0].symbol] ?? []
        var latestEvent: (
            referenceTime: Date,
            direction: CSDDirection,
            divergenceType: String,
            leaders: [String],
            nonconfirming: [String],
            compared: [String: SMTComparedLevel]
        )?
        var ambiguousSeen = false

        for position in (2 * pivotSpan + 1)..<commonBars.count {
            var canonicalUp: [String: Bool] = [:]
            var canonicalDown: [String: Bool] = [:]
            var compared: [String: SMTComparedLevel] = [:]
            var complete = true

            for leg in config.legs {
                guard let data = synchronized[leg.symbol],
                      let legPivots = pivots[leg.symbol],
                      let highRef = latestReference(legPivots.highs, beforePosition: position),
                      let lowRef = latestReference(legPivots.lows, beforePosition: position) else {
                    complete = false
                    break
                }

                let nativeUp = firstHighTake(data, position: position, reference: highRef)
                let nativeDown = firstLowTake(data, position: position, reference: lowRef)
                if leg.polarity == .inverse {
                    canonicalUp[leg.symbol] = nativeDown
                    canonicalDown[leg.symbol] = nativeUp
                } else {
                    canonicalUp[leg.symbol] = nativeUp
                    canonicalDown[leg.symbol] = nativeDown
                }
                compared[leg.symbol] = SMTComparedLevel(
                    polarity: leg.polarity,
                    highReference: highRef,
                    lowReference: lowRef,
                    nativeHighTake: nativeUp,
                    nativeLowTake: nativeDown
                )
            }

            guard complete else { continue }
            let upValues = config.legs.compactMap { canonicalUp[$0.symbol] }
            let downValues = config.legs.compactMap { canonicalDown[$0.symbol] }
            let upDivergence = upValues.contains(true) && upValues.contains(false)
            let downDivergence = downValues.contains(true) && downValues.contains(false)

            if upDivergence && downDivergence {
                ambiguousSeen = true
                continue
            }
            guard upDivergence || downDivergence else { continue }

            let flags = upDivergence ? canonicalUp : canonicalDown
            latestEvent = (
                referenceTime: commonBars[position].openTime,
                direction: upDivergence ? .bearish : .bullish,
                divergenceType: upDivergence ? "HIGH_SIDE_NONCONFIRMATION" : "LOW_SIDE_NONCONFIRMATION",
                leaders: config.legs.compactMap { flags[$0.symbol] == true ? $0.symbol : nil },
                nonconfirming: config.legs.compactMap { flags[$0.symbol] == false ? $0.symbol : nil },
                compared: compared
            )
        }

        guard let latestEvent else {
            return emptyResult(
                config: config,
                timeframe: timeframe,
                polarityMap: polarityMap,
                csdDirection: csdDirection,
                iofDirection: iofDirection,
                reason: ambiguousSeen
                    ? "ONLY_AMBIGUOUS_TWO_SIDED_DIVERGENCE_FOUND"
                    : "NO_STRUCTURAL_SMT_DIVERGENCE"
            )
        }

        let validation = validationState(
            smtDirection: latestEvent.direction,
            csdDirection: csdDirection,
            iofDirection: iofDirection
        )
        var reasons = [
            "STRUCTURAL_SMT_DETECTED",
            "SMT_DIRECTION_\(latestEvent.direction.rawValue)",
            "CSD_\(csdDirection.rawValue)",
            "IOF_\(iofDirection.rawValue)",
            validation.rawValue
        ]
        if config.legs.contains(where: { $0.polarity == .inverse }) {
            reasons.append("INVERSE_POLARITY_NORMALIZED")
        }

        return SMTResult(
            group: config.key,
            timeframe: timeframe,
            referenceTime: latestEvent.referenceTime,
            direction: latestEvent.direction,
            detected: true,
            validated: validation == .validated,
            validationState: validation,
            divergenceType: latestEvent.divergenceType,
            leaderSymbols: latestEvent.leaders,
            nonconfirmingSymbols: latestEvent.nonconfirming,
            polarityMap: polarityMap,
            comparedLevels: latestEvent.compared,
            csdDirection: csdDirection,
            iofDirection: iofDirection,
            reasonCodes: reasons
        )
    }

    private func resolveLegs(
        _ instrumentBars: [String: [MarketCandle]],
        config: SMTGroupConfig
    ) throws -> [String: [MarketCandle]] {
        let normalizedKeys = Dictionary(uniqueKeysWithValues: instrumentBars.keys.map { ($0.uppercased(), $0) })
        var resolved: [String: [MarketCandle]] = [:]
        var missing: [String] = []

        for leg in config.legs {
            let matched = leg.aliases.lazy.compactMap { normalizedKeys[$0.uppercased()] }.first
            guard let matched, let bars = instrumentBars[matched] else {
                missing.append(leg.symbol)
                continue
            }
            guard bars.allSatisfy(\.isValid) else { throw SMTError.invalidBars(leg.symbol) }
            resolved[leg.symbol] = bars.sorted { $0.openTime < $1.openTime }
        }
        if !missing.isEmpty { throw SMTError.missingLegs(missing) }
        return resolved
    }

    private func synchronize(
        _ resolved: [String: [MarketCandle]],
        asOf: Date?
    ) -> [String: [MarketCandle]] {
        guard let first = resolved.values.first else { return [:] }
        var common = Set(first.map(\.openTime))
        for bars in resolved.values {
            common.formIntersection(Set(bars.map(\.openTime)))
        }
        if let asOf {
            common = Set(common.filter { $0 <= asOf })
        }
        let ordered = common.sorted()
        return resolved.mapValues { bars in
            let byTime = Dictionary(uniqueKeysWithValues: bars.map { ($0.openTime, $0) })
            return ordered.compactMap { byTime[$0] }
        }
    }

    private func confirmedPivots(_ data: [MarketCandle]) -> (highs: [SMTReference], lows: [SMTReference]) {
        guard data.count >= 2 * pivotSpan + 1 else { return ([], []) }
        var highs: [SMTReference] = []
        var lows: [SMTReference] = []
        for position in pivotSpan..<(data.count - pivotSpan) {
            let row = data[position]
            let left = data[(position - pivotSpan)..<position]
            let right = data[(position + 1)...(position + pivotSpan)]
            let confirmationPosition = position + pivotSpan
            if row.high > left.map(\.high).max()! && row.high > right.map(\.high).max()! {
                highs.append(
                    SMTReference(
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
                    SMTReference(
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

    private func latestReference(_ references: [SMTReference], beforePosition: Int) -> SMTReference? {
        references.last(where: { $0.confirmedPosition < beforePosition })
    }

    private func firstHighTake(_ data: [MarketCandle], position: Int, reference: SMTReference) -> Bool {
        guard data[position].high > reference.price else { return false }
        let previous = reference.confirmedPosition < position
            ? data[reference.confirmedPosition..<position]
            : data[0..<0]
        return previous.map(\.high).max().map { $0 <= reference.price } ?? true
    }

    private func firstLowTake(_ data: [MarketCandle], position: Int, reference: SMTReference) -> Bool {
        guard data[position].low < reference.price else { return false }
        let previous = reference.confirmedPosition < position
            ? data[reference.confirmedPosition..<position]
            : data[0..<0]
        return previous.map(\.low).min().map { $0 >= reference.price } ?? true
    }

    private func validationState(
        smtDirection: CSDDirection,
        csdDirection: CSDDirection,
        iofDirection: OrderFlowDirection
    ) -> SMTValidationState {
        guard csdDirection == .bullish || csdDirection == .bearish else { return .detectedWaitCSD }
        guard csdDirection == smtDirection else { return .directionConflict }
        guard iofDirection == .bullish || iofDirection == .bearish else { return .detectedWaitIOF }
        guard iofDirection.rawValue == smtDirection.rawValue else { return .directionConflict }
        return .validated
    }

    private func emptyResult(
        config: SMTGroupConfig,
        timeframe: String,
        polarityMap: [String: SMTPolarity],
        csdDirection: CSDDirection,
        iofDirection: OrderFlowDirection,
        reason: String
    ) -> SMTResult {
        SMTResult(
            group: config.key,
            timeframe: timeframe,
            referenceTime: nil,
            direction: .unconfirmed,
            detected: false,
            validated: false,
            validationState: .noSMT,
            divergenceType: nil,
            leaderSymbols: [],
            nonconfirmingSymbols: [],
            polarityMap: polarityMap,
            comparedLevels: [:],
            csdDirection: csdDirection,
            iofDirection: iofDirection,
            reasonCodes: [reason]
        )
    }
}
