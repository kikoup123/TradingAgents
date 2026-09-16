import Foundation

struct LondresInstrumentMetadata: Codable, Hashable, Sendable {
    let symbol: String
    let tickSize: Double?
}

struct LondresDemoExecutionPolicy: Codable, Hashable, Sendable {
    let riskTier: RiskTier
    let preferredStopSource: StopSource
    let targetMode: TraderExitMode
    let stopBufferTicks: Int

    static let conservative = LondresDemoExecutionPolicy(
        riskTier: .conservative,
        preferredStopSource: .iofRange,
        targetMode: .fullAtSD2_5,
        stopBufferTicks: 1
    )
}

struct LondresStrategyInput: Sendable {
    let timeframeBars: [String: [MarketCandle]]
    let intradayBars: [MarketCandle]
    let minuteBars: [MarketCandle]
    let csdBars: [MarketCandle]
    let smtBars: [String: [MarketCandle]]
    let smtGroup: String
    let smtTimeframe: String
    let csdTimeframe: String
    let hierarchy: [String]
    let liquidityTimeframe: String
    let weeklyProfileTimeframe: String
    let weeklyControlTimeframe: String
    let dailyOrderFlowTimeframe: String
    let h4OrderFlowTimeframe: String
    let protectedWeeklyExtreme: Bool?
    let protectedDailyExtreme: Bool?
    let opposingDrawReached: Bool?
    let thursdayExternalManipulation: Bool?
    let reversalConfirmedAt: Date?
    let h4LocationContext: H4LocationContext
    let asOf: Date?
    let currentPrice: Double?
    let instrument: LondresInstrumentMetadata
    let demoPolicy: LondresDemoExecutionPolicy

    init(
        timeframeBars: [String: [MarketCandle]],
        intradayBars: [MarketCandle],
        minuteBars: [MarketCandle],
        csdBars: [MarketCandle],
        smtBars: [String: [MarketCandle]],
        smtGroup: String,
        smtTimeframe: String,
        csdTimeframe: String,
        hierarchy: [String] = NarrativeEngine.defaultHierarchy,
        liquidityTimeframe: String = "4H",
        weeklyProfileTimeframe: String = "1D",
        weeklyControlTimeframe: String = "4H",
        dailyOrderFlowTimeframe: String = "1D",
        h4OrderFlowTimeframe: String = "4H",
        protectedWeeklyExtreme: Bool? = nil,
        protectedDailyExtreme: Bool? = nil,
        opposingDrawReached: Bool? = nil,
        thursdayExternalManipulation: Bool? = nil,
        reversalConfirmedAt: Date? = nil,
        h4LocationContext: H4LocationContext = .unknown,
        asOf: Date? = nil,
        currentPrice: Double? = nil,
        instrument: LondresInstrumentMetadata,
        demoPolicy: LondresDemoExecutionPolicy = .conservative
    ) {
        self.timeframeBars = timeframeBars
        self.intradayBars = intradayBars
        self.minuteBars = minuteBars
        self.csdBars = csdBars
        self.smtBars = smtBars
        self.smtGroup = smtGroup
        self.smtTimeframe = smtTimeframe
        self.csdTimeframe = csdTimeframe
        self.hierarchy = hierarchy
        self.liquidityTimeframe = liquidityTimeframe
        self.weeklyProfileTimeframe = weeklyProfileTimeframe
        self.weeklyControlTimeframe = weeklyControlTimeframe
        self.dailyOrderFlowTimeframe = dailyOrderFlowTimeframe
        self.h4OrderFlowTimeframe = h4OrderFlowTimeframe
        self.protectedWeeklyExtreme = protectedWeeklyExtreme
        self.protectedDailyExtreme = protectedDailyExtreme
        self.opposingDrawReached = opposingDrawReached
        self.thursdayExternalManipulation = thursdayExternalManipulation
        self.reversalConfirmedAt = reversalConfirmedAt
        self.h4LocationContext = h4LocationContext
        self.asOf = asOf
        self.currentPrice = currentPrice
        self.instrument = instrument
        self.demoPolicy = demoPolicy
    }
}

