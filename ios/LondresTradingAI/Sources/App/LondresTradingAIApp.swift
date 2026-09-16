import SwiftUI

@main
struct LondresTradingAIApp: App {
    @StateObject private var appModel = AppModel()
    @StateObject private var journalStore = JournalStore()
    @StateObject private var subscriptionStore = SubscriptionStore()

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(appModel)
                .environmentObject(journalStore)
                .environmentObject(subscriptionStore)
                .task {
                    await subscriptionStore.load()
                }
        }
    }
}
