import Foundation

enum H4ProfileType: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case londonReversal = "LONDON_REVERSAL"
    case sixAMContinuation = "SIX_AM_CONTINUATION"
    case sixAMReversal = "SIX_AM_REVERSAL"
    case nyContinuation = "NY_CONTINUATION"
    case nyReversal = "NY_REVERSAL"
}

enum H4Phase: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case preDriver = "PRE_DRIVER"
    case driverContinuation = "DRIVER_CONTINUATION"
    case driverReversal = "DRIVER_REVERSAL"
    case postDriverExpansion = "POST_DRIVER_EXPANSION"
    case completed = "COMPLETED"
    case invalidated = "INVALIDATED"
}

enum H4LocationContext: String, Codable, Hashable, Sendable {
    case unknown = "UNKNOWN"
    case irl = "IRL"
    case erlToIrl = "ERL_TO_IRL"
    case opr = "OPR"
    case obContinuation = "OB_CONTINUATION"
}

struct H4CandleState: Codable, Hashable, Sendable {
    let label: String
    let startTime: Date
    let endTime: Date
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let complete: Bool
}

struct H4ProfileResult: Codable, Hashable, Sendable {
    let profile: H4ProfileType
    let status: ProfileStatus
    let direction: DirectionalControl
    let phase: H4Phase
    let activeH4: String
    let driverH4: String
    let expectedDriverAction: String
    let reversalBeforeDriver: Bool?
    let locationContext: H4LocationContext
    let candles: [H4CandleState]
    let expectedNextPhase: String?
    let reasonCodes: [String]
}

enum H4ProfileError: Error, Equatable {
    case noBars
    case invalidBar
    case unableToBuildCandles
}

struct H4ProfileEngine: Sendable {
    static let labels = ["18:00", "22:00", "02:00", "06:00", "10:00", "14:00"]
    static let driverIndex = 3
    static let driverLabel = "06:00"

    private let clock = LondresClock()