struct LondresNarrativeGate: Codable, Hashable, Sendable {
    let qualified: Bool
    let direction: DirectionalControl
    let sameDirection: Bool
    let sameTimeframe: Bool
    let sameStream: Bool
    let continuationDraw: Bool
    let reasonCodes: [String]
}

struct LondresStrategyResult: Sendable {
    let weeklyProfile: WeeklyProfileResult
    let dailyProfile: DailyProfileResult
    let h4Profile: H4ProfileResult
    let timePrice: TimePriceResult
    let liquidity: LiquidityResult
    let csd: CSDResult
    let validationCSD: CSDEvent?
    let postCSDIOFC: IOFCResult?
    let smt: SMTResult
    let narrative: NarrativeResult
    let narrativeGate: LondresNarrativeGate
    let mmxmModels: [String: MMXMDetection]
    let executionMMXM: MMXMDetection?
    let mmxmEntryContract: MMXMEntryContract?
    let tradePlan: TradePlanContext?
    let stopOptions: StopSelectionContext?
    let executableStop: ExecutableStopResult?
    let targetManagement: TargetManagementContext?
    let selectedTarget: SelectedTargetManagement?
    let entryExecution: EntryExecutionContext?
    let signal: LondresSignal?
    let blockerCodes: [String]
}

enum LondresStrategyPipelineError: Error, Equatable {
    case missingTimeframe(String)
    case noExecutionBars
    case instrumentSymbolMismatch
}

