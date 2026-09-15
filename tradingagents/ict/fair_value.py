"""FVG geometry, structural fair valuation and observable pairing evidence.

OHLC can show a return and rejection; it cannot prove institutional orders were
paired. The distinction is preserved in field names and reason codes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from .csd import CSDEngine
from .market_data import closed_bars
from .models import Direction


@dataclass
class FairValueGap:
    timeframe: str
    direction: Direction
    low: float
    high: float
    consequent_encroachment: float
    source_position: int
    formation_position: int
    formation_time: str
    source_low: float
    source_high: float
    structural_reference: dict | None = None
    fair_valuation_point: float | None = None
    status: str = "OPEN"
    first_touch_position: int | None = None
    first_touch_time: str | None = None
    filled_position: int | None = None
    invalidated_position: int | None = None
    invalidated_time: str | None = None
    pairing_return_position: int | None = None
    pairing_return_time: str | None = None
    pairing_rejection_position: int | None = None
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        return payload


class FairValueEngine:
    def __init__(self, *, pivot_span: int = 2) -> None:
        self.structure = CSDEngine(pivot_span=pivot_span)

    def analyze(self, bars: pd.DataFrame, *, timeframe: str, as_of=None) -> dict:
        data = closed_bars(bars, as_of)
        pivots = self.structure._confirmed_pivots(data)
        gaps: list[FairValueGap] = []
        for position in range(2, len(data)):
            left, impulse, right = (
                data.iloc[position - 2],
                data.iloc[position - 1],
                data.iloc[position],
            )
            if right.low > left.high and impulse.close > impulse.open:
                direction, low, high = Direction.BULLISH, float(left.high), float(right.low)
            elif right.high < left.low and impulse.close < impulse.open:
                direction, low, high = Direction.BEARISH, float(right.high), float(left.low)
            else:
                continue
            source = position - 1
            # The reference must have been confirmed BEFORE the impulse candle.
            # A previous close already beyond it makes this a continuation gap,
            # not the gap that first closed through the valuation reference.
            refs = pivots["high" if direction == Direction.BULLISH else "low"]
            eligible = []
            for ref in refs:
                if ref.confirmed_position >= source:
                    continue
                earlier = data.iloc[ref.confirmed_position : source]["close"]
                crossed = (
                    impulse.close > ref.price and earlier.max() <= ref.price
                    if direction == Direction.BULLISH
                    else impulse.close < ref.price and earlier.min() >= ref.price
                )
                if crossed:
                    eligible.append(ref)
            reference = max(eligible, key=lambda ref: ref.source_position) if eligible else None
            gap = FairValueGap(
                timeframe=timeframe,
                direction=direction,
                low=low,
                high=high,
                consequent_encroachment=(low + high) / 2,
                source_position=source,
                formation_position=position,
                formation_time=data.index[position].isoformat(),
                source_low=float(impulse.low),
                source_high=float(impulse.high),
                structural_reference=reference.to_dict() if reference else None,
                fair_valuation_point=reference.price if reference else None,
                reason_codes=[
                    "THREE_CANDLE_FVG",
                    "STRUCTURAL_CLOSE_THROUGH" if reference else "NO_STRUCTURAL_CLOSE_THROUGH",
                ],
            )
            for j in range(position + 1, len(data)):
                row = data.iloc[j]
                invalid = row.close < low if direction == Direction.BULLISH else row.close > high
                if invalid:
                    gap.status = "INVALIDATED"
                    gap.invalidated_position = j
                    gap.invalidated_time = data.index[j].isoformat()
                    break
                touched = row.low <= high and row.high >= low
                if touched and gap.first_touch_position is None:
                    gap.first_touch_position = j
                    gap.first_touch_time = data.index[j].isoformat()
                    gap.status = "PARTIALLY_REBALANCED"
                filled = row.low <= low if direction == Direction.BULLISH else row.high >= high
                if touched and filled and gap.filled_position is None:
                    gap.filled_position = j
                    gap.status = "REBALANCED"
                # Keep structural valuation distinct from FVG midpoint and gap touch.
                if (
                    reference
                    and row.low <= reference.price <= row.high
                    and gap.pairing_return_position is None
                ):
                    gap.pairing_return_position = j
                    gap.pairing_return_time = data.index[j].isoformat()
                rejected = row.close > high if direction == Direction.BULLISH else row.close < low
                if (
                    gap.pairing_return_position is not None
                    and rejected
                    and gap.pairing_rejection_position is None
                ):
                    gap.pairing_rejection_position = j
            gaps.append(gap)
        usable = [g for g in gaps if g.status in {"OPEN", "PARTIALLY_REBALANCED"}]
        return {
            "timeframe": timeframe,
            "gaps": [gap.to_dict() for gap in gaps],
            "active_gaps": [gap.to_dict() for gap in usable],
            "structural_fvg_candidates": [
                gap.to_dict() for gap in usable if gap.structural_reference
            ],
            "reason_codes": ["PAIRING_IS_PRICE_ACTION_PROXY", "FVG_IS_CONTEXT_NOT_ENTRY_SIGNAL"],
        }