    func analyze(
        intradayBars: [MarketCandle],
        dailyProfile: DailyProfileResult,
        h4OrderFlowControl: DirectionalControl = .unconfirmed,
        reversalConfirmedAt: Date? = nil,
        locationContext: H4LocationContext = .unknown
    ) throws -> H4ProfileResult {
        guard !intradayBars.isEmpty else { throw H4ProfileError.noBars }
        guard intradayBars.allSatisfy(\.isValid) else { throw H4ProfileError.invalidBar }

        let data = intradayBars.sorted { $0.openTime < $1.openTime }
        let currentLabel = clock.tradingDayLabel(for: data[data.count - 1].openTime)
        let current = data.filter { clock.tradingDayLabel(for: $0.openTime) == currentLabel }
        guard let dayStart = tradingDayStart(currentLabel) else { throw H4ProfileError.unableToBuildCandles }
        let now = current[current.count - 1].openTime
        let candles = aggregateH4(current, dayStart: dayStart, now: now)
        guard !candles.isEmpty else { throw H4ProfileError.unableToBuildCandles }

        let activeH4 = candles[candles.count - 1].label
        let activeIndex = Self.labels.firstIndex(of: activeH4) ?? 0
        let driverStart = dayStart.addingTimeInterval(TimeInterval(Self.driverIndex * 4 * 3600))
        let reversalBeforeDriver = reversalBeforeDriver(
            reversalConfirmedAt,
            driverStart: driverStart,
            now: now
        )
        let direction = dailyProfile.direction

        if dailyProfile.status == .invalidated {
            return invalidatedResult(
                direction: direction,
                candles: candles,
                activeH4: activeH4,
                locationContext: locationContext,
                reason: "DAILY_PROFILE_INVALIDATED",
                reversalBeforeDriver: reversalBeforeDriver
            )
        }

        guard direction == .bullish || direction == .bearish else {
            return H4ProfileResult(
                profile: .unresolved,
                status: .unresolved,
                direction: direction,
                phase: .unresolved,
                activeH4: activeH4,
                driverH4: Self.driverLabel,
                expectedDriverAction: "UNRESOLVED",
                reversalBeforeDriver: reversalBeforeDriver,
                locationContext: locationContext,
                candles: candles,
                expectedNextPhase: "WAIT_FOR_DAILY_DIRECTION",
                reasonCodes: ["DAILY_DIRECTION_NOT_RESOLVED"]
            )
        }

        var state = profileState(
            activeIndex: activeIndex,
            reversalBeforeDriver: reversalBeforeDriver,
            reversalConfirmedAt: reversalConfirmedAt,
            driverStart: driverStart
        )
        var status: ProfileStatus = .developing
        var reasons = [
            "ACTIVE_H4_\(activeH4.replacingOccurrences(of: ":", with: ""))",
            "DAILY_DIRECTION_\(direction.rawValue)",
            "H4_ORDER_FLOW_\(h4OrderFlowControl.rawValue)",
            "LOCATION_\(locationContext.rawValue)"
        ]
        if reversalBeforeDriver == true {
            reasons.append("REVERSAL_CONFIRMED_BEFORE_0600_DRIVER")
        } else if reversalBeforeDriver == false {
            reasons.append("NO_REVERSAL_CONFIRMED_BEFORE_0600_DRIVER")
        } else {
            reasons.append("PRE_DRIVER_REVERSAL_STATUS_PENDING")
        }

        if let driver = candles.first(where: { $0.label == Self.driverLabel }), driver.complete {
            if state.expectedAction == "REVERSAL" {
                if driverReversalEvidence(
                    candles: candles,
                    driver: driver,
                    direction: direction,
                    h4OrderFlowControl: h4OrderFlowControl
                ) {
                    status = .confirmed
                    reasons.append("0600_DRIVER_REVERSAL_CONFIRMED")
                } else {
                    return invalidatedResult(
                        direction: direction,
                        candles: candles,
                        activeH4: activeH4,
                        locationContext: locationContext,
                        reason: "0600_DRIVER_FAILED_TO_REVERSE",
                        reversalBeforeDriver: reversalBeforeDriver
                    )
                }
            } else if state.expectedAction == "CONTINUATION" {
                if driverContinuationEvidence(
                    driver: driver,
                    direction: direction,
                    h4OrderFlowControl: h4OrderFlowControl
                ) {
                    status = .confirmed
                    reasons.append("0600_DRIVER_CONTINUATION_CONFIRMED")
                } else {
                    reasons.append("0600_DRIVER_CONTINUATION_NOT_YET_CONFIRMED")
                }
            }
        }

        if activeIndex > Self.driverIndex && status == .confirmed {
            state.phase = .postDriverExpansion
            if state.profile == .sixAMContinuation {
                state.profile = .nyContinuation
            } else if state.profile == .sixAMReversal {
                state.profile = .nyReversal
            }
        }

        return H4ProfileResult(
            profile: state.profile,
            status: status,
            direction: direction,
            phase: state.phase,
            activeH4: activeH4,
            driverH4: Self.driverLabel,
            expectedDriverAction: state.expectedAction,
            reversalBeforeDriver: reversalBeforeDriver,
            locationContext: locationContext,
            candles: candles,
            expectedNextPhase: nextPhase(
                status: status,
                phase: state.phase,
                expectedAction: state.expectedAction
            ),
            reasonCodes: reasons
        )
    }

    private func tradingDayStart(_ label: DateComponents) -> Date? {
        guard let year = label.year, let month = label.month, let day = label.day else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = LondresClock.fixedUTCMinus4
        var labelled = DateComponents()
        labelled.timeZone = LondresClock.fixedUTCMinus4
        labelled.year = year
        labelled.month = month
        labelled.day = day
        labelled.hour = 0
        guard let labelledDate = calendar.date(from: labelled),
              let priorDate = calendar.date(byAdding: .day, value: -1, to: labelledDate) else {
            return nil
        }
        let prior = calendar.dateComponents([.year, .month, .day], from: priorDate)
        return clock.canonicalDate(
            year: prior.year ?? year,
            month: prior.month ?? month,
            day: prior.day ?? day,
            hour: 18
        )
    }

    private func aggregateH4(
        _ current: [MarketCandle],
        dayStart: Date,
        now: Date
    ) -> [H4CandleState] {
        var groups: [Int: [MarketCandle]] = [:]
        for candle in current {
            let elapsedHours = candle.openTime.timeIntervalSince(dayStart) / 3600
            let bucket = Int(floor(elapsedHours / 4))
            guard (0...5).contains(bucket) else { continue }
            groups[bucket, default: []].append(candle)
        }

        return groups.keys.sorted().compactMap { bucket in
            guard let group = groups[bucket]?.sorted(by: { $0.openTime < $1.openTime }),
                  let first = group.first,
                  let last = group.last else {
                return nil
            }
            let start = dayStart.addingTimeInterval(TimeInterval(bucket * 4 * 3600))
            let end = start.addingTimeInterval(4 * 3600)
            return H4CandleState(
                label: Self.labels[bucket],
                startTime: start,
                endTime: end,
                open: first.open,
                high: group.map(\.high).max() ?? first.high,
                low: group.map(\.low).min() ?? first.low,
                close: last.close,
                complete: now >= end
            )
        }
    }