struct LondresStrategyPipeline: Sendable {
    func analyze(_ input: LondresStrategyInput) throws -> LondresStrategyResult {
        let timeframeBars = clipped(input.timeframeBars, asOf: input.asOf)
        let intradayBars = clipped(input.intradayBars, asOf: input.asOf)
        let minuteBars = clipped(input.minuteBars, asOf: input.asOf)
        let csdBars = clipped(input.csdBars, asOf: input.asOf)
        let smtBars = clipped(input.smtBars, asOf: input.asOf)

        for required in [input.weeklyProfileTimeframe, input.weeklyControlTimeframe, input.liquidityTimeframe] {
            guard timeframeBars[required] != nil else { throw LondresStrategyPipelineError.missingTimeframe(required) }
        }

        var orderFlow: [String: OrderFlowResult] = [:]
        for (timeframe, bars) in timeframeBars {
            orderFlow[timeframe] = try OrderFlowEngine().analyze(bars: bars, timeframe: timeframe)
        }
        let weeklyControl = orderFlow[input.weeklyControlTimeframe]?.control.directionalControl ?? .unconfirmed
        let dailyControl = orderFlow[input.dailyOrderFlowTimeframe]?.control.directionalControl ?? .unconfirmed
        let h4Control = orderFlow[input.h4OrderFlowTimeframe]?.control.directionalControl ?? .unconfirmed

        let weekly = try WeeklyProfileEngine().analyze(
            dailyBars: timeframeBars[input.weeklyProfileTimeframe]!,
            htfControl: weeklyControl,
            protectedWeeklyExtreme: input.protectedWeeklyExtreme,
            opposingDrawReached: input.opposingDrawReached,
            thursdayExternalManipulation: input.thursdayExternalManipulation
        )
        let daily = try DailyProfileEngine().analyze(
            intradayBars: intradayBars,
            weeklyProfile: weekly,
            dailyOrderFlowControl: dailyControl,
            protectedDailyExtreme: input.protectedDailyExtreme,
            opposingDrawReached: input.opposingDrawReached
        )
        let h4 = try H4ProfileEngine().analyze(
            intradayBars: intradayBars,
            dailyProfile: daily,
            h4OrderFlowControl: h4Control,
            reversalConfirmedAt: input.reversalConfirmedAt,
            locationContext: input.h4LocationContext
        )
        let timePrice = try TimePriceEngine().analyze(
            minuteBars: minuteBars,
            asOf: input.asOf,
            currentPrice: input.currentPrice
        )
        let liquidityFlow = orderFlow[input.liquidityTimeframe]?.control.directionalControl ?? .unconfirmed
        let liquidity = try LiquidityEngine().analyze(
            bars: timeframeBars[input.liquidityTimeframe]!,
            timeframe: input.liquidityTimeframe,
            orderFlowControl: liquidityFlow,
            timePriceState: timePrice
        )

        let csd = try CSDEngine().analyze(
            bars: csdBars,
            timeframe: input.csdTimeframe,
            asOf: input.asOf
        )
        let smtProbe = try SMTEngine().analyze(
            instrumentBars: smtBars,
            group: input.smtGroup,
            timeframe: input.smtTimeframe,
            asOf: input.asOf
        )
        let validationCSD = validationCSD(
            events: csd.events,
            smtDetected: smtProbe.detected,
            smtReferenceTime: smtProbe.referenceTime
        )
        let postCSDIOFC = try postCSDIOFC(csdBars: csdBars, event: validationCSD)
        let validatedCSDDirection = validationCSD?.direction ?? .unconfirmed
        let validatedIOFDirection: OrderFlowDirection = postCSDIOFC?.confirmed == true
            ? orderFlowDirection(postCSDIOFC!.expectedDirection)
            : .unconfirmed
        let smt = try SMTEngine().analyze(
            instrumentBars: smtBars,
            group: input.smtGroup,
            timeframe: input.smtTimeframe,
            csdDirection: validatedCSDDirection,
            iofDirection: validatedIOFDirection,
            asOf: input.asOf
        )

        let narrative = try NarrativeEngine().analyze(
            timeframeBars: timeframeBars,
            hierarchy: input.hierarchy,
            asOf: input.asOf
        )
        let narrativeGate = buildNarrativeGate(
            narrative: narrative,
            smt: smt,
            csdTimeframe: input.csdTimeframe,
            narrativeBars: timeframeBars[narrative.executionTimeframe] ?? [],
            executionBars: csdBars
        )
        let mmxmModels = try MMXMEngine().analyzeHierarchy(
            timeframeBars: timeframeBars,
            narrative: narrative,
            asOf: input.asOf
        )
        let executionMMXM = mmxmModels[narrative.executionTimeframe]
        let executionDirection = directionalControl(smt.direction)
        let mmxmEntry = executionMMXM.map {
            MMXMEngine().entryContract(
                model: $0,
                narrativeQualified: narrativeGate.qualified,
                executionSMTValidated: smt.validated,
                executionDirection: executionDirection
            )
        }

        let executionBars = timeframeBars[narrative.executionTimeframe] ?? []
        guard !executionBars.isEmpty else { throw LondresStrategyPipelineError.noExecutionBars }
        guard executionBars.allSatisfy({ $0.symbol == input.instrument.symbol }) else {
            throw LondresStrategyPipelineError.instrumentSymbolMismatch
        }

        let tradePlan: TradePlanContext?
        if let executionMMXM, let mmxmEntry {
            tradePlan = try TradePlanEngine().analyze(
                bars: executionBars,
                timeframe: narrative.executionTimeframe,
                narrative: narrative,
                mmxm: executionMMXM,
                entryContract: mmxmEntry,
                asOf: input.asOf
            )
        } else {
            tradePlan = nil
        }

        let localFlow = narrative.timeframes[narrative.executionTimeframe]?.orderFlow
        let stopOptions: StopSelectionContext?
        if let validationCSD, let postCSDIOFC, let localFlow, let direction = marketDirection(executionDirection) {
            stopOptions = StopSelectionEngine().analyze(
                direction: direction,
                currentPrice: executionBars[executionBars.count - 1].close,
                orderFlow: localFlow,
                validationCSD: validationCSD,
                postCSDIOFC: postCSDIOFC
            )
        } else {
            stopOptions = nil
        }

        let selectedStop: ValidatedStopSelection?
        if let stopOptions {
            selectedStop = StopSelectionEngine().validateSelection(
                stopOptions,
                selectedSource: input.demoPolicy.preferredStopSource
            )
        } else {
            selectedStop = nil
        }

        let targetManagement = CSDTargetEngine().analyze(
            validationCSD: validationCSD,
            htfLiquidity: liquidity
        )
        let selectedTarget = try? CSDTargetEngine().select(
            targetManagement,
            mode: input.demoPolicy.targetMode
        )
        let entryExecution: EntryExecutionContext?
        if let postCSDIOFC {
            entryExecution = try PostCSDIOFEntryEngine().analyze(
                iofc: postCSDIOFC,
                executionBars: executionBars,
                executionDirection: marketDirection(executionDirection),
                asOf: input.asOf
            )
        } else {
            entryExecution = nil
        }

        let executableStop = entryExecution.map {
            ExecutableStopEngine().calculate(
                entryExecution: $0,
                stopSelection: selectedStop,
                tickSize: input.instrument.tickSize,
                bufferTicks: input.demoPolicy.stopBufferTicks
            )
        }

        var blockers: [String] = []
        if !smt.detected { blockers.append("WAIT_FOR_SMT") }
        if validationCSD == nil { blockers.append("WAIT_FOR_CSD_AFTER_SMT") }
        if postCSDIOFC?.confirmed != true { blockers.append("WAIT_FOR_POST_CSD_IOFC") }
        if !narrativeGate.qualified { blockers.append(contentsOf: narrativeGate.reasonCodes) }
        if mmxmEntry?.state != .reversalReady && mmxmEntry?.state != .continuationReady {
            blockers.append("WAIT_FOR_MMXM_ENTRY_STATE")
        }
        if tradePlan?.state != .readyForEntrySelection { blockers.append("WAIT_FOR_STRUCTURAL_TRADE_PLAN") }
        if entryExecution?.entryTriggered != true { blockers.append("WAIT_FOR_FIRST_IOF_RETURN") }
        if executableStop?.status != .ready { blockers.append("WAIT_FOR_EXECUTABLE_STOP") }
        if selectedTarget == nil { blockers.append("WAIT_FOR_SELECTED_CSD_TARGET") }

        let signal = buildSignal(
            input: input,
            weekly: weekly,
            daily: daily,
            h4: h4,
            timePrice: timePrice,
            liquidity: liquidity,
            smt: smt,
            validationCSD: validationCSD,
            postCSDIOFC: postCSDIOFC,
            narrative: narrative,
            narrativeGate: narrativeGate,
            mmxmEntry: mmxmEntry,
            tradePlan: tradePlan,
            entryExecution: entryExecution,
            executableStop: executableStop,
            selectedTarget: selectedTarget
        )

        return LondresStrategyResult(
            weeklyProfile: weekly,
            dailyProfile: daily,
            h4Profile: h4,
            timePrice: timePrice,
            liquidity: liquidity,
            csd: csd,
            validationCSD: validationCSD,
            postCSDIOFC: postCSDIOFC,
            smt: smt,
            narrative: narrative,
            narrativeGate: narrativeGate,
            mmxmModels: mmxmModels,
            executionMMXM: executionMMXM,
            mmxmEntryContract: mmxmEntry,
            tradePlan: tradePlan,
            stopOptions: stopOptions,
            executableStop: executableStop,
            targetManagement: targetManagement,
            selectedTarget: selectedTarget,
            entryExecution: entryExecution,
            signal: signal,
            blockerCodes: Array(Set(blockers)).sorted()
        )
    }

