import SwiftUI

@main
struct LondresTradingAIApp: App {
    @StateObject private var appModel = AppModel()
    @StateObject private var journalStore = JournalStore()
    @StateObject private var paperTradingStore = PaperTradingStore()
    @StateObject private var subscriptionStore = SubscriptionStore()

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(appModel)
                .environmentObject(journalStore)
                .environmentObject(paperTradingStore)
                .environmentObject(subscriptionStore)
                .task {
                    await subscriptionStore.load()
                    await MainActor.run {
                        appModel.startMarketData(
                            paperTradingStore: paperTradingStore,
                            plan: .nq,
                            refreshInterval: 30
                        )
                    }
                }
        }
    }
}
