"""Complete deterministic Londres trade calculation after entry and stop are known.

This layer joins the exact Phase 13 entry, Phase 14 executable stop, the
Trader's approved 3/5/10 risk tier and deterministic Phase 12 target choice.
It calculates broker-valid position size and reward/risk geometry without
placing an order.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .risk_sizing import (
    AccountRiskPolicy,
    InstrumentRiskSpec,
    RiskSizingEngine,
    RiskSizingStatus,
)


class TradeCalculationStatus(str, Enum):
    READY = "READY"
    WAIT_FOR_ENTRY = "WAIT_FOR_ENTRY"
    WAIT_FOR_EXECUTABLE_STOP = "WAIT_FOR_EXECUTABLE_STOP"
    WAIT_FOR_TRADER_SELECTION = "WAIT_FOR_TRADER_SELECTION"
    WAIT_FOR_INSTRUMENT_RISK_SPEC = "WAIT_FOR_INSTRUMENT_RISK_SPEC"
    WAIT_FOR_ACCOUNT_EQUITY = "WAIT_FOR_ACCOUNT_EQUITY"
    INSTRUMENT_STOP_TICK_MISMATCH = "INSTRUMENT_STOP_TICK_MISMATCH"
    RISK_SIZING_BLOCKED = "RISK_SIZING_BLOCKED"
    INVALID_TARGET_GEOMETRY = "INVALID_TARGET_GEOMETRY"
    HOLD_SPLIT_NOT_BROKER_EXECUTABLE = "HOLD_SPLIT_NOT_BROKER_EXECUTABLE"


@dataclass(frozen=True)
class TradeCalculationResult:
    status: TradeCalculationStatus
    direction: str
    symbol: str | None
    entry_price: float | None
    executable_stop_price: float | None
    selected_risk_fraction: float | None
    selected_exit_mode: str | None
    position_volume: float | None
    volume_unit: str | None
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    stop_distance_price: float | None
    stop_distance_ticks: int | None
    stop_distance_pips: float | None
    selected_target_price: float | None
    selected_target_label: str | None
    reward_distance_price: float | None
    risk_reward_ratio: float | None
    partial_trigger_price: float | None
    partial_fraction: float | None
    partial_volume: float | None
    runner_fraction: float | None
    runner_volume: float | None
    runner_target_price: float | None
    runner_risk_reward_ratio: float | None
    risk_sizing: dict | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["manual_volume_allowed"] = False
        payload["order_authorized"] = False
        payload["calculation_authority"] = "DETERMINISTIC_LONDRES_TRADE_CALCULATOR"
        return payload


class TradeCalculatorEngine:
    """Calculate final risk size and selected target R:R without broker execution."""

    def __init__(self) -> None:
        self.risk = RiskSizingEngine()

    def calculate(
        self,
        *,
        entry_execution: dict | None,
        executable_stop: dict | None,
        trader_selection: dict | None,
        instrument: InstrumentRiskSpec | None,
        account_equity: float | None,
        max_risk_cash: float | None = None,
    ) -> TradeCalculationResult:
        entry_state = entry_execution or {}
        stop_state = executable_stop or {}
        selection = trader_selection or {}
        direction = str(entry_state.get("direction") or "UNCONFIRMED")
        entry = entry_state.get("exact_entry_price")

        if entry_state.get("status") != "ENTRY_TRIGGERED" or entry is None:
            return self._wait(
                TradeCalculationStatus.WAIT_FOR_ENTRY,
                direction=direction,
                reason="EXACT_PHASE13_ENTRY_REQUIRED",
            )
        entry_price = float(entry)

        if stop_state.get("status") != "READY" or stop_state.get("executable_stop_price") is None:
            return self._wait(
                TradeCalculationStatus.WAIT_FOR_EXECUTABLE_STOP,
                direction=direction,
                entry_price=entry_price,
                reason="PHASE14_EXECUTABLE_STOP_REQUIRED",
            )
        stop_price = float(stop_state["executable_stop_price"])

        risk_fraction = selection.get("selected_risk_fraction")
        exit_selection = selection.get("target_management")
        if (
            not selection.get("valid")
            or risk_fraction is None
            or not isinstance(exit_selection, dict)
            or not exit_selection.get("selected_exit_mode")
        ):
            return self._wait(
                TradeCalculationStatus.WAIT_FOR_TRADER_SELECTION,
                direction=direction,
                entry_price=entry_price,
                executable_stop_price=stop_price,
                reason="HARD_VALIDATED_STOP_RISK_TARGET_SELECTION_REQUIRED",
            )

        if instrument is None:
            return self._wait(
                TradeCalculationStatus.WAIT_FOR_INSTRUMENT_RISK_SPEC,
                direction=direction,
                entry_price=entry_price,
                executable_stop_price=stop_price,
                selected_risk_fraction=float(risk_fraction),
                selected_exit_mode=exit_selection.get("selected_exit_mode"),
                reason="BROKER_INSTRUMENT_RISK_SPEC_REQUIRED",
            )

        stop_tick = stop_state.get("tick_size")
        if stop_tick is not None and not math.isclose(
            float(stop_tick), instrument.tick_size, rel_tol=0.0, abs_tol=1e-12
        ):
            return self._wait(
                TradeCalculationStatus.INSTRUMENT_STOP_TICK_MISMATCH,
                direction=direction,
                symbol=instrument.symbol,
                entry_price=entry_price,
                executable_stop_price=stop_price,
                selected_risk_fraction=float(risk_fraction),
                selected_exit_mode=exit_selection.get("selected_exit_mode"),
                reason="PHASE14_TICK_SIZE_DIFFERS_FROM_RISK_INSTRUMENT_SPEC",
            )

        if account_equity is None or float(account_equity) <= 0:
            return self._wait(
                TradeCalculationStatus.WAIT_FOR_ACCOUNT_EQUITY,
                direction=direction,
                symbol=instrument.symbol,
                entry_price=entry_price,
                executable_stop_price=stop_price,
                selected_risk_fraction=float(risk_fraction),
                selected_exit_mode=exit_selection.get("selected_exit_mode"),
                reason="POSITIVE_ACCOUNT_EQUITY_REQUIRED",
            )

        policy = AccountRiskPolicy(
            account_equity=float(account_equity),
            risk_fraction=float(risk_fraction),
            max_risk_cash=max_risk_cash,
        )
        risk_result = self.risk.calculate(
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            instrument=instrument,
            policy=policy,
        )
        risk_dict = risk_result.to_dict()
        if risk_result.status != RiskSizingStatus.READY or risk_result.final_volume is None:
            return TradeCalculationResult(
                status=TradeCalculationStatus.RISK_SIZING_BLOCKED,
                direction=direction,
                symbol=instrument.symbol,
                entry_price=entry_price,
                executable_stop_price=stop_price,
                selected_risk_fraction=float(risk_fraction),
                selected_exit_mode=exit_selection.get("selected_exit_mode"),
                position_volume=None,
                volume_unit=instrument.volume_unit,
                projected_cash_risk=None,
                projected_equity_risk_fraction=None,
                stop_distance_price=risk_result.stop_distance_price,
                stop_distance_ticks=risk_result.stop_distance_ticks,
                stop_distance_pips=risk_result.stop_distance_pips,
                selected_target_price=None,
                selected_target_label=None,
                reward_distance_price=None,
                risk_reward_ratio=None,
                partial_trigger_price=None,
                partial_fraction=None,
                partial_volume=None,
                runner_fraction=None,
                runner_volume=None,
                runner_target_price=None,
                runner_risk_reward_ratio=None,
                risk_sizing=risk_dict,
                reason_codes=("DETERMINISTIC_RISK_SIZING_NOT_READY",),
            )

        selected_target = exit_selection.get("selected_sd_target") or {}
        target_price = selected_target.get("price")
        target_label = selected_target.get("label")
        if target_price is None:
            return self._invalid_target(
                direction=direction,
                instrument=instrument,
                entry_price=entry_price,
                stop_price=stop_price,
                risk_fraction=float(risk_fraction),
                exit_mode=exit_selection.get("selected_exit_mode"),
                risk_dict=risk_dict,
                reason="SELECTED_SD_TARGET_PRICE_REQUIRED",
            )

        target = float(target_price)
        reward = self._reward_distance(direction, entry_price, target)
        risk_distance = float(risk_result.stop_distance_price or 0.0)
        if reward is None or risk_distance <= 0:
            return self._invalid_target(
                direction=direction,
                instrument=instrument,
                entry_price=entry_price,
                stop_price=stop_price,
                risk_fraction=float(risk_fraction),
                exit_mode=exit_selection.get("selected_exit_mode"),
                risk_dict=risk_dict,
                reason="SELECTED_TARGET_NOT_BEYOND_ENTRY_IN_TRADE_DIRECTION",
            )
        rr = reward / risk_distance

        partial_trigger_price = None
        partial_fraction = None
        partial_volume = None
        runner_fraction = None
        runner_volume = None
        runner_target_price = None
        runner_rr = None

        if exit_selection.get("hold_for_htf_liquidity"):
            partial = exit_selection.get("partial_trigger") or {}
            runner = exit_selection.get("runner_target") or {}
            partial_trigger_price = partial.get("trigger_price")
            partial_fraction = float(exit_selection.get("partial_fraction") or 0.0)
            runner_fraction = float(exit_selection.get("runner_fraction") or 0.0)
            runner_target_price = runner.get("price")

            if (
                partial_trigger_price is None
                or runner_target_price is None
                or not math.isclose(partial_fraction, 0.60, abs_tol=1e-12)
                or not math.isclose(runner_fraction, 0.40, abs_tol=1e-12)
            ):
                return self._invalid_target(
                    direction=direction,
                    instrument=instrument,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    risk_fraction=float(risk_fraction),
                    exit_mode=exit_selection.get("selected_exit_mode"),
                    risk_dict=risk_dict,
                    reason="HOLD_MODE_REQUIRES_EXACT_60_40_MANAGEMENT_CONTRACT",
                )

            partial_target = float(partial_trigger_price)
            runner_target = float(runner_target_price)
            if self._reward_distance(direction, entry_price, partial_target) is None:
                return self._invalid_target(
                    direction=direction,
                    instrument=instrument,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    risk_fraction=float(risk_fraction),
                    exit_mode=exit_selection.get("selected_exit_mode"),
                    risk_dict=risk_dict,
                    reason="PARTIAL_TRIGGER_NOT_IN_TRADE_DIRECTION",
                )
            runner_reward = self._reward_distance(direction, entry_price, runner_target)
            if runner_reward is None or not self._runner_beyond_partial(
                direction, partial_target, runner_target
            ):
                return self._invalid_target(
                    direction=direction,
                    instrument=instrument,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    risk_fraction=float(risk_fraction),
                    exit_mode=exit_selection.get("selected_exit_mode"),
                    risk_dict=risk_dict,
                    reason="HTF_RUNNER_TARGET_MUST_BE_BEYOND_SD_2_5",
                )

            split = self._exact_split(
                risk_result.final_volume,
                instrument=instrument,
                partial_fraction=partial_fraction,
                runner_fraction=runner_fraction,
            )
            if split is None:
                return TradeCalculationResult(
                    status=TradeCalculationStatus.HOLD_SPLIT_NOT_BROKER_EXECUTABLE,
                    direction=direction,
                    symbol=instrument.symbol,
                    entry_price=entry_price,
                    executable_stop_price=stop_price,
                    selected_risk_fraction=float(risk_fraction),
                    selected_exit_mode=exit_selection.get("selected_exit_mode"),
                    position_volume=risk_result.final_volume,
                    volume_unit=instrument.volume_unit,
                    projected_cash_risk=risk_result.projected_cash_risk,
                    projected_equity_risk_fraction=risk_result.projected_equity_risk_fraction,
                    stop_distance_price=risk_result.stop_distance_price,
                    stop_distance_ticks=risk_result.stop_distance_ticks,
                    stop_distance_pips=risk_result.stop_distance_pips,
                    selected_target_price=target,
                    selected_target_label=target_label,
                    reward_distance_price=reward,
                    risk_reward_ratio=rr,
                    partial_trigger_price=partial_target,
                    partial_fraction=partial_fraction,
                    partial_volume=None,
                    runner_fraction=runner_fraction,
                    runner_volume=None,
                    runner_target_price=runner_target,
                    runner_risk_reward_ratio=runner_reward / risk_distance,
                    risk_sizing=risk_dict,
                    reason_codes=(
                        "POSITION_SIZE_CANNOT_BE_SPLIT_EXACTLY_60_40_ON_BROKER_VOLUME_GRID",
                        "DO_NOT_APPROXIMATE_USER_DEFINED_MANAGEMENT_FRACTIONS",
                    ),
                )
            partial_volume, runner_volume = split
            runner_rr = runner_reward / risk_distance

        reasons = [
            "EXACT_ENTRY_AND_EXECUTABLE_STOP_LOCKED",
            "POSITION_SIZE_DERIVED_FROM_SELECTED_3_5_OR_10_PERCENT_RISK",
            "SELECTED_CSD_TARGET_RISK_REWARD_CALCULATED",
        ]
        if exit_selection.get("hold_for_htf_liquidity"):
            reasons.extend(
                [
                    "EXACT_60_40_VOLUME_SPLIT_IS_BROKER_EXECUTABLE",
                    "RUNNER_RISK_REWARD_CALCULATED_TO_ORIGINAL_HTF_LIQUIDITY_TARGET",
                ]
            )

        return TradeCalculationResult(
            status=TradeCalculationStatus.READY,
            direction=direction,
            symbol=instrument.symbol,
            entry_price=entry_price,
            executable_stop_price=stop_price,
            selected_risk_fraction=float(risk_fraction),
            selected_exit_mode=exit_selection.get("selected_exit_mode"),
            position_volume=risk_result.final_volume,
            volume_unit=instrument.volume_unit,
            projected_cash_risk=risk_result.projected_cash_risk,
            projected_equity_risk_fraction=risk_result.projected_equity_risk_fraction,
            stop_distance_price=risk_result.stop_distance_price,
            stop_distance_ticks=risk_result.stop_distance_ticks,
            stop_distance_pips=risk_result.stop_distance_pips,
            selected_target_price=target,
            selected_target_label=target_label,
            reward_distance_price=reward,
            risk_reward_ratio=rr,
            partial_trigger_price=(
                float(partial_trigger_price) if partial_trigger_price is not None else None
            ),
            partial_fraction=partial_fraction,
            partial_volume=partial_volume,
            runner_fraction=runner_fraction,
            runner_volume=runner_volume,
            runner_target_price=(
                float(runner_target_price) if runner_target_price is not None else None
            ),
            runner_risk_reward_ratio=runner_rr,
            risk_sizing=risk_dict,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _reward_distance(direction: str, entry: float, target: float) -> float | None:
        if direction == "BEARISH" and target < entry:
            return entry - target
        if direction == "BULLISH" and target > entry:
            return target - entry
        return None

    @staticmethod
    def _runner_beyond_partial(direction: str, partial: float, runner: float) -> bool:
        if direction == "BEARISH":
            return runner < partial
        if direction == "BULLISH":
            return runner > partial
        return False

    @staticmethod
    def _exact_split(
        total_volume: float,
        *,
        instrument: InstrumentRiskSpec,
        partial_fraction: float,
        runner_fraction: float,
    ) -> tuple[float, float] | None:
        partial = total_volume * partial_fraction
        runner = total_volume * runner_fraction
        step = instrument.volume_step

        partial_steps = round(partial / step)
        runner_steps = round(runner / step)
        if not math.isclose(partial_steps * step, partial, rel_tol=0.0, abs_tol=1e-9):
            return None
        if not math.isclose(runner_steps * step, runner, rel_tol=0.0, abs_tol=1e-9):
            return None

        partial_volume = partial_steps * step
        runner_volume = runner_steps * step
        if partial_volume + 1e-12 < instrument.min_volume:
            return None
        if runner_volume + 1e-12 < instrument.min_volume:
            return None
        if not math.isclose(
            partial_volume + runner_volume,
            total_volume,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return None
        return partial_volume, runner_volume

    def _invalid_target(
        self,
        *,
        direction: str,
        instrument: InstrumentRiskSpec,
        entry_price: float,
        stop_price: float,
        risk_fraction: float,
        exit_mode: str | None,
        risk_dict: dict,
        reason: str,
    ) -> TradeCalculationResult:
        return TradeCalculationResult(
            status=TradeCalculationStatus.INVALID_TARGET_GEOMETRY,
            direction=direction,
            symbol=instrument.symbol,
            entry_price=entry_price,
            executable_stop_price=stop_price,
            selected_risk_fraction=risk_fraction,
            selected_exit_mode=exit_mode,
            position_volume=risk_dict.get("final_volume"),
            volume_unit=instrument.volume_unit,
            projected_cash_risk=risk_dict.get("projected_cash_risk"),
            projected_equity_risk_fraction=risk_dict.get("projected_equity_risk_fraction"),
            stop_distance_price=risk_dict.get("stop_distance_price"),
            stop_distance_ticks=risk_dict.get("stop_distance_ticks"),
            stop_distance_pips=risk_dict.get("stop_distance_pips"),
            selected_target_price=None,
            selected_target_label=None,
            reward_distance_price=None,
            risk_reward_ratio=None,
            partial_trigger_price=None,
            partial_fraction=None,
            partial_volume=None,
            runner_fraction=None,
            runner_volume=None,
            runner_target_price=None,
            runner_risk_reward_ratio=None,
            risk_sizing=risk_dict,
            reason_codes=(reason,),
        )

    @staticmethod
    def _wait(
        status: TradeCalculationStatus,
        *,
        direction: str,
        reason: str,
        symbol: str | None = None,
        entry_price: float | None = None,
        executable_stop_price: float | None = None,
        selected_risk_fraction: float | None = None,
        selected_exit_mode: str | None = None,
    ) -> TradeCalculationResult:
        return TradeCalculationResult(
            status=status,
            direction=direction,
            symbol=symbol,
            entry_price=entry_price,
            executable_stop_price=executable_stop_price,
            selected_risk_fraction=selected_risk_fraction,
            selected_exit_mode=selected_exit_mode,
            position_volume=None,
            volume_unit=None,
            projected_cash_risk=None,
            projected_equity_risk_fraction=None,
            stop_distance_price=None,
            stop_distance_ticks=None,
            stop_distance_pips=None,
            selected_target_price=None,
            selected_target_label=None,
            reward_distance_price=None,
            risk_reward_ratio=None,
            partial_trigger_price=None,
            partial_fraction=None,
            partial_volume=None,
            runner_fraction=None,
            runner_volume=None,
            runner_target_price=None,
            runner_risk_reward_ratio=None,
            risk_sizing=None,
            reason_codes=(reason,),
        )
