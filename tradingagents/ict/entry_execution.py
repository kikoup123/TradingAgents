"""Deterministic Londres entry trigger from the post-CSD IOF range.

The execution sequence is intentionally strict:

    SMT -> CSD -> post-CSD IOFC -> wait for price to return into that IOF range

The IOF confirmation range itself is the entry zone. The first causal return
into that range creates the exact entry event. No FVG, OTE, Unicorn, or other
entry model is substituted for this rule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

import pandas as pd

from .market_data import closed_bars


class EntryExecutionStatus(str, Enum):
    WAIT_FOR_VALIDATED_IOF_RANGE = "WAIT_FOR_VALIDATED_IOF_RANGE"
    WAIT_FOR_RETRACE = "WAIT_FOR_RETRACE"
    ENTRY_TRIGGERED = "ENTRY_TRIGGERED"
    RANGE_SKIPPED_BY_GAP = "RANGE_SKIPPED_BY_GAP"


@dataclass(frozen=True)
class EntryZone:
    low: float
    high: float
    direction: str
    source_position: int
    confirmed_position: int
    source_time: str | None
    confirmed_time: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntryEvent:
    price: float
    position: int
    time: str
    fill_basis: str
    zone_low: float
    zone_high: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntryExecutionContext:
    status: EntryExecutionStatus
    direction: str
    zone: EntryZone | None
    entry: EntryEvent | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "direction": self.direction,
            "entry_zone": self.zone.to_dict() if self.zone else None,
            "entry_event": self.entry.to_dict() if self.entry else None,
            "exact_entry_price": self.entry.price if self.entry else None,
            "entry_triggered": self.entry is not None,
            "entry_rule": "FIRST_RETURN_INTO_CONFIRMED_POST_CSD_IOF_RANGE",
            "order_authorized": False,
            "reason_codes": list(self.reason_codes),
        }


class PostCSDIOFEntryEngine:
    """Trigger entry on the first return into the confirmed post-CSD IOF range."""

    def analyze(
        self,
        context: dict,
        execution_bars: pd.DataFrame,
        *,
        as_of: pd.Timestamp | str | None = None,
    ) -> EntryExecutionContext:
        data = closed_bars(execution_bars, as_of)
        iofc = context.get("post_csd_iofc") or {}
        gate = context.get("execution_gate") or {}
        direction = str(gate.get("direction") or iofc.get("expected_direction") or "UNCONFIRMED")
        confirmation_range = iofc.get("confirmation_range") or {}

        if (
            not iofc.get("confirmed")
            or direction not in {"BULLISH", "BEARISH"}
            or confirmation_range.get("low") is None
            or confirmation_range.get("high") is None
            or confirmation_range.get("confirmed_position") is None
            or confirmation_range.get("source_position") is None
        ):
            return EntryExecutionContext(
                status=EntryExecutionStatus.WAIT_FOR_VALIDATED_IOF_RANGE,
                direction=direction,
                zone=None,
                entry=None,
                reason_codes=("CONFIRMED_POST_CSD_IOF_RANGE_REQUIRED",),
            )

        zone = EntryZone(
            low=float(confirmation_range["low"]),
            high=float(confirmation_range["high"]),
            direction=direction,
            source_position=int(confirmation_range["source_position"]),
            confirmed_position=int(confirmation_range["confirmed_position"]),
            source_time=confirmation_range.get("source_time"),
            confirmed_time=confirmation_range.get("confirmed_time"),
        )
        if zone.low >= zone.high:
            raise ValueError("post-CSD IOF entry range must have low < high")
        if zone.confirmed_position >= len(data):
            return EntryExecutionContext(
                status=EntryExecutionStatus.WAIT_FOR_RETRACE,
                direction=direction,
                zone=zone,
                entry=None,
                reason_codes=("WAIT_FOR_BARS_AFTER_IOFC_CONFIRMATION",),
            )

        for position in range(zone.confirmed_position + 1, len(data)):
            row = data.iloc[position]
            candle_open = float(row["open"])
            candle_high = float(row["high"])
            candle_low = float(row["low"])

            if direction == "BEARISH":
                # After bearish IOFC price is below resistance. A gap that opens
                # above the entire range skips the first executable retracement
                # price; do not invent a fill from OHLC data.
                if candle_open > zone.high:
                    return EntryExecutionContext(
                        status=EntryExecutionStatus.RANGE_SKIPPED_BY_GAP,
                        direction=direction,
                        zone=zone,
                        entry=None,
                        reason_codes=("BEARISH_IOF_RANGE_GAPPED_THROUGH_WITHOUT_CAUSAL_FILL",),
                    )
                if zone.low <= candle_open <= zone.high:
                    price = candle_open
                    basis = "BAR_OPEN_ALREADY_INSIDE_IOF_RANGE"
                elif candle_open < zone.low and candle_high >= zone.low:
                    price = zone.low
                    basis = "FIRST_TOUCH_OF_BEARISH_IOF_RANGE_LOW"
                else:
                    continue
            else:
                # After bullish IOFC price is above support. A gap that opens
                # below the entire range likewise has no reconstructable fill.
                if candle_open < zone.low:
                    return EntryExecutionContext(
                        status=EntryExecutionStatus.RANGE_SKIPPED_BY_GAP,
                        direction=direction,
                        zone=zone,
                        entry=None,
                        reason_codes=("BULLISH_IOF_RANGE_GAPPED_THROUGH_WITHOUT_CAUSAL_FILL",),
                    )
                if zone.low <= candle_open <= zone.high:
                    price = candle_open
                    basis = "BAR_OPEN_ALREADY_INSIDE_IOF_RANGE"
                elif candle_open > zone.high and candle_low <= zone.high:
                    price = zone.high
                    basis = "FIRST_TOUCH_OF_BULLISH_IOF_RANGE_HIGH"
                else:
                    continue

            timestamp = data.index[position]
            time_label = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
            entry = EntryEvent(
                price=float(price),
                position=position,
                time=time_label,
                fill_basis=basis,
                zone_low=zone.low,
                zone_high=zone.high,
            )
            return EntryExecutionContext(
                status=EntryExecutionStatus.ENTRY_TRIGGERED,
                direction=direction,
                zone=zone,
                entry=entry,
                reason_codes=(
                    "SMT_CSD_IOF_SEQUENCE_CONFIRMED",
                    "PRICE_RETURNED_TO_POST_CSD_IOF_RANGE",
                    "EXACT_ENTRY_PRICE_FROZEN_AT_FIRST_CAUSAL_RANGE_RETURN",
                ),
            )

        return EntryExecutionContext(
            status=EntryExecutionStatus.WAIT_FOR_RETRACE,
            direction=direction,
            zone=zone,
            entry=None,
            reason_codes=("WAIT_FOR_PRICE_RETURN_INTO_POST_CSD_IOF_RANGE",),
        )
