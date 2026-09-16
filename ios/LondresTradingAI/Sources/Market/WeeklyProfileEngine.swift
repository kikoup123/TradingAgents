import Foundation

enum WeeklyProfileError: Error, Equatable {
    case noBars
    case invalidBar
    case invalidTradingDay
}

struct WeeklyProfileEngine: Sendable {
    private static let dayOrder: [String: Int] = [
        "MONDAY": 0,
        "TUESDAY": 1,
        "WEDNESDAY": 2,
        "THURSDAY": 3,
        "FRIDAY": 4
    ]

    func analyze(
        dailyBars: [MarketCandle],
        htfControl: DirectionalControl,
        protectedWeeklyExtreme: Bool? = nil,
        opposingDrawReached: Bool? = nil,
        thursdayExternalManipulation: Bool? = nil
    ) throws -> WeeklyProfileResult {
        guard !dailyBars.isEmpty else { throw WeeklyProfileError.noBars }
        guard dailyBars.allSatisfy(\.isValid) else { throw WeeklyProfileError.invalidBar }

        let sorted = dailyBars.sorted { $0.openTime < $1.openTime }
        let data = Array(sorted.suffix(5))
        let days = data.map { dayName($0.openTime) }
        guard days.allSatisfy({ Self.dayOrder[$0] != nil }) else {
            throw WeeklyProfileError.invalidTradingDay
        }
        let currentDay = days[days.count - 1]

        guard htfControl == .bullish || htfControl == .bearish else {
            return WeeklyProfileResult(
                profile: .unresolved,
                status: .unresolved,
                direction: htfControl,
                currentDay: currentDay,
                currentDayType: .unresolved,
                weekPhase: "ORDER_FLOW_UNRESOLVED",
                weeklyExtreme: nil,
                expectedDailyDelivery: nil,
                expectedNextPhase: "WAIT_FOR_HTF_ORDER_FLOW_CONTROL",
                reasonCodes: ["HTF_ORDER_FLOW_NOT_DIRECTIONAL"]
            )
        }

        let extremePosition: Int
        let extremeType: String
        let extremePrice: Double
        let expectedDailyDelivery: String

        if htfControl == .bullish {
            extremePosition = data.indices.min(by: { data[$0].low < data[$1].low }) ?? 0
            extremeType = "LOW"
            extremePrice = data[extremePosition].low
            expectedDailyDelivery = "OLHC"
        } else {
            extremePosition = data.indices.max(by: { data[$0].high < data[$1].high }) ?? 0
            extremeType = "HIGH"
            extremePrice = data[extremePosition].high
            expectedDailyDelivery = "OHLC"
        }

        let extremeDay = days[extremePosition]
        let weeklyExtreme = WeeklyExtreme(
            extremeType: extremeType,
            day: extremeDay,
            price: extremePrice,
            isProtected: protectedWeeklyExtreme
        )
        let profile = profileForDay(
            extremeDay,
            currentDay: currentDay,
            thursdayExternalManipulation: thursdayExternalManipulation
        )
        let status = status(profile: profile, protectedWeeklyExtreme: protectedWeeklyExtreme)
        let phase = phaseLogic(
            profile: profile,
            status: status,
            extremeDay: extremeDay,
            currentDay: currentDay,
            opposingDrawReached: opposingDrawReached
        )

        var reasons = phase.reasonCodes
        reasons.insert("OBSERVED_WEEKLY_\(extremeType)_ON_\(extremeDay)", at: 0)
        reasons.insert("HTF_CONTROL_\(htfControl.rawValue)", at: 1)
        if protectedWeeklyExtreme == true {
            reasons.append("WEEKLY_EXTREME_PROTECTED")
        } else if protectedWeeklyExtreme == false {
            reasons.append("WEEKLY_EXTREME_FAILED_PROTECTION")
        } else {
            reasons.append("WEEKLY_EXTREME_PROTECTION_PENDING")
        }

        return WeeklyProfileResult(
            profile: profile,
            status: status,
            direction: htfControl,
            currentDay: currentDay,
            currentDayType: phase.dayType,
            weekPhase: phase.weekPhase,
            weeklyExtreme: weeklyExtreme,
            expectedDailyDelivery: expectedDailyDelivery,
            expectedNextPhase: phase.nextPhase,
            reasonCodes: reasons
        )
    }

    private func dayName(_ date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = LondresClock.fixedUTCMinus4
        calendar.locale = Locale(identifier: "en_US_POSIX")
        let weekday = calendar.component(.weekday, from: date)
        switch weekday {
        case 2: return "MONDAY"
        case 3: return "TUESDAY"
        case 4: return "WEDNESDAY"
        case 5: return "THURSDAY"
        case 6: return "FRIDAY"
        case 7: return "SATURDAY"
        default: return "SUNDAY"
        }
    }

