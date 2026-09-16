import Foundation

enum TargetManagementStatus: String, Codable, Hashable, Sendable {
    case ready = "READY"
    case waitForValidatedCSD = "WAIT_FOR_VALIDATED_CSD"
    case invalidCSDRange = "INVALID_CSD_RANGE"
}

enum TraderExitMode: String, Codable, Hashable, Sendable {
    case fullAtSD2 = "FULL_AT_SD_2"
    case fullAtSD2_5 = "FULL_AT_SD_2_5"
    case holdHTFLiquidity = "HOLD_HTF_LIQUIDITY"
    case none = "NONE"
}

struct SDTarget: Codable, Hashable, Sendable {
    let label: String
    let multiplier: Double
    let price: Double
}

struct HTFRunnerTarget: Codable, Hashable, Sendable {
    let price: Double
    let side: LiquiditySide
    let timeframe: String
    let sourceKind: String
    let sourceTime: Date?
}

struct TargetManagementContext: Codable, Hashable, Sendable {
    let status: TargetManagementStatus
    let direction: MarketDirection?
    let csdLevel: Double?
    let protectedExtreme: Double?
    let rangeSize: Double?
    let sd2: SDTarget?
    let sd2_5: SDTarget?
    let htfRunnerTarget: HTFRunnerTarget?
    let htfLiquidityTimeframe: String?
    let holdAvailable: Bool
    let reasonCodes: [String]

    var selectionRequired: Bool { status == .ready }
}

struct SelectedTargetManagement: Codable, Hashable, Sendable {
    let selectedExitMode: TraderExitMode
    let originalTargetPrice: Double
    let selectedSDTarget: SDTarget?
    let holdForHTFLiquidity: Bool
    let partialTrigger: SDTarget?
    let partialFraction: Double
    let runnerFraction: Double
    let runnerTarget: HTFRunnerTarget?
}

enum TargetManagementError: Error, Equatable {
    case notReady
    case unavailableExitMode
}

struct CSDTargetEngine: Sendable {
    func analyze(
        validationCSD: CSDEvent?,
        htfLiquidity: LiquidityResult? = nil
    ) -> TargetManagementContext {
        guard let csd = validationCSD,
              let direction = csd.direction.marketDirection else {
            return TargetManagementContext(
                status: .waitForValidatedCSD,
                direction: validationCSD?.direction.marketDirection,
                csdLevel: validationCSD?.thresholdOpen,
                protectedExtreme: validationCSD?.protectedExtreme,
                rangeSize: nil,
                sd2: nil,
                sd2_5: nil,
                htfRunnerTarget: nil,
                htfLiquidityTimeframe: htfLiquidity?.timeframe,
                holdAvailable: false,
                reasonCodes: ["VALIDATED_SMT_CSD_RANGE_REQUIRED"]
            )
        }

        let csdLevel = csd.thresholdOpen
        let protected = csd.protectedExtreme
        let rangeSize = direction == .bearish
            ? protected - csdLevel
            : csdLevel - protected
        guard rangeSize > 0 else {
            return TargetManagementContext(
                status: .invalidCSDRange,
                direction: direction,
                csdLevel: csdLevel,
                protectedExtreme: protected,
                rangeSize: rangeSize,
                sd2: nil,
                sd2_5: nil,
                htfRunnerTarget: nil,
                htfLiquidityTimeframe: htfLiquidity?.timeframe,
                holdAvailable: false,
                reasonCodes: ["CSD_PROTECTED_EXTREME_DIRECTIONAL_GEOMETRY_INVALID"]
            )
        }

        let sign = direction == .bearish ? -1.0 : 1.0
        let sd2 = SDTarget(
            label: "-2",
            multiplier: 2.0,
            price: csdLevel + sign * 2.0 * rangeSize
        )
        let sd2_5 = SDTarget(
            label: "-2.5",
            multiplier: 2.5,
            price: csdLevel + sign * 2.5 * rangeSize
        )
        let runner = htfRunnerTarget(
            liquidity: htfLiquidity,
            direction: direction,
            beyondPrice: sd2_5.price
        )
        var reasons = [
            "CSD_THRESHOLD_IS_ZERO_REFERENCE",
            "PROTECTED_EXTREME_DEFINES_ONE_RANGE_UNIT",
            "SD_2_AND_SD_2_5_PROJECTED_IN_TRADE_DIRECTION"
        ]
        reasons.append(
            runner == nil
                ? "HTF_EXTERNAL_RUNNER_LIQUIDITY_UNAVAILABLE"
                : "HTF_EXTERNAL_RUNNER_LIQUIDITY_AVAILABLE_BEYOND_SD_2_5"
        )

        return TargetManagementContext(
            status: .ready,
            direction: direction,
            csdLevel: csdLevel,
            protectedExtreme: protected,
            rangeSize: rangeSize,
            sd2: sd2,
            sd2_5: sd2_5,
            htfRunnerTarget: runner,
            htfLiquidityTimeframe: htfLiquidity?.timeframe,
            holdAvailable: runner != nil,
            reasonCodes: reasons
        )
    }

