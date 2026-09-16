"""Deterministic position sizing from structural stop distance.

The Londres risk model never accepts a user/LLM-selected trading volume.  Once
an exact entry and executable stop are known, the engine measures the price
range in broker ticks (and pips when a pip size is supplied), converts that
range into cash risk per volume unit, then rounds the maximum safe volume DOWN
to the broker's allowed step.

Account risk is a policy input, not a Trader discretion.  This module therefore
requires an explicit account equity and risk fraction instead of inventing a
percentage.  Broker/symbol metadata supplies tick value and volume constraints.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class RiskSizingStatus(str, Enum):
    READY = "READY"
    WAIT_FOR_ENTRY_PRICE = "WAIT_FOR_ENTRY_PRICE"
    WAIT_FOR_EXECUTABLE_STOP = "WAIT_FOR_EXECUTABLE_STOP"
    INVALID_DIRECTIONAL_GEOMETRY = "INVALID_DIRECTIONAL_GEOMETRY"
    BELOW_BROKER_MINIMUM = "BELOW_BROKER_MINIMUM"


@dataclass(frozen=True)
class InstrumentRiskSpec:
    """Broker-resolved sizing metadata for one tradable symbol.

    ``tick_value_per_volume_unit`` is the cash P/L for one tick of movement for
    one ``volume_unit`` (for example one futures contract or one lot).  The
    broker adapter must resolve this in the account currency before sizing.
    """

    symbol: str
    tick_size: float
    tick_value_per_volume_unit: float
    volume_step: float
    min_volume: float
    max_volume: float
    volume_unit: str
    pip_size: float | None = None

    def __post_init__(self) -> None:
        numeric_positive = {
            "tick_size": self.tick_size,
            "tick_value_per_volume_unit": self.tick_value_per_volume_unit,
            "volume_step": self.volume_step,
            "min_volume": self.min_volume,
            "max_volume": self.max_volume,
        }
        for name, value in numeric_positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.min_volume > self.max_volume:
            raise ValueError("min_volume cannot exceed max_volume")
        if self.pip_size is not None and self.pip_size <= 0:
            raise ValueError("pip_size must be > 0 when supplied")


@dataclass(frozen=True)
class AccountRiskPolicy:
    account_equity: float
    risk_fraction: float
    max_risk_cash: float | None = None

    def __post_init__(self) -> None:
        if self.account_equity <= 0:
            raise ValueError("account_equity must be > 0")
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk_fraction must be within (0, 1]")
        if self.max_risk_cash is not None and self.max_risk_cash <= 0:
            raise ValueError("max_risk_cash must be > 0 when supplied")

    @property
    def risk_cash_budget(self) -> float:
        percentage_budget = self.account_equity * self.risk_fraction
        if self.max_risk_cash is None:
            return percentage_budget
        return min(percentage_budget, self.max_risk_cash)


@dataclass(frozen=True)
class RiskSizingResult:
    status: RiskSizingStatus
    direction: str
    symbol: str
    entry_price: float | None
    stop_price: float | None
    stop_distance_price: float | None
    stop_distance_ticks: int | None
    stop_distance_pips: float | None
    risk_cash_budget: float
    risk_cash_per_volume_unit: float | None
    raw_volume: float | None
    final_volume: float | None
    volume_unit: str
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["manual_volume_allowed"] = False
        payload["sizing_authority"] = "DETERMINISTIC_STOP_RANGE_RISK_ENGINE"
        return payload


class RiskSizingEngine:
    """Calculate maximum safe volume from entry-to-stop risk range."""

    def calculate(
        self,
        *,
        direction: str,
        entry_price: float | None,
        stop_price: float | None,
        instrument: InstrumentRiskSpec,
        policy: AccountRiskPolicy,
    ) -> RiskSizingResult:
        if entry_price is None:
            return self._waiting(
                RiskSizingStatus.WAIT_FOR_ENTRY_PRICE,
                direction=direction,
                instrument=instrument,
                policy=policy,
                entry_price=None,
                stop_price=stop_price,
                reason="EXACT_ENTRY_PRICE_REQUIRED_FOR_RANGE_SIZING",
            )
        if stop_price is None:
            return self._waiting(
                RiskSizingStatus.WAIT_FOR_EXECUTABLE_STOP,
                direction=direction,
                instrument=instrument,
                policy=policy,
                entry_price=float(entry_price),
                stop_price=None,
                reason="EXECUTABLE_STOP_PRICE_REQUIRED_FOR_RANGE_SIZING",
            )

        entry = float(entry_price)
        stop = float(stop_price)
        geometry_valid = (
            direction == "BULLISH" and stop < entry
        ) or (
            direction == "BEARISH" and stop > entry
        )
        if not geometry_valid:
            return self._waiting(
                RiskSizingStatus.INVALID_DIRECTIONAL_GEOMETRY,
                direction=direction,
                instrument=instrument,
                policy=policy,
                entry_price=entry,
                stop_price=stop,
                reason="STOP_IS_NOT_BEYOND_ENTRY_IN_INVALIDATION_DIRECTION",
            )

        distance = abs(entry - stop)
        # Always round the risk distance UP to whole broker ticks so the engine
        # cannot understate risk because of floating-point or off-grid prices.
        ticks = max(1, math.ceil((distance / instrument.tick_size) - 1e-12))
        cash_per_volume = ticks * instrument.tick_value_per_volume_unit
        budget = policy.risk_cash_budget
        raw_volume = budget / cash_per_volume
        capped_volume = min(raw_volume, instrument.max_volume)
        final_volume = self._floor_to_step(capped_volume, instrument.volume_step)

        pips = distance / instrument.pip_size if instrument.pip_size is not None else None
        if final_volume + 1e-12 < instrument.min_volume:
            return RiskSizingResult(
                status=RiskSizingStatus.BELOW_BROKER_MINIMUM,
                direction=direction,
                symbol=instrument.symbol,
                entry_price=entry,
                stop_price=stop,
                stop_distance_price=distance,
                stop_distance_ticks=ticks,
                stop_distance_pips=pips,
                risk_cash_budget=budget,
                risk_cash_per_volume_unit=cash_per_volume,
                raw_volume=raw_volume,
                final_volume=None,
                volume_unit=instrument.volume_unit,
                projected_cash_risk=None,
                projected_equity_risk_fraction=None,
                reason_codes=(
                    "SAFE_VOLUME_BELOW_BROKER_MINIMUM",
                    "DO_NOT_ROUND_UP_AND_EXCEED_RISK_BUDGET",
                ),
            )

        final_volume = min(final_volume, instrument.max_volume)
        projected_cash_risk = final_volume * cash_per_volume
        projected_fraction = projected_cash_risk / policy.account_equity
        return RiskSizingResult(
            status=RiskSizingStatus.READY,
            direction=direction,
            symbol=instrument.symbol,
            entry_price=entry,
            stop_price=stop,
            stop_distance_price=distance,
            stop_distance_ticks=ticks,
            stop_distance_pips=pips,
            risk_cash_budget=budget,
            risk_cash_per_volume_unit=cash_per_volume,
            raw_volume=raw_volume,
            final_volume=final_volume,
            volume_unit=instrument.volume_unit,
            projected_cash_risk=projected_cash_risk,
            projected_equity_risk_fraction=projected_fraction,
            reason_codes=(
                "VOLUME_DERIVED_FROM_STOP_RANGE_NOT_USER_INPUT",
                "VOLUME_ROUNDED_DOWN_TO_BROKER_STEP",
                "PROJECTED_RISK_NOT_ABOVE_POLICY_BUDGET",
            ),
        )

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        steps = math.floor((value / step) + 1e-12)
        return steps * step

    @staticmethod
    def _waiting(
        status: RiskSizingStatus,
        *,
        direction: str,
        instrument: InstrumentRiskSpec,
        policy: AccountRiskPolicy,
        entry_price: float | None,
        stop_price: float | None,
        reason: str,
    ) -> RiskSizingResult:
        return RiskSizingResult(
            status=status,
            direction=direction,
            symbol=instrument.symbol,
            entry_price=entry_price,
            stop_price=stop_price,
            stop_distance_price=None,
            stop_distance_ticks=None,
            stop_distance_pips=None,
            risk_cash_budget=policy.risk_cash_budget,
            risk_cash_per_volume_unit=None,
            raw_volume=None,
            final_volume=None,
            volume_unit=instrument.volume_unit,
            projected_cash_risk=None,
            projected_equity_risk_fraction=None,
            reason_codes=(reason, "MANUAL_VOLUME_FORBIDDEN"),
        )
