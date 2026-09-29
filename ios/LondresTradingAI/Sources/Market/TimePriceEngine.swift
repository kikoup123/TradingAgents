import Foundation

struct OpenSpec: Codable, Hashable, Sendable {
    let key: String
    let label: String
    let hour: Int
    let minute: Int
    let timeZoneIdentifier: String

    init(
        key: String,
        label: String,
        hour: Int,
        minute: Int,
        timeZoneIdentifier: String = "America/New_York"
    ) {
        self.key = key
        self.label = label
        self.hour = hour
        self.minute = minute
        self.timeZoneIdentifier = timeZoneIdentifier
    }
}

enum ONSRangeType: String, Codable, Hashable, Sendable {
    case wicks = "Wicks"
    case bodies = "Bodies"
}

struct ONSConfig: Codable, Hashable, Sendable {
    let key: String
    let label: String
    let timeZoneIdentifier: String
    let startHour: Int
    let startMinute: Int
    let endHour: Int
    let endMinute: Int
    let rangeType: ONSRangeType
    let showHalfDeviations: Bool
    let projectionCount: Int
}

enum OpenLevelRelation: String, Codable, Hashable, Sendable {
    case above = "ABOVE"
    case below = "BELOW"
    case at = "AT"
    case unavailable = "UNAVAILABLE"
}

struct OpenLevelResult: Codable, Hashable, Sendable {
    let key: String
    let label: String
    let price: Double?
    let timestamp: Date?
    let sourceTimeZone: String
    let relation: OpenLevelRelation
    let touched: Bool
    let crossed: Bool
    let lastCrossTime: Date?
    let available: Bool
}

enum ONSStatus: String, Codable, Hashable, Sendable {
    case unavailable = "UNAVAILABLE"
    case active = "ACTIVE"
    case complete = "COMPLETE"
}

struct ONSProjection: Codable, Hashable, Sendable {
    let deviation: Double
    let price: Double
}

struct ONSRangeResult: Codable, Hashable, Sendable {
    let key: String
    let label: String
    let sourceTimeZone: String
    let rangeType: ONSRangeType
    let startTime: Date?
    let endTime: Date?
    let high: Double?
    let low: Double?
    let equilibrium: Double?
    let rangeSize: Double?
    let upperProjections: [ONSProjection]
    let lowerProjections: [ONSProjection]
    let status: ONSStatus
}

struct TimePriceResult: Codable, Hashable, Sendable {
    let tradingDay: String
    let canonicalClock: String
    let currentPrice: Double
    let asOf: Date
    let opens: [String: OpenLevelResult]
    let ons: [String: ONSRangeResult]
}

enum TimePriceError: Error, Equatable {
    case noBars
    case invalidMinuteBar
    case mixedSymbols
    case noBarsAtOrBeforeAsOf
    case noBarsForActiveTradingDay
    case invalidTimeZone(String)
    case invalidProjectionCount
}

struct TimePriceEngine: Sendable {
    static let defaultOpenSpecs: [OpenSpec] = [
        OpenSpec(key: "asian_open", label: "ASIAN OPEN", hour: 19, minute: 30),
        OpenSpec(key: "midnight_open", label: "MIDNIGHT OPEN", hour: 0, minute: 0),
        OpenSpec(key: "london_open", label: "LD OPEN", hour: 1, minute: 30),
        OpenSpec(key: "open_0200", label: "02:00", hour: 2, minute: 0),
        OpenSpec(key: "ny_premarket_open", label: "NY PREMARKET", hour: 7, minute: 30),
        OpenSpec(key: "open_0830", label: "08:30", hour: 8, minute: 30),
        OpenSpec(key: "equities_open", label: "EQUITIES OPEN", hour: 9, minute: 30),
        OpenSpec(key: "open_1000", label: "10:00", hour: 10, minute: 0),
        OpenSpec(key: "afternoon_open", label: "13:30", hour: 13, minute: 30),
        OpenSpec(key: "open_1400", label: "14:00", hour: 14, minute: 0),
        OpenSpec(key: "settlement_open", label: "SETTLEMENT", hour: 18, minute: 0)
    ]

    static let defaultONSConfigs: [ONSConfig] = [
        ONSConfig(
            key: "ny_ons",
            label: "ONS — New York",
            timeZoneIdentifier: "America/Chicago",
            startHour: 4,
            startMinute: 0,
            endHour: 8,
            endMinute: 0,
            rangeType: .wicks,
            showHalfDeviations: true,
            projectionCount: 2
        ),
        ONSConfig(
            key: "london_ons",
            label: "ONS — London",
            timeZoneIdentifier: "Europe/London",
            startHour: 5,
            startMinute: 0,
            endHour: 7,
            endMinute: 0,
            rangeType: .wicks,
            showHalfDeviations: true,
            projectionCount: 2
        ),
        ONSConfig(
            key: "asia_ons",
            label: "ONS — Asia",
            timeZoneIdentifier: "Asia/Tokyo",
            startHour: 7,
            startMinute: 0,
            endHour: 9,
            endMinute: 0,
            rangeType: .wicks,
            showHalfDeviations: true,
            projectionCount: 2
        )
    ]

