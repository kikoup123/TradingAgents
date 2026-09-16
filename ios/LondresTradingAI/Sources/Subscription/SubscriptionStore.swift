import Foundation
import StoreKit

enum SubscriptionTier: String, CaseIterable, Identifiable {
    case journal
    case analysis
    case pro

    var id: String { rawValue }

    var productID: String {
        switch self {
        case .journal:
            return "com.tradingsand.londrestradingai.journal.monthly"
        case .analysis:
            return "com.tradingsand.londrestradingai.analysis.monthly"
        case .pro:
            return "com.tradingsand.londrestradingai.pro.monthly"
        }
    }
}

@MainActor
final class SubscriptionStore: ObservableObject {
    @Published private(set) var products: [Product] = []
    @Published private(set) var activeProductIDs: Set<String> = []
    @Published private(set) var configuredIntroOfferProductIDs: Set<String> = []
    @Published private(set) var eligibleIntroOfferProductIDs: Set<String> = []
    @Published private(set) var isLoading = false

    var hasProAccess: Bool {
        activeProductIDs.contains(SubscriptionTier.pro.productID)
    }

    func hasConfiguredIntroOffer(for product: Product) -> Bool {
        configuredIntroOfferProductIDs.contains(product.id)
    }

    func isEligibleForIntroOffer(_ product: Product) -> Bool {
        eligibleIntroOfferProductIDs.contains(product.id)
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }

        do {
            let ids = SubscriptionTier.allCases.map(\.productID)
            products = try await Product.products(for: ids)
                .sorted { $0.price < $1.price }
            await refreshEntitlements()
            await refreshIntroOffers()
        } catch {
            products = []
            configuredIntroOfferProductIDs = []
            eligibleIntroOfferProductIDs = []
        }
    }

    func purchase(_ product: Product) async throws -> Bool {
        let result = try await product.purchase()

        switch result {
        case .success(let verification):
            let transaction = try verified(verification)
            await transaction.finish()
            await refreshEntitlements()
            await refreshIntroOffers()
            return true
        case .pending, .userCancelled:
            return false
        @unknown default:
            return false
        }
    }

    func refreshEntitlements() async {
        var active = Set<String>()

        for await result in Transaction.currentEntitlements {
            guard case .verified(let transaction) = result else { continue }
            guard transaction.revocationDate == nil else { continue }
            active.insert(transaction.productID)
        }

        activeProductIDs = active
    }

    func refreshIntroOffers() async {
        var configured = Set<String>()
        var eligible = Set<String>()

        for product in products {
            guard let subscription = product.subscription else { continue }
            guard let offer = subscription.introductoryOffer else { continue }
            guard offer.paymentMode == .freeTrial else { continue }

            configured.insert(product.id)
            if await subscription.isEligibleForIntroOffer {
                eligible.insert(product.id)
            }
        }

        configuredIntroOfferProductIDs = configured
        eligibleIntroOfferProductIDs = eligible
    }

    private func verified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .verified(let value):
            return value
        case .unverified:
            throw SubscriptionError.failedVerification
        }
    }
}

enum SubscriptionError: Error {
    case failedVerification
}
