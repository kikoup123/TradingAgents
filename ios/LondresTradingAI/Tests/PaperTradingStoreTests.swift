import XCTest
@testable import LondresTradingAI

@MainActor
final class PaperTradingStoreTests: XCTestCase {
    func testSeedsExact1000And5000Balances() {
        let store = makeStore()
        XCTAssertEqual(store.accounts.map(\.startingBalance), [1_000, 5_000])
        XCTAssertEqual(store.accounts.map(\.balance), [1_000, 5_000])
    }

    func testValidatedTwoRWinnerCompoundsBothAccountsOnFiveMinuteCandles() {
        let store = makeStore()
        let signal = makeSignal(entry: 100, stop: 90, target: 120, risk: .conservative)
        store.register(signal: signal)

        store.process(candle: candle(time: signal.createdAt.addingTimeInterval(300), open: 101, high: 105, low: 99, close: 102))
        store.process(candle: candle(time: signal.createdAt.addingTimeInterval(600), open: 110, high: 121, low: 109, close: 120))

        XCTAssertEqual(store.accounts[0].balance, 1_060, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[1].balance, 5_300, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[0].trades.first?.realizedR, 2)
        XCTAssertEqual(store.accounts[1].trades.first?.realizedR, 2)
    }

    func testStopLossRisksExactlySignalRiskTierOnFiveMinuteCandles() {
        let store = makeStore()
        let signal = makeSignal(entry: 100, stop: 90, target: 120, risk: .conservative)
        store.register(signal: signal)

        store.process(candle: candle(time: signal.createdAt.addingTimeInterval(300), open: 101, high: 104, low: 99, close: 100))
        store.process(candle: candle(time: signal.createdAt.addingTimeInterval(600), open: 95, high: 96, low: 89, close: 90))

        XCTAssertEqual(store.accounts[0].balance, 970, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[1].balance, 4_850, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[0].trades.first?.realizedR, -1)
    }

    func testSameFiveMinuteCandleEntryAndExitIsAmbiguousNotOptimistic() {
        let store = makeStore()
        let signal = makeSignal(entry: 100, stop: 90, target: 120, risk: .conservative)
        store.register(signal: signal)

        store.process(candle: candle(time: signal.createdAt.addingTimeInterval(300), open: 100, high: 121, low: 99, close: 115))

        XCTAssertEqual(store.accounts[0].balance, 1_000, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[1].balance, 5_000, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[0].trades.first?.state, .ambiguous)
        XCTAssertNil(store.accounts[0].trades.first?.profitLoss)
    }

    func testNonFiveMinuteCandlesCannotOpenOrClosePaperTrade() {
        let store = makeStore()
        let signal = makeSignal(entry: 100, stop: 90, target: 120, risk: .conservative)
        store.register(signal: signal)

        store.process(
            candle: candle(
                time: signal.createdAt.addingTimeInterval(60),
                open: 100,
                high: 121,
                low: 89,
                close: 110,
                timeframe: .oneMinute
            )
        )

        XCTAssertEqual(store.accounts[0].trades.first?.state, .waitingForEntry)
        XCTAssertEqual(store.accounts[1].trades.first?.state, .waitingForEntry)
        XCTAssertEqual(store.accounts[0].balance, 1_000, accuracy: 0.0001)
        XCTAssertEqual(store.accounts[1].balance, 5_000, accuracy: 0.0001)
    }

    func testDeterministicEntryTimeAndGeometryPreventDuplicateRegistration() {
        let store = makeStore()
        let signal = makeSignal(entry: 100, stop: 90, target: 120, risk: .conservative)
        let eventTime = signal.createdAt.addingTimeInterval(300)

        store.register(signal: signal, signaledAt: eventTime)
        let equivalentSignal = makeSignal(entry: 100, stop: 90, target: 120, risk: .conservative)
        store.register(signal: equivalentSignal, signaledAt: eventTime)

        XCTAssertEqual(store.accounts[0].trades.count, 1)
        XCTAssertEqual(store.accounts[1].trades.count, 1)
        XCTAssertEqual(store.accounts[0].trades.first?.signaledAt, eventTime)
    }

    private func makeStore() -> PaperTradingStore {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("paper-\(UUID().uuidString).json")
        return PaperTradingStore(fileURL: url)
    }

    private func makeSignal(entry: Double, stop: Double, target: Double, risk: RiskTier) -> LondresSignal {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let input = LondresSignalInput(
            context: LondresMarketContext(
                symbol: "NQ",
                direction: .bullish,
                session: .newYorkAM,
                higherTimeframeControl: .bullish,
                weeklyProfile: "Expansion",
                dailyProfile: "OHLC",
                h4Profile: "Bullish Expansion",
                liquidityNarrative: "Test",
                timestamp: now
            ),
            evidence: LondresSetupEvidence(
                smtDetected: true,
                csdConfirmed: true,
                iofAligned: true,
                timePriceValid: true,
                entryZoneValid: true,
                liquidityRaidConfirmed: true,
                mmxmNarrativeAligned: true,
                iofRange: PriceRange(low: 99, high: 101)
            ),
            geometry: TradeGeometry(entry: entry, stop: stop, target: target),
            riskTier: risk
        )
        return LondresSignalEngine().evaluate(input, now: now)
    }

    private func candle(
        time: Date,
        open: Double,
        high: Double,
        low: Double,
        close: Double,
        timeframe: LondresTimeframe = .fiveMinute
    ) -> MarketCandle {
        MarketCandle(
            symbol: "NQ",
            timeframe: timeframe,
            openTime: time,
            open: open,
            high: high,
            low: low,
            close: close,
            volume: 100
        )
    }
}
