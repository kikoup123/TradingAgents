import Foundation

enum DailyProfileError: Error, Equatable {
    case noBars
    case invalidBar
}

struct DailyProfileEngine: Sendable {
    private let clock = LondresClock()

    func analyze(
        intradayBars: [MarketCandle],
        weeklyProfile: WeeklyProfileResult,
        dailyOrderFlowControl: DirectionalControl = .unconfirmed,
        protectedDailyExtreme: Bool? = nil,
        opposingDrawReached: Bool? = nil
    ) throws -> DailyProfileResult {
        guard !intradayBars.isEmpty else { throw DailyProfileError.noBars }
        guard intradayBars.allSatisfy(\.isValid) else { throw DailyProfileError.invalidBar }

        let data = intradayBars.sorted { $0.openTime < $1.openTime }
        let currentKey = dayKey(data[data.count - 1].openTime)
        let current = data.filter { dayKey($0.openTime) == currentKey }
        guard !current.isEmpty else { throw DailyProfileError.noBars }

        let dailyOpen = current[0].open
        let dailyHigh = current.map(\.high).max() ?? current[0].high
        let dailyLow = current.map(\.low).min() ?? current[0].low
        let currentClose = current[current.count - 1].close
        let highPosition = firstPosition(in: current, matching: dailyHigh, keyPath: \.high)
        let lowPosition = firstPosition(in: current, matching: dailyLow, keyPath: \.low)
        let highTime = current[highPosition].openTime
        let lowTime = current[lowPosition].openTime

        let observedDelivery = observedDelivery(highPosition: highPosition, lowPosition: lowPosition)
        let expected = expectedDelivery(weeklyProfile)
        let status = profileStatus(
            expectedDirection: expected.direction,
            expectedDelivery: expected.delivery,
            observedDelivery: observedDelivery,
            orderFlowControl: dailyOrderFlowControl,
            protectedDailyExtreme: protectedDailyExtreme
        )
        let phase = phase(
            dayType: weeklyProfile.currentDayType,
            expectedDelivery: expected.delivery,
            expectedDirection: expected.direction,
            status: status,
            dailyOpen: dailyOpen,
            currentClose: currentClose,
            highPosition: highPosition,
            lowPosition: lowPosition,
            opposingDrawReached: opposingDrawReached
        )

        var reasons = [
            "WEEKLY_DAY_TYPE_\(weeklyProfile.currentDayType.rawValue)",
            "OBSERVED_DELIVERY_\(observedDelivery.rawValue)",
            "DAILY_ORDER_FLOW_\(dailyOrderFlowControl.rawValue)"
        ]
        if expected.delivery != .unresolved {
            reasons.append("EXPECTED_DELIVERY_\(expected.delivery.rawValue)")
        }
        if protectedDailyExtreme == true {
            reasons.append("DAILY_EXTREME_PROTECTED")
        } else if protectedDailyExtreme == false {
            reasons.append("DAILY_EXTREME_FAILED_PROTECTION")
        } else {
            reasons.append("DAILY_EXTREME_PROTECTION_PENDING")
        }
        if opposingDrawReached == true {
            reasons.append("OPPOSING_DRAW_REACHED")
        } else if opposingDrawReached == false {
            reasons.append("OPPOSING_DRAW_UNREACHED")
        }

        return DailyProfileResult(
            tradingDay: currentKey,
            direction: expected.direction,
            dayType: weeklyProfile.currentDayType,
            expectedDelivery: expected.delivery,
            observedDelivery: observedDelivery,
            status: status,
            phase: phase,
            dailyOpen: dailyOpen,
            dailyHigh: dailyHigh,
            dailyLow: dailyLow,
            currentClose: currentClose,
            highTime: highTime,
            lowTime: lowTime,
            protectedExtreme: protectedDailyExtreme,
            expectedNextPhase: nextPhase(phase, opposingDrawReached: opposingDrawReached),
            reasonCodes: reasons
        )
    }

    private func dayKey(_ date: Date) -> String {
        let components = clock.tradingDayLabel(for: date)
        return String(
            format: "%04d-%02d-%02d",
            components.year ?? 0,
            components.month ?? 0,
            components.day ?? 0
        )
    }

