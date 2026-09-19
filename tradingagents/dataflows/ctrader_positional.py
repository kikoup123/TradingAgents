"""Deterministic Trading Sand positional-entry model.

The positional entry is an advanced alternative to waiting for a new intra-candle
entry model.  It is only armed when the previous higher-timeframe candle has
already closed with a confirmed lower-timeframe CSD and a protected swing that
is structurally valid relative to the previous candle's equilibrium.

The model is intentionally execution-safe: it defines the signal, stop reference,
and HTF STD -2 objective, but never authorizes a broker order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL
from tradingagents.dataflows.ctrader_csd import (
    _find_bearish_csd,
    _find_bullish_csd,
)
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
        params={
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count,
        },
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
        confirmed, _ = _find_bullish_csd(
            bars,
            pivot_window,
            confirmation_bars,
        )
    else:
        confirmed, _ = _find_bearish_csd(
            bars,
            pivot_window,
            confirmation_bars,
        )
    return confirmed


def _protected_price(event: dict, direction: str) -> float | None:
    key = "protected_low" if direction == "bullish" else "protected_high"
    value = event.get(key)
    return None if value is None else float(value)


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


def _eq_valid(
    protected_price: float,
    *,
    eq: float,
    direction: str,
) -> bool:
    if direction == "bullish":
        return protected_price <= eq
    return protected_price >= eq


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
    eq: float,
    entry_price: float,
    direction: str,
    pre_open_bars: list[dict],
    detail: dict | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "protected_swing": float(protected_price),
        "confirmation_time": confirmation_time,
        "eq_valid": _eq_valid(
            float(protected_price),
            eq=eq,
            direction=direction,
        ),
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
    c2_bars: list[dict],
    csd_event: dict,
    direction: str,
    eq: float,
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
                entry_price=entry_price,
                direction=direction,
                pre_open_bars=c2_bars,
                detail={
                    "raid_time": csd_event.get("raid_time"),
                    "csd_threshold": csd_event.get("csd_threshold"),
                },
            )
        )

    if csd_confirmation is not None:
        csd_confirmation_dt = _dt(csd_confirmation)
        for continuation in _find_ranges(c2_bars, direction):
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
                    entry_price=entry_price,
                    direction=direction,
                    pre_open_bars=c2_bars,
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
        if item["eq_valid"]
        and item["entry_side_valid"]
        and item["intact_before_open"]
    ]
    if not valid:
        return None, candidates

    selected = max(
        valid,
        key=lambda item: (
            _dt(item["confirmation_time"]),
            item["source"] == "POST_CSD_CONTINUATION_PROTECTED_SWING",
        ),
    )
    return selected, candidates


def _htf_std_minus_two(
    htf_closed_bars: list[dict],
    *,
    direction: str,
    pivot_window: int,
    confirmation_bars: int,
    entry_price: float,
) -> dict[str, Any] | None:
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
        "timeframe": None,
        "zero_reference": threshold,
        "protected_extreme": protected,
        "range_size": range_size,
        "multiplier": 2.0,
        "price": target,
        "source": "HTF_CSD_STANDARD_DEVIATION",
        "source_confirmation_time": event.get("confirmation_time"),
    }


def _risk_reward(
    *,
    entry_price: float,
    stop_price: float,
    target_price: float,
) -> float | None:
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
                "reason": "HTF STD -2 objective was reached.",
            }

    return {
        "status": "ACTIVE",
        "time": None,
        "reason": "Neither stop nor HTF STD -2 target has been reached.",
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
        tighter = (
            protected > initial_stop
            if direction == "bullish"
            else protected < initial_stop
        )
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
) -> dict[str, Any]:
    """Evaluate a positional entry from already-fetched chronological OHLC bars."""

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
            f"{htf_timeframe} positional entries require {expected_ltf}, "
            f"not {ltf_timeframe}"
        )
    if pivot_window < 1 or pivot_window > 10:
        raise ValueError("pivot_window must be between 1 and 10")
    if confirmation_bars < 1:
        raise ValueError("confirmation_bars must be >= 1")

    states = ["IDLE"]
    htf = _sort_bars(htf_bars)
    ltf = _sort_bars(ltf_bars)

    if len(htf) < 2:
        return _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_HTF_CANDLE_CLOSE",
            state_trace=states,
            reason="At least one closed HTF candle and the next HTF open are required.",
            fallback_to_unicorn=True,
        )

    c2 = htf[-2]
    c3 = htf[-1]
    c2_time = _dt(c2["time"])
    c3_time = _dt(c3["time"])
    if c3_time <= c2_time:
        raise ValueError("HTF bars must have strictly increasing timestamps")

    c2_high = float(c2["high"])
    c2_low = float(c2["low"])
    entry_price = float(c3["open"])
    if c2_high <= c2_low:
        raise ValueError("qualifying HTF candle must have high > low")

    states.extend(["HTF_SETUP_FOUND", "HTF_CANDLE_CLOSED"])
    eq = (c2_high + c2_low) / 2.0

    pre_open = [bar for bar in ltf if _dt(bar["time"]) < c3_time]
    c2_ltf = [
        bar
        for bar in pre_open
        if c2_time <= _dt(bar["time"]) < c3_time
    ]
    if len(c2_ltf) < 2:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_LTF_DATA",
            state_trace=states,
            reason="Insufficient LTF bars inside the qualifying HTF candle.",
            fallback_to_unicorn=True,
        )
        result["equilibrium"] = eq
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
            reason="No directional lower-timeframe CSD was confirmed before the next HTF open.",
            fallback_to_unicorn=True,
        )
        result["equilibrium"] = eq
        return result

    raid_time = csd_event.get("raid_time")
    csd_confirmation_time = csd_event.get("confirmation_time")
    csd_protected = _protected_price(csd_event, direction)
    csd_inside_c2 = (
        raid_time is not None
        and csd_confirmation_time is not None
        and c2_time <= _dt(raid_time) < c3_time
        and c2_time <= _dt(csd_confirmation_time) < c3_time
        and csd_protected is not None
        and _price_created_inside_candle(
            c2_ltf,
            protected_price=csd_protected,
            direction=direction,
        )
    )
    if not csd_inside_c2:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_CSD_INSIDE_QUALIFYING_CANDLE",
            state_trace=states,
            reason=(
                "The latest directional LTF CSD was not fully formed inside the "
                "closed HTF candle."
            ),
            fallback_to_unicorn=True,
        )
        result["equilibrium"] = eq
        result["csd"] = csd_event
        return result

    states.append("CSD_CONFIRMED")

    selected, candidates = _select_protected_swing(
        c2_bars=c2_ltf,
        csd_event=csd_event,
        direction=direction,
        eq=eq,
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
                "Protected swings exist only if they remain intact, sit on the "
                "correct side of the next HTF open, and structurally cover C2 EQ."
            ),
            fallback_to_unicorn=True,
        )
        result.update(
            {
                "equilibrium": eq,
                "csd": csd_event,
                "protected_swing_candidates": candidates,
            }
        )
        return result

    states.extend(["PROTECTED_SWING_FOUND", "POSITIONAL_ARMED"])

    target = _htf_std_minus_two(
        htf[:-1],
        direction=direction,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
        entry_price=entry_price,
    )
    if target is None:
        result = _base_result(
            symbol=symbol,
            direction=direction,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            status="WAIT_HTF_STD_TARGET",
            state_trace=states,
            reason=(
                "The positional structure is armed, but no directionally valid "
                "HTF CSD range is available for the STD -2 target."
            ),
            fallback_to_unicorn=True,
        )
        result.update(
            {
                "equilibrium": eq,
                "csd": csd_event,
                "selected_protected_swing": selected,
                "protected_swing_candidates": candidates,
            }
        )
        return result

    target["timeframe"] = htf_timeframe
    stop_price = float(selected["protected_swing"])
    target_price = float(target["price"])
    rr = _risk_reward(
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )

    states.extend(["C3_OPEN", "POSITIONAL_ACTIVE"])
    post_open = [bar for bar in ltf if _dt(bar["time"]) >= c3_time]
    outcome = _position_outcome(
        post_open,
        direction=direction,
        stop_price=stop_price,
        target_price=target_price,
    )
    trail = _trail_candidate(
        post_open,
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
        "qualifying_htf_candle": {
            "time": c2["time"],
            "open": float(c2["open"]),
            "high": c2_high,
            "low": c2_low,
            "close": float(c2["close"]),
            "equilibrium": eq,
        },
        "next_htf_candle_open": {
            "time": c3["time"],
            "price": entry_price,
        },
        "csd": csd_event,
        "protected_swing_candidates": candidates,
        "selected_protected_swing": selected,
        "exact_order_price": entry_price,
        "exact_order_price_defined": True,
        "entry_rule": "NEXT_HTF_OPEN_AFTER_VALID_C2_PROTECTED_SWING",
        "stop_reference": stop_price,
        "stop_rule": (
            "Below selected protected swing"
            if direction == "bullish"
            else "Above selected protected swing"
        ),
        "target": target,
        "target_rule": "HTF_CSD_STD_-2",
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
            "Previous HTF candle closed with a directional LTF CSD and a valid "
            "protected swing relative to EQ; the positional signal is the next "
            "HTF candle open."
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
    )
