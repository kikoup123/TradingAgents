import Foundation

enum WeeklyProfileType: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case classicExpansion = "CLASSIC_EXPANSION"
    case midweekReversal = "MIDWEEK_REVERSAL"
    case thursdayReversal = "THURSDAY_REVERSAL"
    case fridayReversal = "FRIDAY_REVERSAL"
}

enum ProfileStatus: String, Codable, Hashable, Sendable {
    case developing = "DEVELOPING"
    case confirmed = "CONFIRMED"
    case invalidated = "INVALIDATED"
    case unresolved = "UNRESOLVED"
}

enum DayType: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case range = "RANGE"
    case reversalCandidate = "REVERSAL_CANDIDATE"
    case continuationCandidate = "CONTINUATION_CANDIDATE"
    case retracementCandidate = "RETRACEMENT_CANDIDATE"
    case returnToRangeCandidate = "RETURN_TO_RANGE_CANDIDATE"
    case waitingForDrawStatus = "WAITING_FOR_DRAW_STATUS"
}

enum DailyDelivery: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case olhc = "OLHC"
    case ohlc = "OHLC"
}

enum DailyPhase: String, Codable, Hashable, Sendable {
    case unresolved = "UNRESOLVED"
    case opening = "OPENING"
    case manipulation = "MANIPULATION"
    case reversalFormation = "REVERSAL_FORMATION"
    case expansion = "EXPANSION"
    case objectiveReached = "OBJECTIVE_REACHED"
    case retracement = "RETRACEMENT"
    case invalidated = "INVALIDATED"
}

struct WeeklyExtreme: Codable, Hashable, Sendable {
    let extremeType: String
    let day: String
    let price: Double
    let isProtected: Bool?
}

struct WeeklyProfileResult: Codable, Hashable, Sendable {
    let profile: WeeklyProfileType
    let status: ProfileStatus
    let direction: DirectionalControl
    let currentDay: String
    let currentDayType: DayType
    let weekPhase: String
    let weeklyExtreme: WeeklyExtreme?
    let expectedDailyDelivery: String?
    let expectedNextPhase: String?
    let reasonCodes: [String]
}

struct DailyProfileResult: Codable, Hashable, Sendable {
    let tradingDay: String
    let direction: DirectionalControl
    let dayType: DayType
    let expectedDelivery: DailyDelivery
    let observedDelivery: DailyDelivery
    let status: ProfileStatus
    let phase: DailyPhase
    let dailyOpen: Double
    let dailyHigh: Double
    let dailyLow: Double
    let currentClose: Double
    let highTime: Date
    let lowTime: Date
    let protectedExtreme: Bool?
    let expectedNextPhase: String?
    let reasonCodes: [String]
}
