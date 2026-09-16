"""Hard deterministic pre-broker validator for Londres trade packages.

This is the final fail-closed firewall before a future broker adapter may
receive an order request.  It does not place orders and does not contain broker
credentials or account-environment information.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .risk_sizing import (
    ALLOWED_RISK_FRACTIONS,
    MAX_ACCOUNT_RISK_FRACTION,
    InstrumentRiskSpec,
)


class PreBrokerValidationStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    WAIT_FOR_ENTRY = "WAIT_FOR_ENTRY"
    WAIT_FOR_EXECUTABLE_STOP = "WAIT_FOR_EXECUTABLE_STOP"
    WAIT_FOR_TRADE_CALCULATION = "WAIT_FOR_TRADE_CALCULATION"
    WAIT_FOR_MANAGEMENT_STATE = "WAIT_FOR_MANAGEMENT_STATE"
    INVALID_TRADER_SELECTION = "INVALID_TRADER_SELECTION"
    DIRECTION_MISMATCH = "DIRECTION_MISMATCH"
    ENTRY_GEOMETRY_MISMATCH = "ENTRY_GEOMETRY_MISMATCH"
    STOP_GEOMETRY_MISMATCH = "STOP_GEOMETRY_MISMATCH"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    ACCOUNT_POLICY_VIOLATION = "ACCOUNT_POLICY_VIOLATION"
    RISK_POLICY_VIOLATION = "RISK_POLICY_VIOLATION"
    VOLUME_POLICY_VIOLATION = "VOLUME_POLICY_VIOLATION"
    TARGET_CONTRACT_VIOLATION = "TARGET_CONTRACT_VIOLATION"
    BREAK_EVEN_POLICY_VIOLATION = "BREAK_EVEN_POLICY_VIOLATION"


@dataclass(frozen=True)
class PreBrokerValidationResult:
    status: PreBrokerValidationStatus
    direction: str
    symbol: str | None
    entry_price: float | None
    original_stop_price: float | None
    current_stop_price: float | None
    selected_risk_fraction: float | None
    selected_exit_mode: str | None
    position_volume: float | None
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    account_equity: float | None
    order_authorized: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["validator_authority"] = "DETERMINISTIC_HARD_PRE_BROKER_GATE"
        payload["broker_order_placed"] = False
        payload["manual_override_allowed"] = False
        return payload


class HardPreBrokerOrderValidator:
    """Validate the complete deterministic Londres order contract.

    A package is authorized only when all prior deterministic layers agree.
    Authorization here means "eligible to be handed to a future broker adapter";
    this module never submits, modifies or closes a broker order itself.
    """

    _MANAGEMENT_WAIT_STATES = {
        "WAIT_FOR_IOF_EXIT",
        "WAIT_FOR_NEW_FRACTAL",
        "WAIT_FOR_BOS",
    }
    _ALLOWED_EXIT_MODES = {
        "FULL_AT_SD_2",
        "FULL_AT_SD_2_5",
        "HOLD_HTF_LIQUIDITY",
    }

    def validate(
        self,
        *,
        entry_execution: dict | None,
        executable_stop: dict | None,
        trade_calculation: dict | None,
        break_even_management: dict | None,
        trader_selection: dict | None,
        instrument: InstrumentRiskSpec | None,
        account_equity: float | None,
        max_risk_cash: float | None = None,
    ) -> PreBrokerValidationResult:
        entry_state = entry_execution or {}
        stop_state = executable_stop or {}
        calculation = trade_calculation or {}
        management = break_even_management or {}
        selection = trader_selection or {}

        direction = str(entry_state.get("direction") or calculation.get("direction") or "UNCONFIRMED")
        entry = self._float_or_none(entry_state.get("exact_entry_price"))
        original_stop = self._float_or_none(stop_state.get("executable_stop_price"))
        current_stop = self._float_or_none(management.get("current_stop_price"))
        selected_risk = self._float_or_none(calculation.get("selected_risk_fraction"))
        selected_exit_mode = calculation.get("selected_exit_mode")
        position_volume = self._float_or_none(calculation.get("position_volume"))
        projected_cash_risk = self._float_or_none(calculation.get("projected_cash_risk"))
        projected_fraction = self._float_or_none(
            calculation.get("projected_equity_risk_fraction")
        )
        equity = self._float_or_none(account_equity)
        symbol = calculation.get("symbol")

        if entry_state.get("status") != "ENTRY_TRIGGERED" or entry is None:
            return self._fail(
                PreBrokerValidationStatus.WAIT_FOR_ENTRY,
                direction=direction,
                symbol=symbol,
                reason="EXACT_PHASE13_ENTRY_REQUIRED",
            )

        if stop_state.get("status") != "READY" or original_stop is None:
            return self._fail(
                PreBrokerValidationStatus.WAIT_FOR_EXECUTABLE_STOP,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                reason="READY_PHASE14_EXECUTABLE_STOP_REQUIRED",
            )

        if calculation.get("status") != "READY":
            return self._fail(
                PreBrokerValidationStatus.WAIT_FOR_TRADE_CALCULATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                reason="READY_PHASE15_TRADE_CALCULATION_REQUIRED",
            )

        management_status = management.get("status")
        if management_status == "WAIT_FOR_ACTIVE_TRADE" or management_status is None:
            return self._fail(
                PreBrokerValidationStatus.WAIT_FOR_MANAGEMENT_STATE,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                reason="VALID_PHASE16_MANAGEMENT_STATE_REQUIRED",
            )

        if not selection.get("valid"):
            return self._fail(
                PreBrokerValidationStatus.INVALID_TRADER_SELECTION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="HARD_VALIDATED_TRADER_STOP_RISK_TARGET_SELECTION_REQUIRED",
            )

        directions = {
            str(value)
            for value in (
                direction,
                stop_state.get("direction"),
                calculation.get("direction"),
                management.get("direction"),
            )
            if value is not None
        }
        if direction not in {"BULLISH", "BEARISH"} or len(directions) != 1:
            return self._fail(
                PreBrokerValidationStatus.DIRECTION_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="ENTRY_STOP_CALCULATION_AND_MANAGEMENT_DIRECTION_MUST_MATCH",
            )

        entry_values = (
            entry,
            self._float_or_none(stop_state.get("entry_price")),
            self._float_or_none(calculation.get("entry_price")),
            self._float_or_none(management.get("entry_price")),
        )
        if any(value is None for value in entry_values) or not self._all_close(entry_values):
            return self._fail(
                PreBrokerValidationStatus.ENTRY_GEOMETRY_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="ALL_PHASES_MUST_REFERENCE_THE_SAME_EXACT_ENTRY_PRICE",
            )

        stop_values = (
            original_stop,
            self._float_or_none(calculation.get("executable_stop_price")),
            self._float_or_none(management.get("original_stop_price")),
        )
        if any(value is None for value in stop_values) or not self._all_close(stop_values):
            return self._fail(
                PreBrokerValidationStatus.STOP_GEOMETRY_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="ORIGINAL_EXECUTABLE_STOP_MUST_REMAIN_IDENTICAL_ACROSS_PHASES",
            )

        if direction == "BEARISH" and original_stop <= entry:
            return self._stop_geometry_failure(direction, symbol, entry, original_stop, current_stop)
        if direction == "BULLISH" and original_stop >= entry:
            return self._stop_geometry_failure(direction, symbol, entry, original_stop, current_stop)

        if selection.get("selected_source") != stop_state.get("selected_stop_source"):
            return self._fail(
                PreBrokerValidationStatus.STOP_GEOMETRY_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="EXECUTABLE_STOP_SOURCE_MUST_MATCH_HARD_VALIDATED_TRADER_STOP_SOURCE",
            )
        selected_anchor = self._float_or_none(selection.get("selected_anchor_price"))
        stop_anchor = self._float_or_none(stop_state.get("structural_anchor_price"))
        if selected_anchor is None or stop_anchor is None or not math.isclose(
            selected_anchor, stop_anchor, rel_tol=0.0, abs_tol=1e-9
        ):
            return self._fail(
                PreBrokerValidationStatus.STOP_GEOMETRY_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="EXECUTABLE_STOP_ANCHOR_MUST_MATCH_SELECTED_STRUCTURAL_ANCHOR",
            )

        if instrument is None or symbol is None or symbol != instrument.symbol:
            return self._fail(
                PreBrokerValidationStatus.SYMBOL_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="PHASE15_SYMBOL_MUST_MATCH_BROKER_INSTRUMENT_SPEC",
            )
        risk_sizing = calculation.get("risk_sizing") or {}
        if risk_sizing.get("symbol") != instrument.symbol:
            return self._fail(
                PreBrokerValidationStatus.SYMBOL_MISMATCH,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="RISK_SIZING_SYMBOL_MUST_MATCH_BROKER_INSTRUMENT_SPEC",
            )

        if equity is None or equity <= 0:
            return self._fail(
                PreBrokerValidationStatus.ACCOUNT_POLICY_VIOLATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                reason="POSITIVE_ACCOUNT_EQUITY_REQUIRED",
            )

        selection_risk = self._float_or_none(selection.get("selected_risk_fraction"))
        if (
            selected_risk is None
            or selection_risk is None
            or not self._is_allowed_risk(selected_risk)
            or not math.isclose(selected_risk, selection_risk, rel_tol=0.0, abs_tol=1e-12)
            or selected_risk > MAX_ACCOUNT_RISK_FRACTION + 1e-12
        ):
            return self._fail(
                PreBrokerValidationStatus.RISK_POLICY_VIOLATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                selected_risk_fraction=selected_risk,
                reason="RISK_MUST_EQUAL_HARD_VALIDATED_3_5_OR_10_PERCENT_TIER",
            )

        if (
            position_volume is None
            or position_volume <= 0
            or position_volume + 1e-12 < instrument.min_volume
            or position_volume > instrument.max_volume + 1e-12
            or not self._on_grid(position_volume, instrument.volume_step)
        ):
            return self._fail(
                PreBrokerValidationStatus.VOLUME_POLICY_VIOLATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                selected_risk_fraction=selected_risk,
                position_volume=position_volume,
                reason="POSITION_VOLUME_MUST_BE_POSITIVE_AND_ON_BROKER_VOLUME_GRID",
            )

        sized_volume = self._float_or_none(risk_sizing.get("final_volume"))
        if risk_sizing.get("status") != "READY" or sized_volume is None or not math.isclose(
            position_volume, sized_volume, rel_tol=0.0, abs_tol=1e-9
        ):
            return self._fail(
                PreBrokerValidationStatus.VOLUME_POLICY_VIOLATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                selected_risk_fraction=selected_risk,
                position_volume=position_volume,
                reason="POSITION_VOLUME_MUST_EQUAL_DETERMINISTIC_RISK_ENGINE_OUTPUT",
            )

        if projected_cash_risk is None or projected_fraction is None:
            return self._risk_failure(
                direction,
                symbol,
                entry,
                original_stop,
                current_stop,
                selected_risk,
                position_volume,
                "PROJECTED_CASH_AND_EQUITY_RISK_REQUIRED",
            )
        expected_fraction = projected_cash_risk / equity
        if not math.isclose(projected_fraction, expected_fraction, rel_tol=0.0, abs_tol=1e-9):
            return self._risk_failure(
                direction,
                symbol,
                entry,
                original_stop,
                current_stop,
                selected_risk,
                position_volume,
                "PROJECTED_EQUITY_RISK_MUST_EQUAL_PROJECTED_CASH_RISK_OVER_EQUITY",
            )
        risk_budget = equity * selected_risk
        if max_risk_cash is not None:
            if max_risk_cash <= 0:
                return self._risk_failure(
                    direction,
                    symbol,
                    entry,
                    original_stop,
                    current_stop,
                    selected_risk,
                    position_volume,
                    "MAX_RISK_CASH_MUST_BE_POSITIVE_WHEN_SUPPLIED",
                )
            risk_budget = min(risk_budget, float(max_risk_cash))
        if (
            projected_cash_risk > risk_budget + 1e-9
            or projected_fraction > selected_risk + 1e-12
            or projected_fraction > MAX_ACCOUNT_RISK_FRACTION + 1e-12
        ):
            return self._risk_failure(
                direction,
                symbol,
                entry,
                original_stop,
                current_stop,
                selected_risk,
                position_volume,
                "PROJECTED_LOSS_EXCEEDS_SELECTED_RISK_BUDGET_OR_10_PERCENT_HARD_CEILING",
            )

        target_failure = self._validate_target_contract(
            direction=direction,
            entry_price=entry,
            calculation=calculation,
            selection=selection,
            instrument=instrument,
            position_volume=position_volume,
        )
        if target_failure is not None:
            return self._fail(
                PreBrokerValidationStatus.TARGET_CONTRACT_VIOLATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                selected_risk_fraction=selected_risk,
                selected_exit_mode=selected_exit_mode,
                position_volume=position_volume,
                projected_cash_risk=projected_cash_risk,
                projected_equity_risk_fraction=projected_fraction,
                account_equity=equity,
                reason=target_failure,
            )

        management_failure = self._validate_break_even_contract(
            management=management,
            entry_price=entry,
            original_stop=original_stop,
            calculation=calculation,
        )
        if management_failure is not None:
            return self._fail(
                PreBrokerValidationStatus.BREAK_EVEN_POLICY_VIOLATION,
                direction=direction,
                symbol=symbol,
                entry_price=entry,
                original_stop_price=original_stop,
                current_stop_price=current_stop,
                selected_risk_fraction=selected_risk,
                selected_exit_mode=selected_exit_mode,
                position_volume=position_volume,
                projected_cash_risk=projected_cash_risk,
                projected_equity_risk_fraction=projected_fraction,
                account_equity=equity,
                reason=management_failure,
            )

        return PreBrokerValidationResult(
            status=PreBrokerValidationStatus.AUTHORIZED,
            direction=direction,
            symbol=symbol,
            entry_price=entry,
            original_stop_price=original_stop,
            current_stop_price=current_stop,
            selected_risk_fraction=selected_risk,
            selected_exit_mode=str(selected_exit_mode),
            position_volume=position_volume,
            projected_cash_risk=projected_cash_risk,
            projected_equity_risk_fraction=projected_fraction,
            account_equity=equity,
            order_authorized=True,
            reason_codes=(
                "PHASE13_EXACT_ENTRY_VALIDATED",
                "PHASE14_EXECUTABLE_STOP_VALIDATED",
                "PHASE15_RISK_VOLUME_TARGET_CONTRACT_VALIDATED",
                "PHASE16_CURRENT_STOP_MANAGEMENT_VALIDATED",
                "RISK_RESTRICTED_TO_3_5_OR_10_PERCENT_WITH_10_PERCENT_HARD_CEILING",
                "SYMBOL_ACCOUNT_AND_BROKER_VOLUME_GRID_VALIDATED",
                "PRE_BROKER_ORDER_CONTRACT_AUTHORIZED",
                "BROKER_EXECUTION_NOT_PERFORMED_BY_THIS_VALIDATOR",
            ),
        )

    def _validate_target_contract(
        self,
        *,
        direction: str,
        entry_price: float,
        calculation: dict,
        selection: dict,
        instrument: InstrumentRiskSpec,
        position_volume: float,
    ) -> str | None:
        mode = calculation.get("selected_exit_mode")
        target_selection = selection.get("target_management") or {}
        if mode not in self._ALLOWED_EXIT_MODES:
            return "EXIT_MODE_MUST_BE_ONE_OF_THE_DETERMINISTIC_PHASE12_OPTIONS"
        if selection.get("selected_exit_mode") != mode:
            return "PHASE15_EXIT_MODE_MUST_MATCH_HARD_VALIDATED_TRADER_SELECTION"
        if target_selection.get("selected_exit_mode") != mode:
            return "PHASE15_EXIT_MODE_MUST_MATCH_DETERMINISTIC_TARGET_SELECTION"

        target_price = self._float_or_none(calculation.get("selected_target_price"))
        selected_sd = target_selection.get("selected_sd_target") or {}
        selected_sd_price = self._float_or_none(selected_sd.get("price"))
        if target_price is None or selected_sd_price is None or not math.isclose(
            target_price, selected_sd_price, rel_tol=0.0, abs_tol=1e-9
        ):
            return "SELECTED_TARGET_PRICE_MUST_MATCH_DETERMINISTIC_CSD_SD_TARGET"
        if direction == "BEARISH" and target_price >= entry_price:
            return "BEARISH_TARGET_MUST_BE_BELOW_ENTRY"
        if direction == "BULLISH" and target_price <= entry_price:
            return "BULLISH_TARGET_MUST_BE_ABOVE_ENTRY"

        if mode != "HOLD_HTF_LIQUIDITY":
            if target_selection.get("hold_for_htf_liquidity"):
                return "FULL_EXIT_MODE_CANNOT_ENABLE_HTF_RUNNER"
            return None

        if not target_selection.get("hold_for_htf_liquidity"):
            return "HOLD_HTF_LIQUIDITY_MODE_REQUIRES_HOLD_FLAG"
        partial_fraction = self._float_or_none(calculation.get("partial_fraction"))
        runner_fraction = self._float_or_none(calculation.get("runner_fraction"))
        partial_volume = self._float_or_none(calculation.get("partial_volume"))
        runner_volume = self._float_or_none(calculation.get("runner_volume"))
        partial_target = self._float_or_none(calculation.get("partial_trigger_price"))
        runner_target = self._float_or_none(calculation.get("runner_target_price"))
        if (
            partial_fraction is None
            or runner_fraction is None
            or not math.isclose(partial_fraction, 0.60, abs_tol=1e-12)
            or not math.isclose(runner_fraction, 0.40, abs_tol=1e-12)
            or partial_volume is None
            or runner_volume is None
            or partial_target is None
            or runner_target is None
        ):
            return "HOLD_MODE_REQUIRES_EXACT_60_40_BROKER_EXECUTABLE_CONTRACT"
        if not math.isclose(
            partial_volume + runner_volume, position_volume, rel_tol=0.0, abs_tol=1e-9
        ):
            return "HOLD_PARTIAL_AND_RUNNER_VOLUMES_MUST_SUM_TO_TOTAL_POSITION"
        if not self._on_grid(partial_volume, instrument.volume_step) or not self._on_grid(
            runner_volume, instrument.volume_step
        ):
            return "HOLD_PARTIAL_AND_RUNNER_VOLUMES_MUST_BE_ON_BROKER_GRID"
        if partial_volume + 1e-12 < instrument.min_volume or runner_volume + 1e-12 < instrument.min_volume:
            return "HOLD_PARTIAL_AND_RUNNER_MUST_EACH_MEET_BROKER_MINIMUM_VOLUME"
        if direction == "BEARISH" and not (runner_target < partial_target < entry_price):
            return "BEARISH_RUNNER_TARGET_MUST_BE_BEYOND_SD_2_5_PARTIAL"
        if direction == "BULLISH" and not (runner_target > partial_target > entry_price):
            return "BULLISH_RUNNER_TARGET_MUST_BE_BEYOND_SD_2_5_PARTIAL"
        return None

    def _validate_break_even_contract(
        self,
        *,
        management: dict,
        entry_price: float,
        original_stop: float,
        calculation: dict,
    ) -> str | None:
        status = management.get("status")
        current = self._float_or_none(management.get("current_stop_price"))
        if status == "BREAK_EVEN_TRIGGERED":
            break_even = self._float_or_none(management.get("break_even_price"))
            if current is None or break_even is None:
                return "BREAK_EVEN_TRIGGERED_REQUIRES_CURRENT_AND_BREAK_EVEN_STOP_PRICES"
            if not math.isclose(current, entry_price, rel_tol=0.0, abs_tol=1e-9):
                return "BREAK_EVEN_CURRENT_STOP_MUST_EQUAL_EXACT_ENTRY_PRICE"
            if not math.isclose(break_even, entry_price, rel_tol=0.0, abs_tol=1e-9):
                return "BREAK_EVEN_PRICE_MUST_EQUAL_EXACT_ENTRY_WITH_NO_COST_COMPENSATION"
        elif status in self._MANAGEMENT_WAIT_STATES:
            if current is None or not math.isclose(
                current, original_stop, rel_tol=0.0, abs_tol=1e-9
            ):
                return "PRE_BREAK_EVEN_CURRENT_STOP_MUST_REMAIN_AT_ORIGINAL_EXECUTABLE_STOP"
        else:
            return "UNRECOGNIZED_PHASE16_BREAK_EVEN_MANAGEMENT_STATE"

        original_cash = self._float_or_none(management.get("original_projected_cash_risk"))
        original_fraction = self._float_or_none(
            management.get("original_projected_equity_risk_fraction")
        )
        original_rr = self._float_or_none(management.get("original_risk_reward_ratio"))
        if (
            original_cash is None
            or original_fraction is None
            or original_rr is None
            or not math.isclose(
                original_cash,
                float(calculation["projected_cash_risk"]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or not math.isclose(
                original_fraction,
                float(calculation["projected_equity_risk_fraction"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                original_rr,
                float(calculation["risk_reward_ratio"]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            return "ORIGINAL_STOP_RISK_AND_RR_ANALYTICS_MUST_REMAIN_IMMUTABLE_AFTER_MANAGEMENT"
        return None

    @staticmethod
    def _is_allowed_risk(value: float) -> bool:
        return any(math.isclose(value, allowed, abs_tol=1e-12) for allowed in ALLOWED_RISK_FRACTIONS)

    @staticmethod
    def _on_grid(value: float, step: float) -> bool:
        steps = round(value / step)
        return math.isclose(steps * step, value, rel_tol=0.0, abs_tol=1e-9)

    @staticmethod
    def _all_close(values: tuple[float | None, ...]) -> bool:
        numeric = [float(value) for value in values if value is not None]
        return bool(numeric) and all(
            math.isclose(numeric[0], value, rel_tol=0.0, abs_tol=1e-9)
            for value in numeric[1:]
        )

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        return None if value is None else float(value)

    def _stop_geometry_failure(
        self,
        direction: str,
        symbol: str | None,
        entry: float,
        original_stop: float,
        current_stop: float | None,
    ) -> PreBrokerValidationResult:
        return self._fail(
            PreBrokerValidationStatus.STOP_GEOMETRY_MISMATCH,
            direction=direction,
            symbol=symbol,
            entry_price=entry,
            original_stop_price=original_stop,
            current_stop_price=current_stop,
            reason="ORIGINAL_EXECUTABLE_STOP_MUST_BE_BEYOND_ENTRY_IN_INVALIDATION_DIRECTION",
        )

    def _risk_failure(
        self,
        direction: str,
        symbol: str | None,
        entry: float,
        original_stop: float,
        current_stop: float | None,
        selected_risk: float,
        position_volume: float,
        reason: str,
    ) -> PreBrokerValidationResult:
        return self._fail(
            PreBrokerValidationStatus.RISK_POLICY_VIOLATION,
            direction=direction,
            symbol=symbol,
            entry_price=entry,
            original_stop_price=original_stop,
            current_stop_price=current_stop,
            selected_risk_fraction=selected_risk,
            position_volume=position_volume,
            reason=reason,
        )

    @staticmethod
    def _fail(
        status: PreBrokerValidationStatus,
        *,
        direction: str,
        reason: str,
        symbol: str | None = None,
        entry_price: float | None = None,
        original_stop_price: float | None = None,
        current_stop_price: float | None = None,
        selected_risk_fraction: float | None = None,
        selected_exit_mode: str | None = None,
        position_volume: float | None = None,
        projected_cash_risk: float | None = None,
        projected_equity_risk_fraction: float | None = None,
        account_equity: float | None = None,
    ) -> PreBrokerValidationResult:
        return PreBrokerValidationResult(
            status=status,
            direction=direction,
            symbol=symbol,
            entry_price=entry_price,
            original_stop_price=original_stop_price,
            current_stop_price=current_stop_price,
            selected_risk_fraction=selected_risk_fraction,
            selected_exit_mode=selected_exit_mode,
            position_volume=position_volume,
            projected_cash_risk=projected_cash_risk,
            projected_equity_risk_fraction=projected_equity_risk_fraction,
            account_equity=account_equity,
            order_authorized=False,
            reason_codes=(reason, "FAIL_CLOSED_NO_BROKER_ORDER_AUTHORIZATION"),
        )