    private func validationCSD(
        events: [CSDEvent],
        smtDetected: Bool,
        smtReferenceTime: Date?
    ) -> CSDEvent? {
        guard smtDetected, let cutoff = smtReferenceTime else { return nil }
        return events.filter { $0.confirmationTime >= cutoff }.last
    }

    private func postCSDIOFC(
        csdBars: [MarketCandle],
        event: CSDEvent?
    ) throws -> IOFCResult? {
        guard let event else { return nil }
        let data = csdBars.sorted { $0.openTime < $1.openTime }
        let after = data.enumerated().filter { $0.offset > event.confirmationPosition }.map(\.element)
        let broken = event.direction == .bullish
            ? after.contains { $0.close < event.protectedExtreme }
            : after.contains { $0.close > event.protectedExtreme }
        guard !broken else {
            return IOFCResult(
                expectedDirection: event.direction == .bullish ? .bullish : .bearish,
                confirmed: false,
                confirmationRange: nil,
                reason: "CSD_PROTECTED_EXTREME_INVALIDATED"
            )
        }
        guard let direction = event.direction.marketDirection else { return nil }
        return try OrderFlowEngine().findIOFCAfter(
            bars: data,
            anchorPosition: event.confirmationPosition,
            expectedDirection: direction
        )
    }

