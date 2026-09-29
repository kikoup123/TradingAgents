import Foundation

/// User-selected presentation language. BCP-47 keeps the app open to any locale
/// supported by iOS or the configured AI provider instead of hard-coding a short list.
struct AppLanguage: Codable, Hashable, Identifiable, Sendable {
    let identifier: String

    var id: String { identifier }
    var locale: Locale { Locale(identifier: identifier) }

    var displayName: String {
        Locale.current.localizedString(forIdentifier: identifier) ?? identifier
    }

    static let english = AppLanguage(identifier: "en")
    static let spanish = AppLanguage(identifier: "es")
    static let finnish = AppLanguage(identifier: "fi")
    static let system = AppLanguage(identifier: Locale.current.identifier)
}

enum TradingTerminologyMode: String, Codable, CaseIterable, Sendable {
    /// Preserve ICT/Londres terms exactly as authored even when prose is translated.
    case preserveEnglishTerms
    /// Allow approved localized labels while keeping canonical identifiers internally.
    case localizedApprovedTerms
}

struct LocalizationPreferences: Codable, Hashable, Sendable {
    var interfaceLanguage: AppLanguage
    var aiLanguage: AppLanguage
    var notificationLanguage: AppLanguage
    var journalLanguage: AppLanguage
    var terminologyMode: TradingTerminologyMode

    static let `default` = LocalizationPreferences(
        interfaceLanguage: .system,
        aiLanguage: .english,
        notificationLanguage: .system,
        journalLanguage: .system,
        terminologyMode: .preserveEnglishTerms
    )
}

enum CanonicalTradingTerm: String, CaseIterable, Codable, Sendable {
    case smt = "SMT"
    case csd = "CSD"
    case iof = "IOF"
    case iofc = "IOFC"
    case mmxm = "MMXM"
    case fairValueGap = "Fair Value Gap"
    case orderBlock = "Order Block"
    case liquidityRaid = "Liquidity Raid"
    case protectedSwing = "Protected Swing"
    case marketMakerModel = "Market Maker Model"
    case dealingRange = "Dealing Range"

    func displayName(
        language: AppLanguage,
        mode: TradingTerminologyMode
    ) -> String {
        guard mode == .localizedApprovedTerms else { return rawValue }

        let approvedSpanish: [CanonicalTradingTerm: String] = [
            .liquidityRaid: "Barrido de liquidez",
            .protectedSwing: "Swing protegido",
            .dealingRange: "Rango de negociación"
        ]
        let approvedFinnish: [CanonicalTradingTerm: String] = [
            .liquidityRaid: "Likviditeetin pyyhkäisy",
            .protectedSwing: "Suojattu swing",
            .dealingRange: "Kaupankäyntialue"
        ]

        if language.identifier.hasPrefix("es") {
            return approvedSpanish[self] ?? rawValue
        }
        if language.identifier.hasPrefix("fi") {
            return approvedFinnish[self] ?? rawValue
        }
        return rawValue
    }
}
