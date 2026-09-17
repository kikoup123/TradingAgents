import Foundation

struct LondresClock: Sendable {
    static let fixedUTCMinus4 = TimeZone(secondsFromGMT: -4 * 60 * 60)!
    static let dailyRolloverHour = 18

    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = Self.fixedUTCMinus4
        calendar.locale = Locale(identifier: "en_US_POSIX")
        return calendar
    }

    /// Labels a Londres trading day by the calendar date on which the 18:00-to-18:00
    /// cycle ends. Example: Monday 18:00 UTC-4 belongs to Tuesday's trading day.
    func tradingDayLabel(for date: Date) -> DateComponents {
        let local = calendar.dateComponents([.year, .month, .day, .hour], from: date)
        guard let localDate = calendar.date(from: local) else { return local }

        if (local.hour ?? 0) >= Self.dailyRolloverHour,
           let next = calendar.date(byAdding: .day, value: 1, to: localDate) {
            return calendar.dateComponents([.year, .month, .day], from: next)
        }
        return calendar.dateComponents([.year, .month, .day], from: localDate)
    }

    func session(for date: Date) -> LondresSession {
        let hour = calendar.component(.hour, from: date)
        switch hour {
        case 18...23:
            return .asia
        case 0...5:
            return .london
        case 6...11:
            return .newYorkAM
        case 12...17:
            return .newYorkPM
        default:
            return .outsideModel
        }
    }

    func canonicalComponents(for date: Date) -> DateComponents {
        calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
    }

    func canonicalDate(
        year: Int,
        month: Int,
        day: Int,
        hour: Int,
        minute: Int = 0
    ) -> Date? {
        var components = DateComponents()
        components.timeZone = Self.fixedUTCMinus4
        components.year = year
        components.month = month
        components.day = day
        components.hour = hour
        components.minute = minute
        return calendar.date(from: components)
    }

    func isSameTradingDay(_ lhs: Date, _ rhs: Date) -> Bool {
        tradingDayLabel(for: lhs) == tradingDayLabel(for: rhs)
    }
}