    private func buildNarrativeGate(
        narrative: NarrativeResult,
        smt: SMTResult,
        csdTimeframe: String,
        narrativeBars: [MarketCandle],
        executionBars: [MarketCandle]
    ) -> LondresNarrativeGate {
        guard let local = narrative.timeframes[narrative.executionTimeframe] else {
            return LondresNarrativeGate(
                qualified: false,
                direction: directionalControl(smt.direction),
                sameDirection: false,
                sameTimeframe: false,
                sameStream: false,
                continuationDraw: false,
                reasonCodes: ["WAIT_FOR_ALIGNED_NARRATIVE_AND_BOUNDED_DRAW"]
            )
        }
        let direction = directionalControl(smt.direction)
        let sameDirection = direction.rawValue == local.control.rawValue
            && local.control == narrative.bias
        let sameTimeframe = narrative.executionTimeframe == csdTimeframe
        let sameStream = sameTimeframe
            && narrativeBars.sorted(by: { $0.openTime < $1.openTime })
                == executionBars.sorted(by: { $0.openTime < $1.openTime })
        let continuationDraw = local.narrativeDraw?.purpose == "LIQUIDITY_OBJECTIVE"
        let qualified = smt.validated
            && narrative.contextConfirmed
            && sameDirection
            && sameStream
            && continuationDraw
        var reasons: [String] = []
        if !smt.validated { reasons.append("WAIT_FOR_SMT_CSD_POST_CSD_IOFC") }
        if !narrative.contextConfirmed { reasons.append("WAIT_FOR_ALIGNED_NARRATIVE_AND_BOUNDED_DRAW") }
        if !sameDirection { reasons.append("NARRATIVE_EXECUTION_DIRECTION_CONFLICT") }
        if !sameStream { reasons.append("NARRATIVE_EXECUTION_STREAM_MISMATCH") }
        if !continuationDraw { reasons.append("NO_CONTINUATION_LIQUIDITY_OBJECTIVE") }
        return LondresNarrativeGate(
            qualified: qualified,
            direction: direction,
            sameDirection: sameDirection,
            sameTimeframe: sameTimeframe,
            sameStream: sameStream,
            continuationDraw: continuationDraw,
            reasonCodes: reasons
        )
    }

