import SwiftUI

@main
struct LondresTradingAIApp: App {
    @StateObject private var appModel = AppModel()
    @StateObject private var journalStore = JournalStore()
    @StateObject private var paperTradingStore = PaperTradingStore()
    @StateObject private var subscriptionStore = SubscriptionStore()
    @StateObject private var cTraderBrokerStore = CTraderBrokerStore()

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(appModel)
                .environmentObject(journalStore)
                .environmentObject(paperTradingStore)
                .environmentObject(subscriptionStore)
                .environmentObject(cTraderBrokerStore)
                .onOpenURL { url in
                    Task {
                        await cTraderBrokerStore.handleCallback(url)
                    }
                }
                .task {
                    await subscriptionStore.load()
                    await cTraderBrokerStore.refreshStatus()
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
