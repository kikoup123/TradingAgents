"""Historical signal-validation layer for Trading Sand positional entries.

This module is deliberately a *model validation* tool, not a portfolio simulator.
It walks historical HTF opens, evaluates the exact positional engine with only
the information available before each entry open, then observes a bounded
post-open window to classify the signal outcome.

Automatic broker execution remains disabled.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from statistics import mean
from typing import Any

from tradingagents.dataflows.ctrader_positional import (
    FRACTAL_TIMEFRAME_PAIRS,
    _dt,
    _fetch,
    _normalize,
    _sort_bars,
    evaluate_positional_from_bars,
)


HTF_MINUTES = {
    "W1": 10080,
    "D1": 1440,
    "H4": 240,
    "H1": 60,
    "M30": 30,
    "M15": 15,
}


def _record_from_result(
    result: dict[str, Any],
    *,
    candidate_number: int,
) -> dict[str, Any]:
    confirmed = result.get("entry_model_confirmed") is True
    fallback = result.get("fallback_to_unicorn") is True
    route = (
        "POSITIONAL"
        if confirmed
        else "UNICORN_FALLBACK_REQUIRED"
        if fallback
        else "NO_ENTRY"
    )

    selected_swing = result.get("selected_protected_swing") or {}
    target = result.get("target") or {}
    outcome = result.get("position_outcome") or {}
    next_open = result.get("next_htf_candle_open") or {}
    qualifying = result.get("qualifying_htf_candle") or {}
    tspot = result.get("tspot") or qualifying.get("tspot") or {}

    return {
        "candidate_number": candidate_number,
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "fractal_stage": result.get("fractal_stage"),
        "entry_candle_label": result.get("entry_candle_label"),
        "entry_time": next_open.get("time"),
        "entry_price": result.get("exact_order_price"),
        "status": result.get("status"),
        "route": route,
        "fallback_to_unicorn": fallback,
        "reason": result.get("reason"),
        "qualifying_htf_time": qualifying.get("time"),
        "wick_equilibrium": qualifying.get(
            "directional_wick_equilibrium",
            result.get("equilibrium"),
        ),
        "tspot_top": tspot.get("top"),
        "tspot_bottom": tspot.get("bottom"),
        "protected_swing": selected_swing.get("protected_swing"),
        "protected_swing_source": selected_swing.get("source"),
        "protected_swing_eq_covers": selected_swing.get("eq_covers"),
        "protected_swing_tspot_covers": selected_swing.get("tspot_covers"),
        "stop_reference": result.get("stop_reference"),
        "target_price": target.get("price"),
        "target_source": target.get("source"),
        "target_zero": target.get("zero_reference"),
        "target_one": target.get("one_reference"),
        "risk_reward": result.get("risk_reward"),
        "outcome": outcome.get("status"),
        "outcome_time": outcome.get("time"),
        "trail_stop_candidate": result.get("trail_stop_candidate"),
        "execution_allowed": result.get("execution_allowed", False),
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    routes = Counter(record["route"] for record in records)
    directions = Counter(record["direction"] for record in records)
    stages = Counter(record["fractal_stage"] for record in records)
    statuses = Counter(record["status"] for record in records)
    outcomes = Counter(
        record["outcome"]
        for record in records
        if record["outcome"] is not None
    )

    confirmed = [
        record
        for record in records
        if record["route"] == "POSITIONAL"
    ]
    rr_values = [
        float(record["risk_reward"])
        for record in confirmed
        if record["risk_reward"] is not None
    ]

    completed = outcomes.get("TARGET_HIT", 0) + outcomes.get("STOPPED", 0)
    target_hit_rate = (
        outcomes.get("TARGET_HIT", 0) / completed
        if completed > 0
        else None
    )

    return {
        "fractal_candidates": len(records),
        "positional_signals": routes.get("POSITIONAL", 0),
        "unicorn_fallback_required": routes.get(
            "UNICORN_FALLBACK_REQUIRED",
            0,
        ),
        "no_entry": routes.get("NO_ENTRY", 0),
        "directions": dict(directions),
        "fractal_stages": dict(stages),
        "statuses": dict(statuses),
        "observed_outcomes": dict(outcomes),
        "completed_positional_outcomes": completed,
        "target_hit_rate_on_completed_signals": target_hit_rate,
        "average_planned_rr": mean(rr_values) if rr_values else None,
        "execution_enabled": False,
        "interpretation": (
            "Signal-validation statistics only. Overlapping historical signals "
            "are evaluated independently and are not a portfolio-performance test."
        ),
    }


def scan_positional_history_from_bars(
    htf_bars: list[dict],
    ltf_bars: list[dict],
    *,
    htf_timeframe: str,
    symbol: str | None = None,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
    min_tick: float = 1e-12,
    outcome_horizon_htf_bars: int = 1,
) -> dict[str, Any]:
    """Scan historical HTF opens without using future data for entry qualification."""

    htf_timeframe = htf_timeframe.strip().upper()
    ltf_timeframe = FRACTAL_TIMEFRAME_PAIRS.get(htf_timeframe)
    if ltf_timeframe is None:
        raise ValueError(f"Unsupported positional HTF: {htf_timeframe}")
    if outcome_horizon_htf_bars < 1 or outcome_horizon_htf_bars > 20:
        raise ValueError("outcome_horizon_htf_bars must be between 1 and 20")

    htf = _sort_bars(htf_bars)
    ltf = _sort_bars(ltf_bars)
    if len(htf) < 3:
        return {
            "symbol": symbol,
            "htf_timeframe": htf_timeframe,
            "ltf_timeframe": ltf_timeframe,
            "coverage": None,
            "summary": _summarize([]),
            "records": [],
            "execution_allowed": False,
        }

    horizon = timedelta(
        minutes=HTF_MINUTES[htf_timeframe] * outcome_horizon_htf_bars
    )
    records: list[dict[str, Any]] = []
    candidate_number = 0

    for entry_index in range(2, len(htf)):
        entry_time = _dt(htf[entry_index]["time"])
        outcome_cutoff = entry_time + horizon

        # The HTF prefix ends at the candle whose open is being evaluated.
        # The positional engine never uses that candle's completed OHLC values
        # to qualify the entry. Its high/low/close are only used after the fact
        # for follow-through reporting.
        htf_prefix = htf[: entry_index + 1]
        ltf_window = [
            bar
            for bar in ltf
            if _dt(bar["time"]) < outcome_cutoff
        ]

        for direction in ("bearish", "bullish"):
            result = evaluate_positional_from_bars(
                htf_prefix,
                ltf_window,
                direction=direction,
                htf_timeframe=htf_timeframe,
                ltf_timeframe=ltf_timeframe,
                pivot_window=pivot_window,
                confirmation_bars=confirmation_bars,
                symbol=symbol,
                min_tick=min_tick,
            )

            if result.get("status") == "WAIT_HTF_FRACTAL":
                continue

            candidate_number += 1
            records.append(
                _record_from_result(
                    result,
                    candidate_number=candidate_number,
                )
            )

    coverage = {
        "htf_first": htf[0]["time"],
        "htf_last": htf[-1]["time"],
        "htf_bars": len(htf),
        "ltf_first": ltf[0]["time"] if ltf else None,
        "ltf_last": ltf[-1]["time"] if ltf else None,
        "ltf_bars": len(ltf),
        "outcome_horizon_htf_bars": outcome_horizon_htf_bars,
    }

    return {
        "symbol": symbol,
        "htf_timeframe": htf_timeframe,
        "ltf_timeframe": ltf_timeframe,
        "coverage": coverage,
        "summary": _summarize(records),
        "records": records,
        "execution_allowed": False,
    }


def validate_positional_history(
    *,
    symbol: str = "NASDAQ",
    htf_timeframe: str = "H1",
    htf_count: int = 160,
    ltf_count: int = 2000,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
    min_tick: float = 1e-12,
    outcome_horizon_htf_bars: int = 1,
) -> dict[str, Any]:
    """Fetch recent cTrader bars and run signal-only historical validation."""

    symbol = _normalize(symbol)
    htf_timeframe = htf_timeframe.strip().upper()
    ltf_timeframe = FRACTAL_TIMEFRAME_PAIRS.get(htf_timeframe)
    if ltf_timeframe is None:
        raise ValueError(f"Unsupported positional HTF: {htf_timeframe}")
    if htf_count < 3 or htf_count > 2000:
        raise ValueError("htf_count must be between 3 and 2000")
    if ltf_count < 10 or ltf_count > 2000:
        raise ValueError("ltf_count must be between 10 and 2000")

    htf_bars = _fetch(symbol, htf_timeframe, htf_count)
    ltf_bars = _fetch(symbol, ltf_timeframe, ltf_count)

    return scan_positional_history_from_bars(
        htf_bars,
        ltf_bars,
        htf_timeframe=htf_timeframe,
        symbol=symbol,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
        min_tick=min_tick,
        outcome_horizon_htf_bars=outcome_horizon_htf_bars,
    )