    func select(
        _ plan: TargetManagementContext,
        mode: TraderExitMode
    ) throws -> SelectedTargetManagement {
        guard plan.status == .ready else { throw TargetManagementError.notReady }

        switch mode {
        case .fullAtSD2:
            guard let target = plan.sd2 else { throw TargetManagementError.unavailableExitMode }
            return SelectedTargetManagement(
                selectedExitMode: mode,
                originalTargetPrice: target.price,
                selectedSDTarget: target,
                holdForHTFLiquidity: false,
                partialTrigger: nil,
                partialFraction: 0,
                runnerFraction: 0,
                runnerTarget: nil
            )
        case .fullAtSD2_5:
            guard let target = plan.sd2_5 else { throw TargetManagementError.unavailableExitMode }
            return SelectedTargetManagement(
                selectedExitMode: mode,
                originalTargetPrice: target.price,
                selectedSDTarget: target,
                holdForHTFLiquidity: false,
                partialTrigger: nil,
                partialFraction: 0,
                runnerFraction: 0,
                runnerTarget: nil
            )
        case .holdHTFLiquidity:
            guard let runner = plan.htfRunnerTarget,
                  let partial = plan.sd2_5,
                  plan.holdAvailable else {
                throw TargetManagementError.unavailableExitMode
            }
            return SelectedTargetManagement(
                selectedExitMode: mode,
                originalTargetPrice: runner.price,
                selectedSDTarget: partial,
                holdForHTFLiquidity: true,
                partialTrigger: partial,
                partialFraction: 0.60,
                runnerFraction: 0.40,
                runnerTarget: runner
            )
        case .none:
            throw TargetManagementError.unavailableExitMode
        }
    }

    private func htfRunnerTarget(
        liquidity: LiquidityResult?,
        direction: MarketDirection,
        beyondPrice: Double
    ) -> HTFRunnerTarget? {
        guard let liquidity else { return nil }
        let pools = direction == .bearish ? liquidity.sellSide : liquidity.buySide
        let eligible = pools.filter {
            guard $0.status == .active, $0.liquidityClass == .external else { return false }
            return direction == .bearish ? $0.price < beyondPrice : $0.price > beyondPrice
        }
        let selected: LiquidityPool?
        if direction == .bearish {
            selected = eligible.max(by: { $0.price < $1.price })
        } else {
            selected = eligible.min(by: { $0.price < $1.price })
        }
        guard let selected else { return nil }
        return HTFRunnerTarget(
            price: selected.price,
            side: direction == .bearish ? .sellSide : .buySide,
            timeframe: liquidity.timeframe,
            sourceKind: selected.sourceKind,
            sourceTime: selected.sourceTime
        )
    }
}
