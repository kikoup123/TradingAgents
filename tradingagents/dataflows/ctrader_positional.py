"""Deterministic Trading Sand positional-entry model.

The positional path mirrors the LONDRES HTF SUITE fractal rules while preserving
the video-derived protected-swing concept. A positional signal is only armed
when the qualifying HTF candle is closed, its mapped LTF CSD already exists,
and a protected swing formed before the next HTF candle opened.

Broker execution remains deliberately disabled.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL
from tradingagents.dataflows.ctrader_csd import _find_bearish_csd, _find_bullish_csd
from tradingagents.dataflows.ctrader_entry import _find_ranges

FRACTAL_TIMEFRAME_PAIRS = {
    "W1": "H4",
    "D1": "H1",
    "H4": "M15",
    "H1": "M5",
    "M30": "M3",
    "M15": "M1",
}

SYMBOLS = {
    "XAUUSD": "XAUUSD",
    "GOLD": "XAUUSD",
    "NASDAQ": "NASDAQ",
    "NQ": "NASDAQ",
    "US100": "NASDAQ",
    "USTECH100": "NASDAQ",
    "US TECH 100": "NASDAQ",
    "US500": "US500",
    "ES": "US500",
    "SP500": "US500",
    "SPX500": "US500",
}


def _normalize(symbol: str) -> str:
    key = symbol.strip().upper()
    if key not in SYMBOLS:
        raise ValueError(f"Unsupported positional-entry symbol: {symbol}")
    return SYMBOLS[key]


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sort_bars(bars: list[dict]) -> list[dict]:
    return sorted(bars, key=lambda bar: _dt(bar["time"]))


def _fetch(symbol: str, timeframe: str, count: int) -> list[dict]:
    response = requests.get(
        f"{BRIDGE_URL}/bars",
        params={"symbol": symbol, "timeframe": timeframe, "count": count},
        timeout=50,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return _sort_bars(payload["bars"])


def _base_result(
    *,
    symbol: str | None,
    direction: str,
    htf_timeframe: str,
    ltf_timeframe: str,
    status: str,
    state_trace: list[str],
    reason: str,
    fallback_to_unicorn: bool,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "direction": direction,
        "htf_timeframe": htf_timeframe,
        "ltf_timeframe": ltf_timeframe,
        "status": status,
        "state_trace": state_trace,
        "reason": reason,
        "entry_model": "POSITIONAL",
        "entry_model_confirmed": False,
        "entry_gate_passed": False,
        "entry_model_invalidated": status == "POSITIONAL_STOPPED",
        "fallback_to_unicorn": fallback_to_unicorn,
        "exact_order_price_defined": False,
        "execution_allowed": False,
        "order_placement": False,
    }


def _directional_csd(
    bars: list[dict],
    *,
    direction: str,
    pivot_window: int,
    confirmation_bars: int,
):
    if direction == "bullish":
        confirmed, _ = _find_bullish_csd(bars, pivot_window, confirmation_bars)
    else:
        confirmed, _ = _find_bearish_csd(bars, pivot_window, confirmation_bars)
    return confirmed


def _protected_price(event: dict, direction: str) -> float | None:
    key = "protected_low" if direction == "bullish" else "protected_high"
    value = event.get(key)
    return None if value is None else float(value)


def _c2_direction(c1: dict, c2: dict) -> str | None:
    """Exact LONDRES HTF SUITE C2 sweep/reclaim rule."""
    c1_high = float(c1["high"])
    c1_low = float(c1["low"])
    c2_high = float(c2["high"])
    c2_low = float(c2["low"])
    c2_close = float(c2["close"])

    if c2_high > c1_high and c2_close <= c1_high:
        return "bearish"
    if c2_low < c1_low and c2_close >= c1_low:
        return "bullish"
    return None


def _opposite_model(reference: dict, current: dict, direction: str) -> bool:
    """Pine f_opposite_model_at_index translated to chronological bars."""
    if direction == "bearish":
        return (
            float(current["low"]) < float(reference["low"])
            and float(current["close"]) > float(reference["low"])
        )
    return (
        float(current["high"]) > float(reference["high"])
        and float(current["close"]) < float(reference["high"])
    )


def _follow_through_failure(reference: dict, current: dict, direction: str) -> dict[str, Any]:
    if direction == "bearish":
        level_fail = float(current["high"]) > float(reference["high"])
    else:
        level_fail = float(current["low"]) < float(reference["low"])

    opposite_fail = _opposite_model(reference, current, direction)
    return {
        "failed": level_fail or opposite_fail,
        "level_fail": level_fail,
        "opposite_model_fail": opposite_fail,
        "reference_time": reference["time"],
        "current_time": current["time"],
    }


def _resolve_fractal_stage(
    htf: list[dict],
    *,
    direction: str,
) -> dict[str, Any] | None:
    """Resolve whether the next open is C3 or C4 in the Pine fractal model."""
    if len(htf) < 3:
        return None

    qualifying = htf[-2]
    next_candle = htf[-1]

    # Standard C2 -> C3 positional entry.
    c1 = htf[-3]
    if _c2_direction(c1, qualifying) == direction:
        return {
            "stage": "C2",
            "entry_label": "C3_OPEN",
            "model_c1": c1,
            "model_c2": qualifying,
            "qualifying_candle": qualifying,
            "next_candle": next_candle,
            "prior_follow_through": None,
        }

    # Advanced continuation: valid C3 closure -> C4 positional entry.
    if len(htf) < 4:
        return None

    c1 = htf[-4]
    c2 = htf[-3]
    c3 = htf[-2]
    if _c2_direction(c1, c2) != direction:
        return None

    c3_state = _follow_through_failure(c2, c3, direction)
    if c3_state["failed"]:
        return None

    return {
        "stage": "C3",
        "entry_label": "C4_OPEN",
        "model_c1": c1,
        "model_c2": c2,
        "qualifying_candle": c3,
        "next_candle": next_candle,
        "prior_follow_through": c3_state,
    }


def _wick_eq_price(
    direction: str,
    candle: dict,
    *,
    min_tick: float = 1e-12,
) -> float | None:
    """Exact directional-wick EQ geometry from LONDRES HTF SUITE."""
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    body = abs(close - open_price)
    min_body = max(body, min_tick)

    if direction == "bearish":
        wick_top = high
        wick_bottom = max(open_price, close)
        wick_size = wick_top - wick_bottom
    else:
        wick_top = min(open_price, close)
        wick_bottom = low
        wick_size = wick_top - wick_bottom

    if wick_size < min_body * 0.40:
        return None
    return (wick_top + wick_bottom) / 2.0


def _tspot_zone(direction: str, candle: dict) -> dict[str, float]:
    """Exact T-Spot geometry projected from the qualifying candle."""
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    if direction == "bearish":
        top = (high + close) / 2.0
        bottom = close
    else:
        top = close
        bottom = (close + low) / 2.0

    return {
        "top": max(top, bottom),
        "bottom": min(top, bottom),
        "mid": (top + bottom) / 2.0,
    }


def _price_created_inside_candle(
    bars: list[dict],
    *,
    protected_price: float,
    direction: str,
) -> bool:
    field = "low" if direction == "bullish" else "high"
    return any(float(bar[field]) == protected_price for bar in bars)


def _swing_intact_after(
    bars: list[dict],
    *,
    confirmation_time: str,
    protected_price: float,
    direction: str,
) -> bool:
    confirmation_dt = _dt(confirmation_time)
    later = [bar for bar in bars if _dt(bar["time"]) > confirmation_dt]
    if direction == "bullish":
        return not any(float(bar["low"]) < protected_price for bar in later)
    return not any(float(bar["high"]) > protected_price for bar in later)


def _eq_covers(
    protected_price: float,
    *,
    eq: float | None,
    direction: str,
) -> bool:
    if eq is None:
        return False
    if direction == "bullish":
        return protected_price <= eq
    return protected_price >= eq


def _tspot_covers(
    protected_price: float,
    *,
    tspot: dict[str, float],
    direction: str,
) -> bool:
    if direction == "bullish":
        return protected_price <= tspot["bottom"]
    return protected_price >= tspot["top"]


def _entry_side_valid(
    protected_price: float,
    *,
    entry_price: float,
    direction: str,
) -> bool:
    if direction == "bullish":
        return protected_price < entry_price
    return protected_price > entry_price


def _candidate(
    *,
    source: str,
    protected_price: float,
    confirmation_time: str,
    eq: float | None,
    tspot: dict[str, float],
    entry_price: float,
    direction: str,
    pre_open_bars: list[dict],
    detail: dict | None = None,
) -> dict[str, Any]:
    eq_cover = _eq_covers(protected_price, eq=eq, direction=direction)
    tspot_cover = _tspot_covers(protected_price, tspot=tspot, direction=direction)
    return {
        "source": source,
        "protected_swing": float(protected_price),
        "confirmation_time": confirmation_time,
        "eq_available": eq is not None,
        "eq_covers": eq_cover,
        "tspot_covers": tspot_cover,
        # The video permits contextual flexibility around EQ. Therefore EQ is
        # not a hard binary veto: a swing is geometrically valid if it protects
        # either the directional wick EQ or the full expected T-Spot wick zone.
        "geometry_valid": eq_cover or tspot_cover,
        "entry_side_valid": _entry_side_valid(
            float(protected_price),
            entry_price=entry_price,
            direction=direction,
        ),
        "intact_before_open": _swing_intact_after(
            pre_open_bars,
            confirmation_time=confirmation_time,
            protected_price=float(protected_price),
            direction=direction,
        ),
        "detail": detail,
    }


def _select_protected_swing(
    *,
    qualifying_bars: list[dict],
    csd_event: dict,
    direction: str,
    eq: float | None,
    tspot: dict[str, float],
    entry_price: float,
) -> tuple[dict | None, list[dict]]:
    candidates: list[dict] = []
    csd_confirmation = csd_event.get("confirmation_time")
    csd_protected = _protected_price(csd_event, direction)

    if csd_confirmation is not None and csd_protected is not None:
        candidates.append(
            _candidate(
                source="CSD_PROTECTED_SWING",
                protected_price=csd_protected,
                confirmation_time=csd_confirmation,
                eq=eq,
                tspot=tspot,
                entry_price=entry_price,
                direction=direction,
                pre_open_bars=qualifying_bars,
                detail={
                    "raid_time": csd_event.get("raid_time"),
                    "csd_threshold": csd_event.get("csd_threshold"),
                },
            )
        )

    if csd_confirmation is not None:
        csd_confirmation_dt = _dt(csd_confirmation)
        for continuation in _find_ranges(qualifying_bars, direction):
            source_time = continuation.get("source_start_time")
            confirmation_time = continuation.get("confirmation_time")
            protected = continuation.get("protected_swing")
            if (
                source_time is None
                or confirmation_time is None
                or protected is None
                or _dt(source_time) < csd_confirmation_dt
                or _dt(confirmation_time) <= csd_confirmation_dt
            ):
                continue

            candidates.append(
                _candidate(
                    source="POST_CSD_CONTINUATION_PROTECTED_SWING",
                    protected_price=float(protected),
                    confirmation_time=confirmation_time,
                    eq=eq,
                    tspot=tspot,
                    entry_price=entry_price,
                    direction=direction,
                    pre_open_bars=qualifying_bars,
                    detail={
                        "source_start_time": source_time,
                        "source_end_time": continuation.get("source_end_time"),
                        "source_range_low": continuation.get("source_range_low"),
                        "source_range_high": continuation.get("source_range_high"),
                    },
                )
            )

    valid = [
        item
        for item in candidates
        if item["geometry_valid"]
        and item["entry_side_valid"]
        and item["intact_before_open"]
    ]
    if not valid:
        return None, candidates

    # Once geometry is valid, prefer the most recently confirmed protected
    # structure, matching the video's continuation logic rather than simply
    # choosing the tightest numerical stop.
    selected = max(
        valid,
        key=lambda item: (
            _dt(item["confirmation_time"]),
            item["source"] == "POST_CSD_CONTINUATION_PROTECTED_SWING",
        ),
    )
    return selected, candidates


def _three_bar_pivot(
    bars: list[dict],
    index: int,
    *,
    field: str,
    find_low: bool,
) -> bool:
    if index <= 0 or index >= len(bars) - 1:
        return False

    value = float(bars[index][field])
    left = float(bars[index - 1][field])
    right = float(bars[index + 1][field])
    return value < left and value < right if find_low else value > left and value > right


def _projection_structure_anchor(
    ltf_bars: list[dict],
    *,
    direction: str,
    c2: dict,
    c2_end_time: datetime,
    htf_duration: timedelta,
) -> dict[str, Any] | None:
    """Translate the Pine structural projection-anchor search."""
    c2_start = _dt(c2["time"])
    c2_extreme = float(c2["high"] if direction == "bearish" else c2["low"])
    extreme_field = "high" if direction == "bearish" else "low"

    inside_c2 = [
        bar
        for bar in ltf_bars
        if c2_start <= _dt(bar["time"]) < c2_end_time
    ]
    extreme_candidates = [
        bar
        for bar in inside_c2
        if (
            float(bar[extreme_field]) >= c2_extreme
            if direction == "bearish"
            else float(bar[extreme_field]) <= c2_extreme
        )
    ]
    if not extreme_candidates:
        return None

    # Pine scans newest -> oldest and stops on the first bar containing the
    # C2 extreme, so use the latest matching LTF bar.
    extreme_bar = max(extreme_candidates, key=lambda bar: _dt(bar["time"]))
    extreme_time = _dt(extreme_bar["time"])
    search_start = c2_start - htf_duration

    # Include the extreme bar as the right-hand neighbour of a pivot that
    # formed immediately before the C2 extreme. Pine's low[k-1]/high[k-1]
    # check has access to that bar even though the anchor itself must precede it.
    window = [
        bar
        for bar in ltf_bars
        if search_start <= _dt(bar["time"]) <= extreme_time
    ]
    if len(window) < 3:
        return None

    field = "low" if direction == "bearish" else "high"
    find_low = direction == "bearish"
    pivots = [
        window[index]
        for index in range(1, len(window) - 1)
        if _dt(window[index]["time"]) < extreme_time
        and _three_bar_pivot(window, index, field=field, find_low=find_low)
    ]
    if not pivots:
        return None

    # Same as Pine's first hit while scanning backward: closest prior pivot.
    anchor_bar = max(pivots, key=lambda bar: _dt(bar["time"]))
    anchor = float(anchor_bar[field])
    return {
        "price": anchor,
        "time": anchor_bar["time"],
        "extreme_price": c2_extreme,
        "extreme_time": extreme_bar["time"],
    }


def _fractal_structure_std_minus_two(
    ltf_bars: list[dict],
    *,
    direction: str,
    c2: dict,
    c2_end_time: datetime,
    htf_duration: timedelta,
    entry_price: float,
    htf_timeframe: str,
) -> dict[str, Any] | None:
    anchor = _projection_structure_anchor(
        ltf_bars,
        direction=direction,
        c2=c2,
        c2_end_time=c2_end_time,
        htf_duration=htf_duration,
    )
    if anchor is None:
        return None

    zero = float(anchor["price"])
    extreme = float(anchor["extreme_price"])
    range_size = abs(extreme - zero)
    if range_size <= 0:
        return None

    target = zero - 2.0 * range_size if direction == "bearish" else zero + 2.0 * range_size
    directionally_valid = target < entry_price if direction == "bearish" else target > entry_price
    if not directionally_valid:
        return None

    return {
        "label": "STD_-2",
        "timeframe": htf_timeframe,
        "zero_reference": zero,
        "one_reference": extreme,
        "range_size": range_size,
        "multiplier": -2.0,
        "price": target,
        "source": "TV_FRACTAL_STRUCTURE_STD",
        "structure_anchor_time": anchor["time"],
        "c2_extreme_time": anchor["extreme_time"],
    }


def _csd_std_minus_two(
    htf_closed_bars: list[dict],
    *,
    direction: str,
    pivot_window: int,
    confirmation_bars: int,
    entry_price: float,
    htf_timeframe: str,
) -> dict[str, Any] | None:
    """Keep the older CSD-range target explicit for comparison/backward compatibility."""
    event = _directional_csd(
        htf_closed_bars,
        direction=direction,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )
    if event is None:
        return None

    threshold = event.get("csd_threshold")
    protected = _protected_price(event, direction)
    if threshold is None or protected is None:
        return None

    threshold = float(threshold)
    protected = float(protected)
    if direction == "bearish":
        range_size = protected - threshold
        target = threshold - 2.0 * range_size
        directionally_valid = target < entry_price
    else:
        range_size = threshold - protected
        target = threshold + 2.0 * range_size
        directionally_valid = target > entry_price

    if range_size <= 0 or not directionally_valid:
        return None

    return {
        "label": "STD_-2",
        "timeframe": htf_timeframe,
        "zero_reference": threshold,
        "protected_extreme": protected,
        "range_size": range_size,
        "multiplier": 2.0,
        "price": target,
        "source": "LEGACY_HTF_CSD_STANDARD_DEVIATION",
        "source_confirmation_time": event.get("confirmation_time"),
    }


def _risk_reward(*, entry_price: float, stop_price: float, target_price: float) -> float | None:
    risk = abs(entry_price - stop_price)
    reward = abs(target_price - entry_price)
    if risk <= 0:
        return None
    return reward / risk


def _position_outcome(
    post_open_bars: list[dict],
    *,
    direction: str,
    stop_price: float,
    target_price: float,
) -> dict[str, Any]:
    for bar in post_open_bars:
        if direction == "bullish":
            stop_touched = float(bar["low"]) <= stop_price
            target_touched = float(bar["high"]) >= target_price
        else:
            stop_touched = float(bar["high"]) >= stop_price
            target_touched = float(bar["low"]) <= target_price

        if stop_touched and target_touched:
            return {
                "status": "AMBIGUOUS_INTRABAR",
                "time": bar["time"],
                "reason": (
                    "Stop and target were both touched inside one lower-timeframe "
                    "bar; OHLC data cannot establish which occurred first."
                ),
            }
        if stop_touched:
            return {
                "status": "STOPPED",
                "time": bar["time"],
                "reason": "Selected protected swing was violated after entry.",
            }
        if target_touched:
            return {
                "status": "TARGET_HIT",
                "time": bar["time"],
                "reason": "TV fractal structural STD -2 objective was reached.",
            }

    return {
        "status": "ACTIVE",
        "time": None,
        "reason": "Neither stop nor TV fractal structural STD -2 target has been reached.",
    }


def _trail_candidate(
    post_open_bars: list[dict],
    *,
    direction: str,
    initial_stop: float,
) -> dict[str, Any] | None:
    if len(post_open_bars) < 2:
        return None

    candidates = []
    for continuation in _find_ranges(post_open_bars, direction):
        protected = continuation.get("protected_swing")
        confirmation_time = continuation.get("confirmation_time")
        if protected is None or confirmation_time is None:
            continue

        protected = float(protected)
        tighter = protected > initial_stop if direction == "bullish" else protected < initial_stop
        if not tighter:
            continue
        if not _swing_intact_after(
            post_open_bars,
            confirmation_time=confirmation_time,
            protected_price=protected,
            direction=direction,
        ):
            continue

        candidates.append(
            {
                "protected_swing": protected,
                "confirmation_time": confirmation_time,
                "source": "NEW_CONFIRMED_POST_ENTRY_CONTINUATION",
                "automatic_move": False,
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: _dt(item["confirmation_time"]))


def evaluate_positional_from_bars(
    htf_bars: list[dict],
    ltf_bars: list[dict],
    *,
    direction: str,
    htf_timeframe: str,
    ltf_timeframe: str | None = None,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
    symbol: str | None = None,
    min_tick: float = 1e-12,
    assume_sorted: bool = False,
) -> dict[str, Any]:
    """Evaluate C3/C4 positional entry from chronological HTF/LTF OHLC bars."""

    direction = direction.strip().lower()
    htf_timeframe = htf_timeframe.strip().upper()
    expected_ltf = FRACTAL_TIMEFRAME_PAIRS.get(htf_timeframe)
    ltf_timeframe = (ltf_timeframe or expected_ltf or "").strip().upper()

    if direction not in {"bullish", "bearish"}:
        raise ValueError("direction must be bullish or bearish")
    if expected_ltf is None:
        raise ValueError(f"Unsupported positional HTF: {htf_timeframe}")
    if ltf_timeframe != expected_ltf:
        raise ValueError(
            f"{htf_timeframe} positional entries require {expected_ltf}, not {ltf_timeframe}"
        )
    if pivot_window < 1 or pivot_window > 10:
        raise ValueError("pivot_window must be between 1 and 10")
    if confirmation_bars < 1:
        raise ValueError("confirmation_bars must be >= 1")
    if min_tick <= 0:
        raise ValueError("min_tick must be > 0")

    states = ["IDLE"]
    if assume_sorted:
        htf = htf_bars
        ltf = ltf_bars
    else:
        htf = _sort_bars(htf_bars)
        ltf = _sort_bars(ltf_bars)

    if len(htf) < 3:
        return _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_HTF_FRACTAL",
            state_trace=states,
            reason="C1, a closed qualifying HTF candle, and the next HTF open are required.",
            fallback_to_unicorn=True,
        )

    stage = _resolve_fractal_stage(htf, direction=direction)
    if stage is None:
        return _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_HTF_FRACTAL",
            state_trace=states,
            reason=(
                "No valid LONDRES C2 sweep/reclaim or surviving C3 continuation "
                "exists for the requested direction."
            ),
            fallback_to_unicorn=True,
        )

    c1 = stage["model_c1"]
    model_c2 = stage["model_c2"]
    qualifying = stage["qualifying_candle"]
    next_candle = stage["next_candle"]

    qualifying_start = _dt(qualifying["time"])
    next_open_time = _dt(next_candle["time"])
    model_c2_start = _dt(model_c2["time"])
    if next_open_time <= qualifying_start:
        raise ValueError("HTF bars must have strictly increasing timestamps")

    htf_duration = next_open_time - qualifying_start
    if htf_duration.total_seconds() <= 0:
        raise ValueError("HTF duration must be positive")

    model_c2_end = model_c2_start + htf_duration
    entry_price = float(next_candle["open"])
    eq = _wick_eq_price(direction, qualifying, min_tick=min_tick)
    tspot = _tspot_zone(direction, qualifying)

    states.extend(
        [
            "HTF_FRACTAL_CONFIRMED",
            f"{stage['stage']}_CLOSED",
        ]
    )

    pre_open = [bar for bar in ltf if _dt(bar["time"]) < next_open_time]
    qualifying_ltf = [
        bar
        for bar in pre_open
        if qualifying_start <= _dt(bar["time"]) < next_open_time
    ]
    if len(qualifying_ltf) < 2:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_LTF_DATA",
            state_trace=states,
            reason="Insufficient mapped-LTF bars inside the qualifying HTF candle.",
            fallback_to_unicorn=True,
        )
        result.update({"equilibrium": eq, "tspot": tspot, "fractal_stage": stage["stage"]})
        return result

    csd_event = _directional_csd(
        pre_open,
        direction=direction,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )
    if csd_event is None:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_LTF_CSD",
            state_trace=states,
            reason="No directional mapped-LTF CSD was confirmed before the next HTF open.",
            fallback_to_unicorn=True,
        )
        result.update({"equilibrium": eq, "tspot": tspot, "fractal_stage": stage["stage"]})
        return result

    raid_time = csd_event.get("raid_time")
    csd_confirmation_time = csd_event.get("confirmation_time")
    csd_protected = _protected_price(csd_event, direction)

    # C2 -> C3 positional entries require the CSD/protected swing to be born
    # inside C2. For a valid C3 -> C4 continuation, the video model permits an
    # already-formed CSD/protected swing from the active C2/C3 sequence, as long
    # as it still exists before C4 opens. Requiring a brand-new CSD inside C3
    # incorrectly eliminates those C4 positional entries.
    csd_window_start = (
        qualifying_start
        if stage["stage"] == "C2"
        else model_c2_start
    )
    structural_window_ltf = [
        bar
        for bar in pre_open
        if csd_window_start <= _dt(bar["time"]) < next_open_time
    ]
    csd_ready_before_open = (
        raid_time is not None
        and csd_confirmation_time is not None
        and csd_window_start <= _dt(raid_time) < next_open_time
        and csd_window_start <= _dt(csd_confirmation_time) < next_open_time
        and csd_protected is not None
        and _price_created_inside_candle(
            structural_window_ltf,
            protected_price=csd_protected,
            direction=direction,
        )
    )
    if not csd_ready_before_open:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_CSD_BEFORE_POSITIONAL_OPEN",
            state_trace=states,
            reason=(
                "The directional mapped-LTF CSD/protected swing was not fully "
                f"formed inside the active fractal sequence before {stage['entry_label']}."
            ),
            fallback_to_unicorn=True,
        )
        result.update(
            {
                "equilibrium": eq,
                "tspot": tspot,
                "fractal_stage": stage["stage"],
                "csd": csd_event,
                "csd_window_start": csd_window_start.isoformat(),
            }
        )
        return result

    states.append("CSD_CONFIRMED")

    selected, candidates = _select_protected_swing(
        qualifying_bars=structural_window_ltf,
        csd_event=csd_event,
        direction=direction,
        eq=eq,
        tspot=tspot,
        entry_price=entry_price,
    )
    if selected is None:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_VALID_PROTECTED_SWING",
            state_trace=states,
            reason=(
                "No intact protected swing on the correct side of the entry "
                "protects the qualifying candle's directional wick EQ or T-Spot."
            ),
            fallback_to_unicorn=True,
        )
        result.update(
            {
                "equilibrium": eq,
                "tspot": tspot,
                "fractal_stage": stage["stage"],
                "csd": csd_event,
                "protected_swing_candidates": candidates,
            }
        )
        return result

    states.extend(["PROTECTED_SWING_FOUND", "POSITIONAL_ARMED"])

    structure_target = _fractal_structure_std_minus_two(
        ltf,
        direction=direction,
        c2=model_c2,
        c2_end_time=model_c2_end,
        htf_duration=htf_duration,
        entry_price=entry_price,
        htf_timeframe=htf_timeframe,
    )
    legacy_csd_target = _csd_std_minus_two(
        htf[:-1],
        direction=direction,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
        entry_price=entry_price,
        htf_timeframe=htf_timeframe,
    )

    if structure_target is None:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_TV_FRACTAL_STD_TARGET",
            state_trace=states,
            reason=(
                "The positional structure is armed, but the Pine structural "
                "anchor required for the fractal STD -2 target is unavailable."
            ),
            fallback_to_unicorn=True,
        )
        result.update(
            {
                "equilibrium": eq,
                "tspot": tspot,
                "fractal_stage": stage["stage"],
                "csd": csd_event,
                "selected_protected_swing": selected,
                "protected_swing_candidates": candidates,
                "target_models": {
                    "tv_fractal_structure": None,
                    "legacy_csd": legacy_csd_target,
                },
            }
        )
        return result

    stop_price = float(selected["protected_swing"])
    target_price = float(structure_target["price"])
    rr = _risk_reward(
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )

    states.extend([stage["entry_label"], "POSITIONAL_ACTIVE"])
    post_open = [bar for bar in ltf if _dt(bar["time"]) >= next_open_time]
    outcome = _position_outcome(
        post_open,
        direction=direction,
        stop_price=stop_price,
        target_price=target_price,
    )
    trail_bars = post_open
    if outcome["status"] != "ACTIVE" and outcome.get("time") is not None:
        outcome_time = _dt(outcome["time"])
        trail_bars = [
            bar
            for bar in post_open
            if _dt(bar["time"]) < outcome_time
        ]

    trail = _trail_candidate(
        trail_bars,
        direction=direction,
        initial_stop=stop_price,
    )

    if outcome["status"] == "STOPPED":
        status = "POSITIONAL_STOPPED"
        states.append("STOPPED")
        entry_gate_passed = False
        invalidated = True
    elif outcome["status"] == "TARGET_HIT":
        status = "POSITIONAL_TARGET_HIT"
        states.append("TARGET_HIT")
        entry_gate_passed = False
        invalidated = False
    elif outcome["status"] == "AMBIGUOUS_INTRABAR":
        status = "POSITIONAL_OUTCOME_AMBIGUOUS"
        states.append("OUTCOME_AMBIGUOUS")
        entry_gate_passed = False
        invalidated = False
    else:
        status = "POSITIONAL_ACTIVE"
        entry_gate_passed = True
        invalidated = False

    follow_through = _follow_through_failure(qualifying, next_candle, direction)

    return {
        "symbol": symbol,
        "direction": direction,
        "htf_timeframe": htf_timeframe,
        "ltf_timeframe": ltf_timeframe,
        "status": status,
        "state_trace": states,
        "entry_model": "POSITIONAL",
        "entry_model_confirmed": True,
        "entry_gate_passed": entry_gate_passed,
        "entry_model_invalidated": invalidated,
        "fallback_to_unicorn": False,
        "fractal_stage": stage["stage"],
        "entry_candle_label": stage["entry_label"],
        "fractal_context": {
            "c1": c1,
            "c2": model_c2,
            "qualifying_candle": qualifying,
            "prior_follow_through": stage["prior_follow_through"],
            "observed_next_candle_follow_through": follow_through,
        },
        "qualifying_htf_candle": {
            "time": qualifying["time"],
            "open": float(qualifying["open"]),
            "high": float(qualifying["high"]),
            "low": float(qualifying["low"]),
            "close": float(qualifying["close"]),
            "directional_wick_equilibrium": eq,
            "tspot": tspot,
        },
        # Backward-compatible field name; value now means directional wick EQ.
        "equilibrium": eq,
        "tspot": tspot,
        "next_htf_candle_open": {
            "time": next_candle["time"],
            "price": entry_price,
            "label": stage["entry_label"],
        },
        "csd": csd_event,
        "protected_swing_candidates": candidates,
        "selected_protected_swing": selected,
        "exact_order_price": entry_price,
        "exact_order_price_defined": True,
        "entry_rule": (
            "NEXT_HTF_OPEN_AFTER_VALID_FRACTAL_CLOSURE_CSD_AND_PROTECTED_SWING"
        ),
        "stop_reference": stop_price,
        "stop_rule": (
            "Below selected protected swing"
            if direction == "bullish"
            else "Above selected protected swing"
        ),
        "target": structure_target,
        "target_rule": "TV_FRACTAL_STRUCTURE_STD_-2",
        "target_models": {
            "tv_fractal_structure": structure_target,
            "legacy_csd": legacy_csd_target,
        },
        "selected_target_model": "TV_FRACTAL_STRUCTURE_STD",
        "risk_reward": rr,
        "position_outcome": outcome,
        "trail_stop_candidate": trail,
        "trail_rule": (
            "Optional only after a new confirmed continuation protected swing; "
            "never trail to an unconfirmed pivot."
        ),
        "do_not_chase_after_open": True,
        "execution_allowed": False,
        "order_placement": False,
        "reason": (
            "Exact HTF fractal context is valid, the mapped-LTF CSD and protected "
            f"swing existed before {stage['entry_label']}, and the stop protects "
            "the directional wick EQ/T-Spot geometry."
        ),
    }


def evaluate_positional_entry(
    *,
    symbol: str,
    direction: str,
    htf_timeframe: str = "H1",
    count: int = 500,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
    min_tick: float = 1e-12,
) -> dict[str, Any]:
    """Fetch the mapped HTF/LTF pair from cTrader and evaluate the model."""

    symbol = _normalize(symbol)
    htf_timeframe = htf_timeframe.strip().upper()
    ltf_timeframe = FRACTAL_TIMEFRAME_PAIRS.get(htf_timeframe)
    if ltf_timeframe is None:
        raise ValueError(f"Unsupported positional HTF: {htf_timeframe}")
    if count < 50 or count > 2000:
        raise ValueError("count must be between 50 and 2000")

    htf_bars = _fetch(symbol, htf_timeframe, count)
    ltf_bars = _fetch(symbol, ltf_timeframe, count)

    return evaluate_positional_from_bars(
        htf_bars,
        ltf_bars,
        direction=direction,
        htf_timeframe=htf_timeframe,
        ltf_timeframe=ltf_timeframe,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
        symbol=symbol,
        min_tick=min_tick,
    )
