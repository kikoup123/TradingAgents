"""HTF WHAT / LTF WHEN: bias, parent control and bounded liquidity runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .csd import CSDEngine
from .delivery import PriceDeliveryEngine
from .fair_value import FairValueEngine
from .liquidity import LiquidityEngine
from .market_data import closed_bars, comparable_time
from .models import Direction
from .order_flow import OrderFlowEngine

DIRECTIONAL = {Direction.BULLISH.value, Direction.BEARISH.value}
DEFAULT_HIERARCHY = ("6M", "3M", "1M", "1W", "1D", "4H", "1H", "15m", "5m", "1m")


def classify_liquidity_run(
    direction: str,
    control: str,
    *,
    opposing_matrix_reached: bool = False,
    crosses_parent_matrix: bool = False,
) -> dict:
    if direction not in DIRECTIONAL or control not in DIRECTIONAL:
        return {
            "classification": "UNRESOLVED",
            "target_scope": "UNRESOLVED",
            "reason_codes": ["DIRECTIONAL_IOF_REQUIRED"],
        }
    if direction != control or opposing_matrix_reached or crosses_parent_matrix:
        return {
            "classification": "HRLR",
            "target_scope": "IRL_OR_PARENT_MATRIX",
            "reason_codes": ["AGAINST_CONTROL_OR_PROTECTED_PARENT_ARRAY"],
        }
    return {
        "classification": "LRLR",
        "target_scope": "ERL",
        "reason_codes": ["WITH_CONFIRMED_IOF_WITHIN_PARENT_BOUNDARY"],
    }


class NarrativeEngine:
    def __init__(self, *, pivot_span: int = 2) -> None:
        self.order_flow = OrderFlowEngine()
        self.valuation = FairValueEngine(pivot_span=pivot_span)
        self.liquidity = LiquidityEngine(pivot_span=pivot_span)
        self.delivery = PriceDeliveryEngine(pivot_span=pivot_span)
        self.csd = CSDEngine(pivot_span=pivot_span)

    def analyze(
        self,
        timeframe_bars: Mapping[str, pd.DataFrame],
        *,
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        as_of=None,
    ) -> dict:
        if len(set(hierarchy)) != len(hierarchy):
            raise ValueError("hierarchy must list each timeframe once, highest first")
        if not timeframe_bars or set(timeframe_bars) - set(hierarchy):
            raise ValueError("supply bars with an explicit highest-to-lowest timeframe hierarchy")
        data = {
            tf: closed_bars(timeframe_bars[tf], as_of) for tf in hierarchy if tf in timeframe_bars
        }
        states = {}
        for tf, bars in data.items():
            flow = self.order_flow.analyze(bars, timeframe=tf)
            liquidity = self.liquidity.analyze(bars, timeframe=tf, order_flow_control=flow.control)
            values = self.valuation.analyze(bars, timeframe=tf)
            previous = bars.iloc[-2] if len(bars) >= 2 else None
            current = bars.iloc[-1]
            bullish = flow.control == Direction.BULLISH
            target = (
                float(previous.high if bullish else previous.low)
                if previous is not None and flow.control.value in DIRECTIONAL
                else None
            )
            reached = (
                bool(current.high >= target if bullish else current.low <= target)
                if target is not None
                else None
            )
            states[tf] = {
                "timeframe": tf,
                "as_of": bars.index[-1].isoformat(),
                "order_flow": flow.to_dict(),
                "control": flow.control.value,
                "curve": "BUY_SIDE_CURVE"
                if bullish
                else "SELL_SIDE_CURVE"
                if flow.control == Direction.BEARISH
                else "UNRESOLVED",
                "previous_candle_draw": {
                    "price": target,
                    "source_time": bars.index[-2].isoformat() if previous is not None else None,
                    "side": "HIGH" if bullish else "LOW" if target is not None else None,
                    "reached": reached,
                },
                "expected_delivery": "OLHC"
                if bullish
                else "OHLC"
                if flow.control == Direction.BEARISH
                else "UNRESOLVED",
                "liquidity": liquidity.to_dict(),
                "fair_value": values,
                "price_delivery": self.delivery.analyze(bars, timeframe=tf),
                "parent_timeframe": None,
                "parent_control": None,
                "parent_matrices": [],
                "profile": "UNRESOLVED",
                "reason_codes": [],
            }
        ordered = list(data)
        for n, tf in enumerate(ordered):
            state, bars = states[tf], data[tf]
            direction = state["control"]
            parent = states[ordered[n - 1]] if n else None
            state["parent_timeframe"] = parent["timeframe"] if parent else None
            state["parent_control"] = parent["control"] if parent else None
            # All available ancestors bound a local run, not only the adjacent TF.
            matrices = []
            for ancestor_tf in ordered[:n]:
                ancestor = states[ancestor_tf]
                if direction not in DIRECTIONAL:
                    continue
                desired = (
                    Direction.BEARISH.value
                    if direction == Direction.BULLISH.value
                    else Direction.BULLISH.value
                )
                arrays = []
                flow = ancestor["order_flow"]
                for r in flow["active_support_ranges"] + flow["active_resistance_ranges"]:
                    if r["direction"] == desired:
                        arrays.append(
                            {
                                "kind": "IOF_RANGE",
                                "low": r["low"],
                                "high": r["high"],
                                "available_time": r["confirmed_time"],
                            }
                        )
                for g in ancestor["fair_value"]["active_gaps"]:
                    if g["direction"] == desired:
                        arrays.append(
                            {
                                "kind": "FVG",
                                "low": g["low"],
                                "high": g["high"],
                                "available_time": g["formation_time"],
                            }
                        )
                for array in arrays:
                    available = comparable_time(array["available_time"], bars.index)
                    subsequent = bars.loc[bars.index > available]
                    touches = subsequent.loc[
                        (subsequent.low <= array["high"]) & (subsequent.high >= array["low"])
                    ]
                    price = float(bars.iloc[-1].close)
                    ahead = (
                        array["high"] >= price
                        if direction == Direction.BULLISH.value
                        else array["low"] <= price
                    )
                    if not ahead and touches.empty:
                        continue
                    matrices.append(
                        {
                            **array,
                            "timeframe": ancestor_tf,
                            "direction": desired,
                            "reached": not touches.empty,
                            "reached_time": touches.index[0].isoformat()
                            if not touches.empty
                            else None,
                            "distance": max(array["low"] - price, price - array["high"], 0.0),
                        }
                    )
            state["parent_matrices"] = matrices
            touched = [m for m in matrices if m["reached"]]
            draw = state["liquidity"]["active_draw"]
            price = float(bars.iloc[-1].close)
            crosses = bool(
                draw
                and any(
                    min(price, draw["price"]) <= m["high"] and max(price, draw["price"]) >= m["low"]
                    for m in matrices
                )
            )
            state["liquidity_run"] = classify_liquidity_run(
                direction,
                direction,
                opposing_matrix_reached=bool(touched),
                crosses_parent_matrix=crosses,
            )
            state["parent_relative_run"] = classify_liquidity_run(
                direction, parent["control"] if parent else "UNCONFIRMED"
            )
            if direction not in DIRECTIONAL or (parent and parent["control"] not in DIRECTIONAL):
                state["profile"] = "UNRESOLVED"
                state["reason_codes"].append("WAIT_FOR_DIRECTIONAL_CONTROL")
            elif touched:
                state["profile"] = "AT_PARENT_MATRIX_WAIT_FOR_SHIFT"
                state["reason_codes"].append("MATRIX_TOUCH_IS_NOT_REVERSAL")
            elif parent and parent["control"] != direction:
                state["profile"] = "RETRACEMENT"
                state["reason_codes"].append("LOCAL_COUNTER_FLOW_DOES_NOT_INVALIDATE_PARENT")
            else:
                state["profile"] = "CONTINUATION"
            # A reversal needs its own observed CSD and new post-CSD IOFC.
            # Locate the parent array in the NEW direction, even though it is
            # no longer an opposing array after local control has realigned.
            state["reversal"] = self._reversal(bars, tf, states, ordered[:n], direction)
            if state["reversal"]["confirmed"]:
                state["profile"] = "REVERSAL_CONFIRMED"
            state["narrative_draw"] = self._draw(state, price)
        highest, lowest = states[ordered[0]], states[ordered[-1]]
        aligned = highest["control"] in DIRECTIONAL and all(
            s["control"] == highest["control"] for s in states.values()
        )
        ready = bool(
            len(states) >= 2
            and aligned
            and lowest["liquidity_run"]["classification"] == "LRLR"
            and lowest["narrative_draw"]
        )
        return {
            "hierarchy": ordered,
            "missing_higher_timeframes": list(hierarchy[: hierarchy.index(ordered[0])]),
            "bias": highest["control"],
            "bias_timeframe": ordered[0],
            "execution_timeframe": ordered[-1],
            "alignment": "ALIGNED" if aligned else "MIXED_OR_UNRESOLVED",
            "context_confirmed": ready,
            "timeframes": states,
            "reason_codes": ["HTF_WHAT_LTF_WHEN", "EXPECTATIONS_ARE_NOT_OBSERVED_DELIVERY"],
        }

    @staticmethod
    def _draw(state: dict, price: float) -> dict | None:
        cycle = state["price_delivery"]["cycle"]
        if cycle == "STOPS_TO_IMBALANCE":
            # A return is a retracement target, not a change to HTF bias.
            bullish = state["control"] == Direction.BULLISH.value
            gaps = [
                g
                for g in state["fair_value"]["active_gaps"]
                if g["direction"] == state["control"]
                and (g["high"] < price if bullish else g["low"] > price)
            ]
            if gaps:
                g = min(gaps, key=lambda g: min(abs(price - g["low"]), abs(price - g["high"])))
                return {
                    "kind": "IRL_FVG",
                    "low": g["low"],
                    "high": g["high"],
                    "timeframe": g["timeframe"],
                    "purpose": "REBALANCE",
                }
            return None
        if state["liquidity_run"]["classification"] == "HRLR":
            ahead = [m for m in state["parent_matrices"] if not m["reached"]]
            return (
                {**min(ahead, key=lambda m: m["distance"]), "purpose": "PARENT_MATRIX_BOUNDARY"}
                if ahead
                else None
            )
        draw = state["liquidity"]["active_draw"]
        return {**draw, "purpose": "LIQUIDITY_OBJECTIVE"} if draw else None

    def _reversal(self, bars, tf, states, ancestors, direction):
        result = {"confirmed": False, "csd": None, "post_csd_iofc": None, "matrix": None}
        csd = self.csd.analyze(bars, timeframe=tf).latest_event
        if csd is None or csd.direction.value != direction:
            return result
        after = bars.iloc[csd.confirmation_position + 1 :]
        broken = (
            (after.close < csd.protected_extreme).any()
            if direction == Direction.BULLISH.value
            else (after.close > csd.protected_extreme).any()
        )
        if broken:
            return result
        iofc = self.order_flow.find_iofc_after(
            bars, anchor_position=csd.confirmation_position, expected_direction=csd.direction
        )
        if not iofc.confirmed:
            return result
        raid_time = bars.index[csd.raid_position]
        for ancestor_tf in reversed(ancestors):
            parent = states[ancestor_tf]
            if parent["control"] != direction:
                continue
            arrays = [
                {**r, "available_time": r["confirmed_time"], "kind": "IOF_RANGE"}
                for r in parent["order_flow"]["active_support_ranges"]
                + parent["order_flow"]["active_resistance_ranges"]
            ] + [
                {**g, "available_time": g["formation_time"], "kind": "FVG"}
                for g in parent["fair_value"]["gaps"]
                if g["status"] != "INVALIDATED"
            ]
            for r in arrays:
                if (
                    r["direction"] != direction
                    or comparable_time(r["available_time"], bars.index) >= raid_time
                ):
                    continue
                raid = bars.iloc[csd.raid_position]
                if raid.low <= r["high"] and raid.high >= r["low"]:
                    return {
                        "confirmed": True,
                        "csd": csd.to_dict(),
                        "post_csd_iofc": iofc.to_dict(),
                        "matrix": {
                            "timeframe": ancestor_tf,
                            "low": r["low"],
                            "high": r["high"],
                            "kind": r["kind"],
                        },
                    }
        return result
