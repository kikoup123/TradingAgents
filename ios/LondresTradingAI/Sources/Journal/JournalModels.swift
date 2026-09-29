import Foundation

enum TradeOutcome: String, Codable, CaseIterable, Sendable {
    case open
    case win
    case loss
    case breakeven
    case skipped
}

enum DisciplineTag: String, Codable, CaseIterable, Identifiable, Sendable {
    case followedPlan
    case earlyEntry
    case fomo
    case overtrade
    case movedStop
    case closedWinnerEarly
    case outsideSession
    case revengeTrade
    case oversizedRisk
    case manualOverride

    var id: String { rawValue }
}

enum TraderEmotion: String, Codable, CaseIterable, Identifiable, Sendable {
    case calm
    case focused
    case confident
    case hesitant
    case anxious
    case fearful
    case frustrated
    case euphoric
    case impatient

    var id: String { rawValue }
}

struct JournalExecution: Codable, Hashable, Sendable {
    let actualEntry: Double?
    let actualExit: Double?
    let realizedR: Double?
    let monetaryPnL: Double?
    let mfeR: Double?
    let maeR: Double?
    let openedAt: Date?
    let closedAt: Date?
}

struct JournalReview: Codable, Hashable, Sendable {
    let preTradeNotes: String
    let postTradeNotes: String
    let emotions: Set<TraderEmotion>
    let disciplineTags: Set<DisciplineTag>
    let confidence: Int
    let planCompliance: Int

    init(
        preTradeNotes: String = "",
        postTradeNotes: String = "",
        emotions: Set<TraderEmotion> = [],
        disciplineTags: Set<DisciplineTag> = [],
        confidence: Int = 50,
        planCompliance: Int = 100
    ) {
        self.preTradeNotes = preTradeNotes
        self.postTradeNotes = postTradeNotes
        self.emotions = emotions
        self.disciplineTags = disciplineTags
        self.confidence = min(max(confidence, 0), 100)
        self.planCompliance = min(max(planCompliance, 0), 100)
    }
}

struct JournalTrade: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    let signal: LondresSignal
    var outcome: TradeOutcome
    var execution: JournalExecution
    var review: JournalReview
    var playbookModel: String
    var screenshotReferences: [String]
    let createdAt: Date
    var updatedAt: Date

    init(
        id: UUID = UUID(),
        signal: LondresSignal,
        outcome: TradeOutcome = .open,
        execution: JournalExecution = JournalExecution(
            actualEntry: nil,
            actualExit: nil,
            realizedR: nil,
            monetaryPnL: nil,
            mfeR: nil,
            maeR: nil,
            openedAt: nil,
            closedAt: nil
        ),
        review: JournalReview = JournalReview(),
        playbookModel: String = "SMT + CSD + IOF First Return",
        screenshotReferences: [String] = [],
        createdAt: Date = Date(),
        updatedAt: Date = Date()
    ) {
        self.id = id
        self.signal = signal
        self.outcome = outcome
        self.execution = execution
        self.review = review
        self.playbookModel = playbookModel
        self.screenshotReferences = screenshotReferences
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}