    private func profileForDay(
        _ extremeDay: String,
        currentDay: String,
        thursdayExternalManipulation: Bool?
    ) -> WeeklyProfileType {
        if extremeDay == "MONDAY" || extremeDay == "TUESDAY" {
            return .classicExpansion
        }
        if extremeDay == "WEDNESDAY" {
            return .midweekReversal
        }
        if extremeDay == "THURSDAY" {
            return .thursdayReversal
        }
        if extremeDay == "FRIDAY" && currentDay == "FRIDAY" && thursdayExternalManipulation == true {
            return .fridayReversal
        }
        return .unresolved
    }

    private func status(
        profile: WeeklyProfileType,
        protectedWeeklyExtreme: Bool?
    ) -> ProfileStatus {
        if profile == .unresolved { return .unresolved }
        if protectedWeeklyExtreme == true { return .confirmed }
        if protectedWeeklyExtreme == false { return .invalidated }
        return .developing
    }

    private func phaseLogic(
        profile: WeeklyProfileType,
        status: ProfileStatus,
        extremeDay: String,
        currentDay: String,
        opposingDrawReached: Bool?
    ) -> (dayType: DayType, weekPhase: String, nextPhase: String?, reasonCodes: [String]) {
        if profile == .unresolved {
            return (
                .unresolved,
                "UNRESOLVED",
                "WAIT_FOR_VALID_WEEKLY_PROFILE",
                ["NO_SUPPORTED_WEEKLY_PROFILE_YET"]
            )
        }

        if status == .invalidated {
            return (
                .unresolved,
                "PROFILE_INVALIDATED",
                "RECLASSIFY_WEEKLY_PROFILE",
                ["PROTECTED_EXTREME_INVALIDATED"]
            )
        }

        let currentOrder = Self.dayOrder[currentDay] ?? 0
        let extremeOrder = Self.dayOrder[extremeDay] ?? 0

        if currentOrder == extremeOrder {
            return (
                .reversalCandidate,
                "REVERSAL_FORMATION",
                "WAIT_FOR_PROTECTION_AND_DIRECTIONAL_DELIVERY",
                ["CURRENT_DAY_IS_WEEKLY_EXTREME_CANDIDATE"]
            )
        }

        if currentOrder < extremeOrder {
            return (
                .range,
                "PRE_REVERSAL_RANGE",
                "WATCH_\(extremeDay)_FOR_REVERSAL",
                ["PROFILE_EXTREME_LIES_LATER_IN_WEEK"]
            )
        }

        if status != .confirmed {
            return (
                .unresolved,
                "POST_EXTREME_UNCONFIRMED",
                "WAIT_FOR_WEEKLY_EXTREME_PROTECTION",
                ["EXTREME_EXISTS_BUT_IS_NOT_YET_PROTECTED"]
            )
        }

        if profile == .classicExpansion {
            if currentDay == "FRIDAY" {
                return (
                    .retracementCandidate,
                    "FRIDAY_REBALANCE",
                    "REASSESS_RETRACEMENT_VS_UNFINISHED_DRAW",
                    ["CLASSIC_EXPANSION_FRIDAY_RETRACEMENT_BIAS"]
                )
            }
            return (
                .continuationCandidate,
                "WEEKLY_EXPANSION",
                "CONTINUATION_TOWARD_OPPOSING_WEEKLY_DRAW",
                ["EARLY_WEEK_EXTREME_PROTECTED"]
            )
        }

        if profile == .midweekReversal {
            return (
                .continuationCandidate,
                "POST_MIDWEEK_EXPANSION",
                "CONTINUATION_TOWARD_OPPOSING_WEEKLY_DRAW",
                ["WEDNESDAY_EXTREME_PROTECTED"]
            )
        }

        if profile == .thursdayReversal && currentDay == "FRIDAY" {
            if opposingDrawReached == false {
                return (
                    .continuationCandidate,
                    "FRIDAY_CONTINUATION",
                    "CONTINUE_TOWARD_UNFINISHED_OPPOSING_DRAW",
                    ["THURSDAY_REVERSAL_CONFIRMED", "OPPOSING_DRAW_UNREACHED"]
                )
            }
            if opposingDrawReached == true {
                return (
                    .returnToRangeCandidate,
                    "FRIDAY_RETURN_TO_RANGE",
                    "LOOK_FOR_REBALANCE_AFTER_DRAW_COMPLETION",
                    ["THURSDAY_REVERSAL_CONFIRMED", "OPPOSING_DRAW_REACHED"]
                )
            }
            return (
                .waitingForDrawStatus,
                "FRIDAY_BRANCH_PENDING",
                "RESOLVE_OPPOSING_DRAW_STATUS",
                ["THURSDAY_REVERSAL_CONFIRMED", "OPPOSING_DRAW_STATUS_UNKNOWN"]
            )
        }

        if profile == .fridayReversal {
            return (
                .reversalCandidate,
                "DELAYED_FRIDAY_REVERSAL",
                "WAIT_FOR_FRIDAY_REVERSAL_CONFIRMATION",
                ["THURSDAY_EXTERNAL_MANIPULATION_WITHOUT_EARLIER_EXTREME_PROTECTION"]
            )
        }

        return (
            .continuationCandidate,
            "EXPANSION",
            "CONTINUE_WITH_CONFIRMED_WEEKLY_DIRECTION",
            []
        )
    }
}
