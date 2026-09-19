"""Deterministic executable stop derived from the selected structural anchor.

Phase 10 defines the structural stop choices. Phase 13 supplies the exact entry.
This layer converts the Trader's hard-validated structural anchor into an actual
broker stop price only when an explicit symbol tick size and stop-buffer policy
are supplied. No default buffer is invented.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ExecutableStopStatus(str, Enum):
    READY = "READY"
    WAIT_FOR_ENTRY = "WAIT_FOR_ENTRY"
    WAIT_FOR_SELECTED_STRUCTURAL_STOP = "WAIT_FOR_SELECTED_STRUCTURAL_STOP"
    WAIT_FOR_TICK_SIZE = "WAIT_FOR_TICK_SIZE"
    WAIT_FOR_BUFFER_POLICY = "WAIT_FOR_BUFFER_POLICY"
    INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
    INVALID_BUFFER_POLICY = "INVALID_BUFFER_POLICY"
    INVALID_DIRECTIONAL_GEOMETRY = "INVALID_DIRECTIONAL_GEOMETRY"


@dataclass(frozen=True)
class ExecutableStopResult:
    status: ExecutableStopStatus
    direction: str
    entry_price: float | None
    selected_stop_source: str | None
    structural_anchor_price: float | None
    placement: str | None
    tick_size: float | None
    buffer_ticks: int | None
    buffer_price: float | None
    executable_stop_price: float | None
    stop_distance_price: float | None
    stop_distance_ticks: int | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["risk_sizing_ready"] = self.status == ExecutableStopStatus.READY
        payload["order_authorized"] = False
        payload["buffer_policy_authority"] = "EXPLICIT_SYMBOL_STOP_BUFFER_POLICY"
        payload["llm_may_set_executable_stop"] = False
        return payload


class ExecutableStopEngine:
    """Apply an explicit tick buffer beyond a validated structural stop anchor."""

    def calculate(
        self,
        *,
        entry_execution: dict | None,
        trader_stop_selection: dict | None,
        tick_size: float | None,
        buffer_ticks: int | None,
    ) -> ExecutableStopResult:
        entry_state = entry_execution or {}
        selection = trader_stop_selection or {}
        direction = str(entry_state.get("direction") or "UNCONFIRMED")
        entry_price = entry_state.get("exact_entry_price")

        if entry_state.get("status") != "ENTRY_TRIGGERED" or entry_price is None:
            return self._wait(
                ExecutableStopStatus.WAIT_FOR_ENTRY,
                direction=direction,
                reason="EXACT_PHASE13_ENTRY_REQUIRED",
            )

        entry = float(entry_price)
        if not selection.get("valid") or selection.get("selected_anchor_price") is None:
            return self._wait(
                ExecutableStopStatus.WAIT_FOR_SELECTED_STRUCTURAL_STOP,
                direction=direction,
                entry_price=entry,
                reason="HARD_VALIDATED_TRADER_STOP_SELECTION_REQUIRED",
            )

        source = selection.get("selected_source")
        anchor = float(selection["selected_anchor_price"])
        placement = selection.get("placement")

        if tick_size is None:
            return self._wait(
                ExecutableStopStatus.WAIT_FOR_TICK_SIZE,
                direction=direction,
                entry_price=entry,
                selected_stop_source=source,
                structural_anchor_price=anchor,
                placement=placement,
                reason="BROKER_SYMBOL_TICK_SIZE_REQUIRED",
            )
        tick = float(tick_size)
        if tick <= 0:
            return self._wait(
                ExecutableStopStatus.INVALID_TICK_SIZE,
                direction=direction,
                entry_price=entry,
                selected_stop_source=source,
                structural_anchor_price=anchor,
                placement=placement,
                tick_size=tick,
                reason="TICK_SIZE_MUST_BE_POSITIVE",
            )

        if buffer_ticks is None:
            return self._wait(
                ExecutableStopStatus.WAIT_FOR_BUFFER_POLICY,
                direction=direction,
                entry_price=entry,
                selected_stop_source=source,
                structural_anchor_price=anchor,
                placement=placement,
                tick_size=tick,
                reason="EXPLICIT_STOP_BUFFER_TICKS_REQUIRED",
            )
        if isinstance(buffer_ticks, bool) or not isinstance(buffer_ticks, int) or buffer_ticks < 1:
            return self._wait(
                ExecutableStopStatus.INVALID_BUFFER_POLICY,
                direction=direction,
                entry_price=entry,
                selected_stop_source=source,
                structural_anchor_price=anchor,
                placement=placement,
                tick_size=tick,
                buffer_ticks=buffer_ticks if isinstance(buffer_ticks, int) else None,
                reason="STOP_BUFFER_MUST_BE_AT_LEAST_ONE_WHOLE_TICK",
            )

        if direction == "BEARISH":
            if anchor <= entry:
                return self._invalid_geometry(
                    direction=direction,
                    entry_price=entry,
                    source=source,
                    anchor=anchor,
                    placement=placement,
                    tick_size=tick,
                    buffer_ticks=buffer_ticks,
                )
            raw_stop = anchor + buffer_ticks * tick
            executable_stop = self._ceil_to_tick(raw_stop, tick)
        elif direction == "BULLISH":
            if anchor >= entry:
                return self._invalid_geometry(
                    direction=direction,
                    entry_price=entry,
                    source=source,
                    anchor=anchor,
                    placement=placement,
                    tick_size=tick,
                    buffer_ticks=buffer_ticks,
                )
            raw_stop = anchor - buffer_ticks * tick
            executable_stop = self._floor_to_tick(raw_stop, tick)
        else:
            return self._invalid_geometry(
                direction=direction,
                entry_price=entry,
                source=source,
                anchor=anchor,
                placement=placement,
                tick_size=tick,
                buffer_ticks=buffer_ticks,
            )

        distance = abs(entry - executable_stop)
        distance_ticks = max(1, math.ceil((distance / tick) - 1e-12))
        return ExecutableStopResult(
            status=ExecutableStopStatus.READY,
            direction=direction,
            entry_price=entry,
            selected_stop_source=source,
            structural_anchor_price=anchor,
            placement=placement,
            tick_size=tick,
            buffer_ticks=buffer_ticks,
            buffer_price=buffer_ticks * tick,
            executable_stop_price=executable_stop,
            stop_distance_price=distance,
            stop_distance_ticks=distance_ticks,
            reason_codes=(
                "STRUCTURAL_STOP_SELECTED_BY_HARD_VALIDATED_TRADER_GATE",
                "EXECUTABLE_STOP_BUFFER_SUPPLIED_EXPLICITLY",
                "STOP_SNAPPED_OUTWARD_TO_BROKER_TICK_GRID",
                "RISK_SIZING_MAY_NOW_USE_ENTRY_TO_EXECUTABLE_STOP_RANGE",
            ),
        )

    @staticmethod
    def _ceil_to_tick(price: float, tick: float) -> float:
        return math.ceil((price / tick) - 1e-12) * tick

    @staticmethod
    def _floor_to_tick(price: float, tick: float) -> float:
        return math.floor((price / tick) + 1e-12) * tick

    def _invalid_geometry(
        self,
        *,
        direction: str,
        entry_price: float,
        source: str | None,
        anchor: float,
        placement: str | None,
        tick_size: float,
        buffer_ticks: int,
    ) -> ExecutableStopResult:
        return ExecutableStopResult(
            status=ExecutableStopStatus.INVALID_DIRECTIONAL_GEOMETRY,
            direction=direction,
            entry_price=entry_price,
            selected_stop_source=source,
            structural_anchor_price=anchor,
            placement=placement,
            tick_size=tick_size,
            buffer_ticks=buffer_ticks,
            buffer_price=buffer_ticks * tick_size,
            executable_stop_price=None,
            stop_distance_price=None,
            stop_distance_ticks=None,
            reason_codes=("STRUCTURAL_STOP_ANCHOR_IS_NOT_BEYOND_ENTRY_IN_INVALIDATION_DIRECTION",),
        )

    @staticmethod
    def _wait(
        status: ExecutableStopStatus,
        *,
        direction: str,
        reason: str,
        entry_price: float | None = None,
        selected_stop_source: str | None = None,
        structural_anchor_price: float | None = None,
        placement: str | None = None,
        tick_size: float | None = None,
        buffer_ticks: int | None = None,
    ) -> ExecutableStopResult:
        return ExecutableStopResult(
            status=status,
            direction=direction,
            entry_price=entry_price,
            selected_stop_source=selected_stop_source,
            structural_anchor_price=structural_anchor_price,
            placement=placement,
            tick_size=tick_size,
            buffer_ticks=buffer_ticks,
            buffer_price=(buffer_ticks * tick_size if buffer_ticks is not None and tick_size else None),
            executable_stop_price=None,
            stop_distance_price=None,
            stop_distance_ticks=None,
            reason_codes=(reason,),
        )
