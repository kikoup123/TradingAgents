"""Causal price-delivery observations; expectations never count as events."""

from __future__ import annotations

import pandas as pd

from .csd import CSDEngine
from .fair_value import FairValueEngine
from .market_data import closed_bars
from .models import Direction
from .order_flow import OrderFlowEngine


class PriceDeliveryEngine:
    def __init__(self, *, pivot_span: int = 2, consolidation_window: int = 3) -> None:
        if consolidation_window < 2:
            raise ValueError("consolidation_window must be >= 2")
        self.structure = CSDEngine(pivot_span=pivot_span)
        self.valuation = FairValueEngine(pivot_span=pivot_span)
        self.consolidation_window = consolidation_window

    def analyze(self, bars: pd.DataFrame, *, timeframe: str, as_of=None) -> dict:
        data = closed_bars(bars, as_of)
        valuation = self.valuation.analyze(data, timeframe=timeframe)
        pivots = self.structure._confirmed_pivots(data)
        gaps = {g["formation_position"]: g for g in valuation["gaps"]}
        sequence, phase, direction = "UNRESOLVED", "UNRESOLVED", Direction.UNCONFIRMED
        cycle = "UNRESOLVED"
        events: list[dict] = []
        neutralized = None
        raids = []
        distribution = None
        rebalanced_at = None
        oc = None
        consolidations = []
        consumed: set[tuple[str, int]] = set()

        def record(position, kind, evidence):
            events.append(
                {
                    "position": position,
                    "time": data.index[position].isoformat(),
                    "event": kind,
                    "evidence": evidence,
                }
            )

        for i in range(len(data)):
            row = data.iloc[i]
            # A candidate efficient range needs both delivery directions and
            # a common traded overlap. Window size is an explicit heuristic.
            if i + 1 >= self.consolidation_window and distribution is None:
                window = data.iloc[i + 1 - self.consolidation_window : i + 1]
                overlap_low, overlap_high = float(window.low.max()), float(window.high.min())
                if (
                    overlap_low < overlap_high
                    and (window.close > window.open).any()
                    and (window.close < window.open).any()
                ):
                    oc = {
                        "low": float(window.low.min()),
                        "high": float(window.high.max()),
                        "overlap_low": overlap_low,
                        "overlap_high": overlap_high,
                        "start_position": i + 1 - self.consolidation_window,
                        "end_position": i,
                        "status": "CANDIDATE",
                    }
                    consolidations.append(oc)
                    phase = "CONSOLIDATION"
            newly_taken = []
            for side in ("high", "low"):
                for ref in pivots[side]:
                    key = (side, ref.source_position)
                    if ref.confirmed_position >= i or key in consumed:
                        continue
                    if row.high > ref.price if side == "high" else row.low < ref.price:
                        consumed.add(key)
                        newly_taken.append(ref)
                    elif sequence == "UNRESOLVED":
                        sequence = "ENGINEER"
                        record(i, "ENGINEER", ref.to_dict())
            if newly_taken:
                sides = {ref.side for ref in newly_taken}
                if len(sides) == 2:
                    record(
                        i,
                        "AMBIGUOUS_TWO_SIDED_RAID",
                        {"references": [r.to_dict() for r in newly_taken]},
                    )
                    neutralized = None
                    raids.clear()
                    cycle = "UNRESOLVED"
                else:
                    ref = max(newly_taken, key=lambda ref: ref.source_position)
                    neutralized = {"position": i, "reference": ref.to_dict()}
                    raids.append(neutralized)
                    cycle = "STOPS_TO_IMBALANCE"
                    if distribution is None:
                        sequence = "NEUTRALIZE"
                    record(i, "NEUTRALIZE", neutralized)
            gap = gaps.get(i)
            if gap is not None and distribution is None:
                direction = Direction(gap["direction"])
                phase = "EXPANSION"
                prior_oc = [
                    candidate
                    for candidate in consolidations
                    if candidate["end_position"] < gap["source_position"]
                ]
                if prior_oc:
                    oc = prior_oc[-1]
                    source_close = data.iloc[gap["source_position"]].close
                    if source_close > oc["high"] or source_close < oc["low"]:
                        oc = {**oc, "status": "DISPLACEMENT_CONFIRMED", "departure_position": i}
                # A gap only counts as distribution AFTER an observed raid.
                prior_raids = [
                    raid
                    for raid in raids
                    if raid["position"] < gap["source_position"]
                    and raid["reference"]["side"]
                    == ("LOW" if direction == Direction.BULLISH else "HIGH")
                ]
                if prior_raids:
                    distribution = gap
                    sequence = "DISTRIBUTE"
                    record(
                        i,
                        "DISTRIBUTE",
                        {
                            "gap": {
                                k: gap[k]
                                for k in (
                                    "timeframe",
                                    "direction",
                                    "low",
                                    "high",
                                    "formation_position",
                                    "formation_time",
                                    "structural_reference",
                                )
                            },
                            "raid": prior_raids[-1],
                        },
                    )
                else:
                    record(i, "EXPANSION", {"gap_formation_position": i})
            if distribution is not None and i > distribution["formation_position"]:
                bullish = distribution["direction"] == Direction.BULLISH.value
                broken = (
                    row.close < distribution["low"] if bullish else row.close > distribution["high"]
                )
                if broken:
                    phase, sequence = "TRANSITION", "INVALIDATED"
                    record(
                        i,
                        "DELIVERY_ARRAY_NEGATED",
                        {"gap_formation_position": distribution["formation_position"]},
                    )
                    distribution, rebalanced_at, neutralized = None, None, None
                    raids.clear()
                    cycle = "UNRESOLVED"
                    continue
                touched = row.low <= distribution["high"] and row.high >= distribution["low"]
                if touched and rebalanced_at is None:
                    rebalanced_at = i
                    sequence, phase, cycle = "REBALANCE", "RETRACEMENT", "IMBALANCE_TO_STOPS"
                    record(
                        i,
                        "REBALANCE",
                        {"gap_formation_position": distribution["formation_position"]},
                    )
                if rebalanced_at is not None and i > rebalanced_at and sequence == "REBALANCE":
                    continued = (
                        row.close > distribution["source_high"]
                        if bullish
                        else row.close < distribution["source_low"]
                    )
                    if continued:
                        sequence, phase = "REDISTRIBUTE", "EXPANSION"
                        record(
                            i,
                            "REACCUMULATION" if bullish else "REDISTRIBUTION",
                            {"gap_formation_position": distribution["formation_position"]},
                        )
                        distribution, rebalanced_at, neutralized = None, None, None
                        raids.clear()
        control = OrderFlowEngine().analyze(data, timeframe=timeframe).control
        return {
            "timeframe": timeframe,
            "phase": phase,
            "sequence": sequence,
            "direction": direction.value,
            "control": control.value,
            "cycle": cycle,
            "original_consolidation": oc,
            "events": events,
            "expected_next": {
                "ENGINEER": "NEUTRALIZE",
                "NEUTRALIZE": "DISTRIBUTE",
                "DISTRIBUTE": "REBALANCE",
                "REBALANCE": "REDISTRIBUTE",
            }.get(sequence),
            "reason_codes": [
                "CONSOLIDATION_IS_OVERLAP_PROXY",
                "NO_REVERSAL_FROM_ARRAY_TOUCH_ALONE",
            ],
        }
