"""Structural break-even management after a validated Londres entry.

Break-even is not distance based.  The stop moves to the exact deterministic
entry only after price leaves the post-CSD IOF entry range in trade direction,
a new confirmed execution-timeframe fractal forms after that exit, and price
then produces a body-close break of structure through that fractal in the
active IOF direction.

Spread, commission, fees, slippage and other transaction costs are deliberately
not added to the break-even stop.  They remain separate trade-cost accounting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

import pandas as pd

from .market_data import closed_bars


class BreakEvenStatus(str, Enum):
    WAIT_FOR_ACTIVE_TRADE = "WAIT_FOR_ACTIVE_TRADE"
    WAIT_FOR_IOF_EXIT = "WAIT_FOR_IOF_EXIT"
    WAIT_FOR_NEW_FRACTAL = "WAIT_FOR_NEW_FRACTAL"
    WAIT_FOR_BOS = "WAIT_FOR_BOS"
    BREAK_EVEN_TRIGGERED = "BREAK_EVEN_TRIGGERED"


@dataclass(frozen=True)
class BreakEvenContext:
    status: BreakEvenStatus
    direction: str
    entry_price: float | None
    entry_position: int | None
    entry_zone_low: float | None
    entry_zone_high: float | None
    original_stop_price: float | None
    original_projected_cash_risk: float | None
    original_projected_equity_risk_fraction: float | None
    original_risk_reward_ratio: float | None
    range_exit_position: int | None
    range_exit_time: str | None
    range_exit_close: float | None
    fractal_position: int | None
    fractal_time: str | None
    fractal_level: float | None
    fractal_confirmation_position: int | None
    fractal_confirmation_time: str | None
    bos_position: int | None
    bos_time: str | None
    bos_level: float | None
    bos_close: float | None
    break_even_price: float | None
    current_stop_price: float | None
    pivot_span: int
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["break_even_price_basis"] = "EXACT_DETERMINISTIC_ENTRY_PRICE"
        payload["cost_compensation_applied"] = False
        payload["spread_compensation_applied"] = False
        payload["commission_compensation_applied"] = False
        payload["transaction_cost_accounting"] = "SEPARATE_FROM_BREAK_EVEN_STOP_PLACEMENT"
        payload["net_zero_after_costs_guaranteed"] = False
        payload["original_trade_geometry_preserved"] = True
        payload["order_authorized"] = False
        return payload


class StructuralBreakEvenEngine:
    """Move the current stop to exact entry only after structural continuation."""

    def __init__(self, *, pivot_span: int = 2) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be >= 1")
        self.pivot_span = pivot_span

    def analyze(
        self,
        *,
        entry_execution: dict | None,
        trade_calculation: dict | None,
        executable_stop: dict | None,
        execution_bars: pd.DataFrame,
        as_of: pd.Timestamp | str | None = None,
    ) -> BreakEvenContext:
        entry_state = entry_execution or {}
        calculation = trade_calculation or {}
        stop_state = executable_stop or {}
        direction = str(entry_state.get("direction") or calculation.get("direction") or "UNCONFIRMED")
        entry_price = entry_state.get("exact_entry_price")
        entry_event = entry_state.get("entry_event") or {}
        zone = entry_state.get("entry_zone") or {}
        original_stop = stop_state.get("executable_stop_price")

        if (
            entry_state.get("status") != "ENTRY_TRIGGERED"
            or calculation.get("status") != "READY"
            or stop_state.get("status") != "READY"
            or direction not in {"BULLISH", "BEARISH"}
            or entry_price is None
            or entry_event.get("position") is None
            or zone.get("low") is None
            or zone.get("high") is None
            or original_stop is None
        ):
            return self._result(
                status=BreakEvenStatus.WAIT_FOR_ACTIVE_TRADE,
                direction=direction,
                entry_price=self._float_or_none(entry_price),
                original_stop_price=self._float_or_none(original_stop),
                calculation=calculation,
                reason_codes=("READY_PHASE15_TRADE_WITH_IOF_ENTRY_GEOMETRY_REQUIRED",),
            )

        data = closed_bars(execution_bars, as_of)
        entry = float(entry_price)
        entry_position = int(entry_event["position"])
        zone_low = float(zone["low"])
        zone_high = float(zone["high"])
        stop_price = float(original_stop)
        if entry_position < 0 or entry_position >= len(data) or zone_low >= zone_high:
            return self._result(
                status=BreakEvenStatus.WAIT_FOR_ACTIVE_TRADE,
                direction=direction,
                entry_price=entry,
                entry_position=entry_position,
                entry_zone_low=zone_low,
                entry_zone_high=zone_high,
                original_stop_price=stop_price,
                calculation=calculation,
                reason_codes=("ENTRY_EVENT_OR_IOF_ZONE_GEOMETRY_INVALID",),
            )

        exit_event = self._first_range_exit(
            data,
            direction=direction,
            entry_position=entry_position,
            zone_low=zone_low,
            zone_high=zone_high,
        )
        if exit_event is None:
            return self._result(
                status=BreakEvenStatus.WAIT_FOR_IOF_EXIT,
                direction=direction,
                entry_price=entry,
                entry_position=entry_position,
                entry_zone_low=zone_low,
                entry_zone_high=zone_high,
                original_stop_price=stop_price,
                current_stop_price=stop_price,
                calculation=calculation,
                reason_codes=(
                    "PRICE_HAS_NOT_BODY_CLOSED_OUTSIDE_IOF_RANGE_IN_TRADE_DIRECTION",
                    "LEAVING_IOF_RANGE_IS_REQUIRED_BUT_NOT_SUFFICIENT_FOR_BREAK_EVEN",
                ),
            )

        exit_position, exit_time, exit_close = exit_event
        fractal = self._first_new_fractal(
            data,
            direction=direction,
            after_position=exit_position,
        )
        if fractal is None:
            return self._result(
                status=BreakEvenStatus.WAIT_FOR_NEW_FRACTAL,
                direction=direction,
                entry_price=entry,
                entry_position=entry_position,
                entry_zone_low=zone_low,
                entry_zone_high=zone_high,
                original_stop_price=stop_price,
                current_stop_price=stop_price,
                calculation=calculation,
                range_exit_position=exit_position,
                range_exit_time=exit_time,
                range_exit_close=exit_close,
                reason_codes=(
                    "IOF_RANGE_EXIT_CONFIRMED_IN_TRADE_DIRECTION",
                    "WAIT_FOR_NEW_CONFIRMED_EXECUTION_TIMEFRAME_FRACTAL_AFTER_EXIT",
                ),
            )

        (
            fractal_position,
            fractal_time,
            fractal_level,
            confirmation_position,
            confirmation_time,
        ) = fractal
        bos = self._first_bos(
            data,
            direction=direction,
            level=fractal_level,
            after_position=confirmation_position,
        )
        if bos is None:
            return self._result(
                status=BreakEvenStatus.WAIT_FOR_BOS,
                direction=direction,
                entry_price=entry,
                entry_position=entry_position,
                entry_zone_low=zone_low,
                entry_zone_high=zone_high,
                original_stop_price=stop_price,
                current_stop_price=stop_price,
                calculation=calculation,
                range_exit_position=exit_position,
                range_exit_time=exit_time,
                range_exit_close=exit_close,
                fractal_position=fractal_position,
                fractal_time=fractal_time,
                fractal_level=fractal_level,
                fractal_confirmation_position=confirmation_position,
                fractal_confirmation_time=confirmation_time,
                reason_codes=(
                    "IOF_RANGE_EXIT_CONFIRMED_IN_TRADE_DIRECTION",
                    "NEW_EXECUTION_TIMEFRAME_FRACTAL_CONFIRMED_AFTER_IOF_EXIT",
                    "WAIT_FOR_BODY_CLOSE_BOS_THROUGH_FRACTAL_IN_IOF_DIRECTION",
                ),
            )

        bos_position, bos_time, bos_close = bos
        return self._result(
            status=BreakEvenStatus.BREAK_EVEN_TRIGGERED,
            direction=direction,
            entry_price=entry,
            entry_position=entry_position,
            entry_zone_low=zone_low,
            entry_zone_high=zone_high,
            original_stop_price=stop_price,
            current_stop_price=entry,
            calculation=calculation,
            range_exit_position=exit_position,
            range_exit_time=exit_time,
            range_exit_close=exit_close,
            fractal_position=fractal_position,
            fractal_time=fractal_time,
            fractal_level=fractal_level,
            fractal_confirmation_position=confirmation_position,
            fractal_confirmation_time=confirmation_time,
            bos_position=bos_position,
            bos_time=bos_time,
            bos_level=fractal_level,
            bos_close=bos_close,
            break_even_price=entry,
            reason_codes=(
                "IOF_RANGE_EXIT_CONFIRMED_IN_TRADE_DIRECTION",
                "NEW_EXECUTION_TIMEFRAME_FRACTAL_CONFIRMED_AFTER_IOF_EXIT",
                "BODY_CLOSE_BOS_CONFIRMED_IN_ACTIVE_IOF_DIRECTION",
                "BREAK_EVEN_SET_TO_EXACT_ENTRY_PRICE",
                "NO_SPREAD_COMMISSION_FEE_OR_TICK_COMPENSATION_ADDED_TO_BREAK_EVEN",
                "ORIGINAL_STOP_RISK_AND_RR_PRESERVED_FOR_ANALYTICS",
            ),
        )

    def _first_range_exit(
        self,
        data: pd.DataFrame,
        *,
        direction: str,
        entry_position: int,
        zone_low: float,
        zone_high: float,
    ) -> tuple[int, str, float] | None:
        for position in range(entry_position, len(data)):
            close = float(data.iloc[position]["close"])
            exited = (direction == "BEARISH" and close < zone_low) or (
                direction == "BULLISH" and close > zone_high
            )
            if exited:
                return position, self._time_label(data.index[position], position), close
        return None

    def _first_new_fractal(
        self,
        data: pd.DataFrame,
        *,
        direction: str,
        after_position: int,
    ) -> tuple[int, str, float, int, str] | None:
        span = self.pivot_span
        start = max(after_position + 1, span)
        for position in range(start, len(data) - span):
            row = data.iloc[position]
            left = data.iloc[position - span : position]
            right = data.iloc[position + 1 : position + span + 1]
            if direction == "BEARISH":
                level = float(row["low"])
                is_fractal = level < float(left["low"].min()) and level < float(
                    right["low"].min()
                )
            else:
                level = float(row["high"])
                is_fractal = level > float(left["high"].max()) and level > float(
                    right["high"].max()
                )
            if not is_fractal:
                continue

            confirmation_position = position + span
            return (
                position,
                self._time_label(data.index[position], position),
                level,
                confirmation_position,
                self._time_label(data.index[confirmation_position], confirmation_position),
            )
        return None

    @staticmethod
    def _first_bos(
        data: pd.DataFrame,
        *,
        direction: str,
        level: float,
        after_position: int,
    ) -> tuple[int, str, float] | None:
        for position in range(after_position + 1, len(data)):
            close = float(data.iloc[position]["close"])
            broken = (direction == "BEARISH" and close < level) or (
                direction == "BULLISH" and close > level
            )
            if broken:
                index_value = data.index[position]
                time_label = (
                    index_value.isoformat() if hasattr(index_value, "isoformat") else str(index_value)
                )
                return position, time_label, close
        return None

    def _result(
        self,
        *,
        status: BreakEvenStatus,
        direction: str,
        calculation: dict,
        entry_price: float | None = None,
        entry_position: int | None = None,
        entry_zone_low: float | None = None,
        entry_zone_high: float | None = None,
        original_stop_price: float | None = None,
        range_exit_position: int | None = None,
        range_exit_time: str | None = None,
        range_exit_close: float | None = None,
        fractal_position: int | None = None,
        fractal_time: str | None = None,
        fractal_level: float | None = None,
        fractal_confirmation_position: int | None = None,
        fractal_confirmation_time: str | None = None,
        bos_position: int | None = None,
        bos_time: str | None = None,
        bos_level: float | None = None,
        bos_close: float | None = None,
        break_even_price: float | None = None,
        current_stop_price: float | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> BreakEvenContext:
        return BreakEvenContext(
            status=status,
            direction=direction,
            entry_price=entry_price,
            entry_position=entry_position,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            original_stop_price=original_stop_price,
            original_projected_cash_risk=self._float_or_none(
                calculation.get("projected_cash_risk")
            ),
            original_projected_equity_risk_fraction=self._float_or_none(
                calculation.get("projected_equity_risk_fraction")
            ),
            original_risk_reward_ratio=self._float_or_none(
                calculation.get("risk_reward_ratio")
            ),
            range_exit_position=range_exit_position,
            range_exit_time=range_exit_time,
            range_exit_close=range_exit_close,
            fractal_position=fractal_position,
            fractal_time=fractal_time,
            fractal_level=fractal_level,
            fractal_confirmation_position=fractal_confirmation_position,
            fractal_confirmation_time=fractal_confirmation_time,
            bos_position=bos_position,
            bos_time=bos_time,
            bos_level=bos_level,
            bos_close=bos_close,
            break_even_price=break_even_price,
            current_stop_price=current_stop_price,
            pivot_span=self.pivot_span,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        return None if value is None else float(value)

    @staticmethod
    def _time_label(index_value: Any, position: int) -> str:
        if hasattr(index_value, "isoformat"):
            return index_value.isoformat()
        if index_value is not None:
            return str(index_value)
        return str(position)
