import Foundation

@MainActor
final class JournalStore: ObservableObject {
    @Published private(set) var trades: [JournalTrade] = []
    @Published private(set) var lastError: String?

    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(fileURL: URL? = nil) {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let folder = base.appendingPathComponent("LondresTradingAI", isDirectory: true)
        self.fileURL = fileURL ?? folder.appendingPathComponent("journal.json")

        encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601

        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        load()
    }

    func add(signal: LondresSignal, playbookModel: String = "SMT + CSD + IOF First Return") {
        let trade = JournalTrade(signal: signal, playbookModel: playbookModel)
        trades.insert(trade, at: 0)
        persist()
    }

    func upsert(_ trade: JournalTrade) {
        if let index = trades.firstIndex(where: { $0.id == trade.id }) {
            trades[index] = trade
        } else {
            trades.insert(trade, at: 0)
        }
        persist()
    }

    func delete(at offsets: IndexSet) {
        trades.remove(atOffsets: offsets)
        persist()
    }

    func clear() {
        trades.removeAll()
        persist()
    }

    private func load() {
        do {
            guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
            let data = try Data(contentsOf: fileURL)
            trades = try decoder.decode([JournalTrade].self, from: data)
            lastError = nil
        } catch {
            lastError = "Journal load failed: \(error.localizedDescription)"
            trades = []
        }
    }

    private func persist() {
        do {
            let directory = fileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try encoder.encode(trades)
            try data.write(to: fileURL, options: .atomic)
            lastError = nil
        } catch {
            lastError = "Journal save failed: \(error.localizedDescription)"
        }
    }
}