    private func buildSignal(
        input: LondresStrategyInput,
        weekly: WeeklyProfileResult,
        daily: DailyProfileResult,
        h4: H4ProfileResult,
        timePrice: TimePriceResult,
        liquidity: LiquidityResult,
        smt: SMTResult,
        validationCSD: CSDEvent?,
        postCSDIOFC: IOFCResult?,
        narrative: NarrativeResult,
        narrativeGate: LondresNarrativeGate,
        mmxmEntry: MMXMEntryContract?,
        tradePlan: TradePlanContext?,
        entryExecution: EntryExecutionContext?,
        executableStop: ExecutableStopResult?,
        selectedTarget: SelectedTargetManagement?
    ) -> LondresSignal? {
        let directionControl = mmxmEntry?.direction ?? directionalControl(smt.direction)
        guard let direction = marketDirection(directionControl) else { return nil }
        let higherControl = marketDirection(narrative.bias.directionalControl) ?? direction
        let entry = entryExecution?.exactEntryPrice
        let stop = executableStop?.executableStopPrice
        let target = selectedTarget?.originalTargetPrice
        let geometry: TradeGeometry? = if let entry, let stop, let target {
            TradeGeometry(entry: entry, stop: stop, target: target)
        } else {
            nil
        }
        let context = LondresMarketContext(
            symbol: input.instrument.symbol,
            direction: direction,
            session: session(at: timePrice.asOf),
            higherTimeframeControl: higherControl,
            weeklyProfile: weekly.profile.rawValue,
            dailyProfile: "\(daily.dayType.rawValue)|\(daily.expectedDelivery.rawValue)|\(daily.phase.rawValue)",
            h4Profile: "\(h4.profile.rawValue)|\(h4.phase.rawValue)",
            liquidityNarrative: liquidity.activeDraw.map { "\($0.side.rawValue)|\($0.liquidityClass.rawValue)|\($0.sourceKind)" } ?? "UNRESOLVED",
            timestamp: timePrice.asOf
        )
        let iofRange = postCSDIOFC?.confirmationRange.map { PriceRange(low: $0.low, high: $0.high) }
        let timePriceValid = timePrice.canonicalClock == "UTC-4_FIXED"
            && daily.status != .invalidated
            && h4.status != .invalidated
        let entryZoneValid = entryExecution?.entryTriggered == true
            && narrativeGate.qualified
            && (mmxmEntry?.state == .reversalReady || mmxmEntry?.state == .continuationReady)
            && tradePlan?.state == .readyForEntrySelection
        let evidence = LondresSetupEvidence(
            smtDetected: smt.detected,
            csdConfirmed: validationCSD != nil,
            iofAligned: postCSDIOFC?.confirmed == true,
            timePriceValid: timePriceValid,
            entryZoneValid: entryZoneValid,
            liquidityRaidConfirmed: validationCSD != nil,
            mmxmNarrativeAligned: narrativeGate.qualified,
            iofRange: iofRange
        )
        return LondresSignalEngine().evaluate(
            LondresSignalInput(
                context: context,
                evidence: evidence,
                geometry: geometry,
                riskTier: input.demoPolicy.riskTier
            ),
            now: timePrice.asOf
        )
    }

    private func clipped(_ bars: [MarketCandle], asOf: Date?) -> [MarketCandle] {
        let sorted = bars.sorted { $0.openTime < $1.openTime }
        guard let asOf else { return sorted }
        return sorted.filter { $0.openTime <= asOf }
    }

    private func clipped(
        _ input: [String: [MarketCandle]],
        asOf: Date?
    ) -> [String: [MarketCandle]] {
        input.mapValues { clipped($0, asOf: asOf) }
    }

    private func directionalControl(_ direction: CSDDirection) -> DirectionalControl {
        if direction == .bullish { return .bullish }
        if direction == .bearish { return .bearish }
        return .unconfirmed
    }

    private func orderFlowDirection(_ direction: MarketDirection) -> OrderFlowDirection {
        direction == .bullish ? .bullish : .bearish
    }

    private func marketDirection(_ direction: DirectionalControl) -> MarketDirection? {
        if direction == .bullish { return .bullish }
        if direction == .bearish { return .bearish }
        return nil
    }

    private func session(at date: Date) -> LondresSession {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = LondresClock.fixedUTCMinus4
        let hour = calendar.component(.hour, from: date)
        switch hour {
        case 18...23: return .asia
        case 0...5: return .london
        case 6...11: return .newYorkAM
        case 12...17: return .newYorkPM
        default: return .outsideModel
        }
    }
}