    private func firstPosition(
        in bars: [MarketCandle],
        matching value: Double,
        keyPath: KeyPath<MarketCandle, Double>
    ) -> Int {
        bars.firstIndex(where: { $0[keyPath: keyPath] == value }) ?? 0
    }

    private func observedDelivery(highPosition: Int, lowPosition: Int) -> DailyDelivery {
        if lowPosition < highPosition { return .olhc }
        if highPosition < lowPosition { return .ohlc }
        return .unresolved
    }

    private func opposite(_ direction: DirectionalControl) -> DirectionalControl {
        switch direction {
        case .bullish: return .bearish
        case .bearish: return .bullish
        case .unconfirmed: return .unconfirmed
        }
    }

    private func expectedDelivery(
        _ weeklyProfile: WeeklyProfileResult
    ) -> (direction: DirectionalControl, delivery: DailyDelivery) {
        let direction = weeklyProfile.direction
        guard direction == .bullish || direction == .bearish else {
            return (.unconfirmed, .unresolved)
        }

        if weeklyProfile.currentDayType == .continuationCandidate
            || weeklyProfile.currentDayType == .reversalCandidate {
            return (
                direction,
                direction == .bullish ? .olhc : .ohlc
            )
        }

        if weeklyProfile.currentDayType == .retracementCandidate
            || weeklyProfile.currentDayType == .returnToRangeCandidate {
            let inverse = opposite(direction)
            return (
                inverse,
                inverse == .bullish ? .olhc : .ohlc
            )
        }

        return (.unconfirmed, .unresolved)
    }

    private func profileStatus(
        expectedDirection: DirectionalControl,
        expectedDelivery: DailyDelivery,
        observedDelivery: DailyDelivery,
        orderFlowControl: DirectionalControl,
        protectedDailyExtreme: Bool?
    ) -> ProfileStatus {
        if expectedDelivery == .unresolved { return .unresolved }
        if protectedDailyExtreme == false { return .invalidated }
        if observedDelivery == expectedDelivery
            && orderFlowControl == expectedDirection
            && protectedDailyExtreme == true {
            return .confirmed
        }
        return .developing
    }

    private func phase(
        dayType: DayType,
        expectedDelivery: DailyDelivery,
        expectedDirection: DirectionalControl,
        status: ProfileStatus,
        dailyOpen: Double,
        currentClose: Double,
        highPosition: Int,
        lowPosition: Int,
        opposingDrawReached: Bool?
    ) -> DailyPhase {
        if status == .invalidated { return .invalidated }
        if expectedDelivery == .unresolved { return .unresolved }
        if dayType == .retracementCandidate || dayType == .returnToRangeCandidate {
            return .retracement
        }
        if opposingDrawReached == true { return .objectiveReached }
        if dayType == .reversalCandidate && status != .confirmed {
            return .reversalFormation
        }
        if status == .confirmed { return .expansion }

        if expectedDirection == .bullish {
            if currentClose <= dailyOpen && lowPosition <= highPosition {
                return .manipulation
            }
            if currentClose > dailyOpen && lowPosition < highPosition {
                return .expansion
            }
        } else if expectedDirection == .bearish {
            if currentClose >= dailyOpen && highPosition <= lowPosition {
                return .manipulation
            }
            if currentClose < dailyOpen && highPosition < lowPosition {
                return .expansion
            }
        }
        return .opening
    }

    private func nextPhase(_ phase: DailyPhase, opposingDrawReached: Bool?) -> String? {
        switch phase {
        case .invalidated:
            return "RECLASSIFY_DAILY_PROFILE"
        case .unresolved:
            return "WAIT_FOR_WEEKLY_DAY_TYPE"
        case .opening, .manipulation, .reversalFormation:
            return "WAIT_FOR_DIRECTIONAL_ORDER_FLOW_AND_PROTECTED_EXTREME"
        case .expansion:
            return opposingDrawReached == false
                ? "CONTINUE_TOWARD_OPPOSING_DRAW"
                : "MONITOR_DRAW_COMPLETION_AND_REBALANCE"
        case .objectiveReached:
            return "REASSESS_CONTINUATION_VS_RETRACEMENT"
        case .retracement:
            return "MONITOR_RETURN_TO_RANGE_AND_ORDER_FLOW"
        }
    }
}