    let openSpecs: [OpenSpec]
    let onsConfigs: [ONSConfig]
    private let clock = LondresClock()

    init(
        openSpecs: [OpenSpec] = Self.defaultOpenSpecs,
        onsConfigs: [ONSConfig] = Self.defaultONSConfigs
    ) {
        self.openSpecs = openSpecs
        self.onsConfigs = onsConfigs
    }

    func analyze(
        minuteBars: [MarketCandle],
        asOf: Date? = nil,
        currentPrice: Double? = nil
    ) throws -> TimePriceResult {
        guard !minuteBars.isEmpty else { throw TimePriceError.noBars }
        guard minuteBars.allSatisfy({ $0.timeframe == .oneMinute && $0.isValid }) else {
            throw TimePriceError.invalidMinuteBar
        }

        let symbols = Set(minuteBars.map(\.symbol))
        guard symbols.count == 1 else { throw TimePriceError.mixedSymbols }

        var data = minuteBars.sorted { $0.openTime < $1.openTime }
        let resolvedAsOf: Date
        if let asOf {
            data = data.filter { $0.openTime <= asOf }
            guard !data.isEmpty else { throw TimePriceError.noBarsAtOrBeforeAsOf }
            resolvedAsOf = asOf
        } else {
            resolvedAsOf = data[data.count - 1].openTime
        }

        let activeLabel = LocalDateKey(clock.tradingDayLabel(for: resolvedAsOf))
        let current = data.filter {
            LocalDateKey(clock.tradingDayLabel(for: $0.openTime)) == activeLabel
        }
        guard !current.isEmpty else { throw TimePriceError.noBarsForActiveTradingDay }

        let px = currentPrice ?? current[current.count - 1].close
        var opens: [String: OpenLevelResult] = [:]
        for spec in openSpecs {
            opens[spec.key] = try openLevel(current: current, spec: spec, currentPrice: px)
        }

        var ons: [String: ONSRangeResult] = [:]
        for config in onsConfigs {
            guard config.projectionCount >= 1 else { throw TimePriceError.invalidProjectionCount }
            ons[config.key] = try onsRange(current: current, config: config, asOf: resolvedAsOf)
        }

        return TimePriceResult(
            tradingDay: activeLabel.iso8601,
            canonicalClock: "UTC-4_FIXED",
            currentPrice: px,
            asOf: resolvedAsOf,
            opens: opens,
            ons: ons
        )
    }