    private func reversalBeforeDriver(
        _ reversalConfirmedAt: Date?,
        driverStart: Date,
        now: Date
    ) -> Bool? {
        if let reversalConfirmedAt {
            return reversalConfirmedAt < driverStart
        }
        if now < driverStart { return nil }
        return false
    }

    private func profileState(
        activeIndex: Int,
        reversalBeforeDriver: Bool?,
        reversalConfirmedAt: Date?,
        driverStart: Date
    ) -> (profile: H4ProfileType, phase: H4Phase, expectedAction: String) {
        if activeIndex < Self.driverIndex {
            if let reversalConfirmedAt {
                let londonStart = driverStart.addingTimeInterval(-4 * 3600)
                if reversalConfirmedAt >= londonStart && reversalConfirmedAt < driverStart {
                    return (.londonReversal, .preDriver, "CONTINUATION")
                }
            }
            return (.unresolved, .preDriver, "PENDING")
        }

        if reversalBeforeDriver == true {
            return (.sixAMContinuation, .driverContinuation, "CONTINUATION")
        }
        return (.sixAMReversal, .driverReversal, "REVERSAL")
    }

    private func bodyMatchesDirection(_ candle: H4CandleState, direction: DirectionalControl) -> Bool {
        if direction == .bullish { return candle.close > candle.open }
        if direction == .bearish { return candle.close < candle.open }
        return false
    }

    private func driverContinuationEvidence(
        driver: H4CandleState,
        direction: DirectionalControl,
        h4OrderFlowControl: DirectionalControl
    ) -> Bool {
        bodyMatchesDirection(driver, direction: direction) && h4OrderFlowControl == direction
    }

    private func driverReversalEvidence(
        candles: [H4CandleState],
        driver: H4CandleState,
        direction: DirectionalControl,
        h4OrderFlowControl: DirectionalControl
    ) -> Bool {
        let preDriver = candles.filter {
            guard let index = Self.labels.firstIndex(of: $0.label) else { return false }
            return index < Self.driverIndex
        }
        guard !preDriver.isEmpty,
              h4OrderFlowControl == direction,
              bodyMatchesDirection(driver, direction: direction) else {
            return false
        }

        if direction == .bullish {
            let priorLow = preDriver.map(\.low).min() ?? driver.low
            return driver.low <= priorLow
        }
        let priorHigh = preDriver.map(\.high).max() ?? driver.high
        return driver.high >= priorHigh
    }

    private func nextPhase(
        status: ProfileStatus,
        phase: H4Phase,
        expectedAction: String
    ) -> String? {
        if status == .invalidated { return "RECLASSIFY_H4_PROFILE" }
        if phase == .preDriver { return "RESOLVE_REVERSAL_BEFORE_0600_DRIVER" }
        if phase == .driverContinuation || phase == .driverReversal {
            return "WAIT_FOR_0600_\(expectedAction)_CONFIRMATION"
        }
        if phase == .postDriverExpansion {
            return "CONTINUE_WITH_DAILY_PROFILE_UNTIL_DRAW_OR_INVALIDATION"
        }
        return nil
    }

    private func invalidatedResult(
        direction: DirectionalControl,
        candles: [H4CandleState],
        activeH4: String,
        locationContext: H4LocationContext,
        reason: String,
        reversalBeforeDriver: Bool? = nil
    ) -> H4ProfileResult {
        H4ProfileResult(
            profile: .unresolved,
            status: .invalidated,
            direction: direction,
            phase: .invalidated,
            activeH4: activeH4,
            driverH4: Self.driverLabel,
            expectedDriverAction: "RECLASSIFY",
            reversalBeforeDriver: reversalBeforeDriver,
            locationContext: locationContext,
            candles: candles,
            expectedNextPhase: "RECLASSIFY_H4_PROFILE",
            reasonCodes: [reason]
        )
    }
}