    private func openLevel(
        current: [MarketCandle],
        spec: OpenSpec,
        currentPrice: Double
    ) throws -> OpenLevelResult {
        guard let zone = TimeZone(identifier: spec.timeZoneIdentifier) else {
            throw TimePriceError.invalidTimeZone(spec.timeZoneIdentifier)
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone

        let matches = current.filter { candle in
            let parts = calendar.dateComponents([.hour, .minute], from: candle.openTime)
            return parts.hour == spec.hour && parts.minute == spec.minute
        }

        guard let matched = matches.last else {
            return OpenLevelResult(
                key: spec.key,
                label: spec.label,
                price: nil,
                timestamp: nil,
                sourceTimeZone: spec.timeZoneIdentifier,
                relation: .unavailable,
                touched: false,
                crossed: false,
                lastCrossTime: nil,
                available: false
            )
        }

        let level = matched.open
        let relation: OpenLevelRelation
        if currentPrice > level {
            relation = .above
        } else if currentPrice < level {
            relation = .below
        } else {
            relation = .at
        }

        let revisit = current.filter { $0.openTime > matched.openTime }
        let touched = revisit.contains { $0.low <= level && $0.high >= level }

        let post = current.filter { $0.openTime >= matched.openTime }
        var previousSign: Int?
        var lastCrossTime: Date?
        for candle in post {
            let difference = candle.close - level
            let sign = difference > 0 ? 1 : (difference < 0 ? -1 : 0)
            guard sign != 0 else { continue }
            if let previousSign, previousSign != sign {
                lastCrossTime = candle.openTime
            }
            previousSign = sign
        }

        return OpenLevelResult(
            key: spec.key,
            label: spec.label,
            price: level,
            timestamp: matched.openTime,
            sourceTimeZone: spec.timeZoneIdentifier,
            relation: relation,
            touched: touched,
            crossed: lastCrossTime != nil,
            lastCrossTime: lastCrossTime,
            available: true
        )
    }

    private func onsRange(
        current: [MarketCandle],
        config: ONSConfig,
        asOf: Date
    ) throws -> ONSRangeResult {
        guard let sourceZone = TimeZone(identifier: config.timeZoneIdentifier) else {
            throw TimePriceError.invalidTimeZone(config.timeZoneIdentifier)
        }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = sourceZone

        let startMinuteOfDay = config.startHour * 60 + config.startMinute
        let endMinuteOfDay = config.endHour * 60 + config.endMinute
        let isOvernight = startMinuteOfDay >= endMinuteOfDay

        let candidates: [(candle: MarketCandle, anchor: LocalDateKey)] = current.compactMap { candle in
            let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: candle.openTime)
            guard
                let hour = parts.hour,
                let minute = parts.minute,
                let year = parts.year,
                let month = parts.month,
                let day = parts.day
            else { return nil }

            let minuteOfDay = hour * 60 + minute
            let inSession: Bool
            if !isOvernight {
                inSession = minuteOfDay >= startMinuteOfDay && minuteOfDay < endMinuteOfDay
            } else {
                inSession = minuteOfDay >= startMinuteOfDay || minuteOfDay < endMinuteOfDay
            }
            guard inSession else { return nil }

            let localDate = LocalDateKey(year: year, month: month, day: day)
            if isOvernight && minuteOfDay < endMinuteOfDay {
                guard let previousDate = calendar.date(byAdding: .day, value: -1, to: candle.openTime) else {
                    return nil
                }
                return (candle, LocalDateKey(calendar.dateComponents([.year, .month, .day], from: previousDate)))
            }
            return (candle, localDate)
        }

        guard let selectedAnchor = candidates.map(\.anchor).max() else {
            return unavailableONS(config)
        }

        let session = candidates
            .filter { $0.anchor == selectedAnchor }
            .map(\.candle)
            .sorted { $0.openTime < $1.openTime }
        guard !session.isEmpty else { return unavailableONS(config) }

        let high: Double
        let low: Double
        switch config.rangeType {
        case .wicks:
            high = session.map(\.high).max() ?? 0
            low = session.map(\.low).min() ?? 0
        case .bodies:
            high = session.map(\.bodyHigh).max() ?? 0
            low = session.map(\.bodyLow).min() ?? 0
        }

        let equilibrium = (high + low) / 2
        let rangeSize = high - low
        let multiplier = config.showHalfDeviations ? 0.5 : 1.0

        let upper = (1...config.projectionCount).map { index in
            let deviation = Double(index) * multiplier
            return ONSProjection(deviation: deviation, price: high + deviation * rangeSize)
        }
        let lower = (1...config.projectionCount).map { index in
            let deviation = Double(index) * multiplier
            return ONSProjection(deviation: -deviation, price: low - deviation * rangeSize)
        }

        var endComponents = DateComponents()
        endComponents.timeZone = sourceZone
        endComponents.year = selectedAnchor.year
        endComponents.month = selectedAnchor.month
        endComponents.day = selectedAnchor.day
        endComponents.hour = config.endHour
        endComponents.minute = config.endMinute
        guard var expectedEnd = calendar.date(from: endComponents) else {
            throw TimePriceError.invalidTimeZone(config.timeZoneIdentifier)
        }
        if isOvernight {
            expectedEnd = calendar.date(byAdding: .day, value: 1, to: expectedEnd) ?? expectedEnd
        }

        return ONSRangeResult(
            key: config.key,
            label: config.label,
            sourceTimeZone: config.timeZoneIdentifier,
            rangeType: config.rangeType,
            startTime: session[0].openTime,
            endTime: expectedEnd,
            high: high,
            low: low,
            equilibrium: equilibrium,
            rangeSize: rangeSize,
            upperProjections: upper,
            lowerProjections: lower,
            status: asOf < expectedEnd ? .active : .complete
        )
    }

    private func unavailableONS(_ config: ONSConfig) -> ONSRangeResult {
        ONSRangeResult(
            key: config.key,
            label: config.label,
            sourceTimeZone: config.timeZoneIdentifier,
            rangeType: config.rangeType,
            startTime: nil,
            endTime: nil,
            high: nil,
            low: nil,
            equilibrium: nil,
            rangeSize: nil,
            upperProjections: [],
            lowerProjections: [],
            status: .unavailable
        )
    }
}

private struct LocalDateKey: Hashable, Comparable, Sendable {
    let year: Int
    let month: Int
    let day: Int

    init(year: Int, month: Int, day: Int) {
        self.year = year
        self.month = month
        self.day = day
    }

    init(_ components: DateComponents) {
        year = components.year ?? 0
        month = components.month ?? 0
        day = components.day ?? 0
    }

    var iso8601: String {
        String(format: "%04d-%02d-%02d", year, month, day)
    }

    static func < (lhs: LocalDateKey, rhs: LocalDateKey) -> Bool {
        if lhs.year != rhs.year { return lhs.year < rhs.year }
        if lhs.month != rhs.month { return lhs.month < rhs.month }
        return lhs.day < rhs.day
    }
}
