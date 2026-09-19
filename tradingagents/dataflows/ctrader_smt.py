from __future__ import annotations

from datetime import datetime

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL


SUPPORTED = {
    "NASDAQ": "NASDAQ",
    "NQ": "NASDAQ",
    "US100": "NASDAQ",
    "USTECH100": "NASDAQ",
    "US500": "US500",
    "ES": "US500",
    "SP500": "US500",
}

TIMEFRAME_MINUTES = {
    "M1": 1,
    "M3": 3,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
}


def _normalize(symbol: str) -> str:
    key = symbol.strip().upper()

    if key not in SUPPORTED:
        raise ValueError(f"Unsupported SMT symbol: {symbol}")

    return SUPPORTED[key]


def _fetch(symbol: str, timeframe: str, count: int) -> list[dict]:
    response = requests.get(
        f"{BRIDGE_URL}/bars",
        params={
            "symbol": _normalize(symbol),
            "timeframe": timeframe,
            "count": count,
        },
        timeout=50,
    )

    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise RuntimeError(payload["error"])

    return payload["bars"]


def _aligned(a: list[dict], b: list[dict]):
    a_map = {x["time"]: x for x in a}
    b_map = {x["time"]: x for x in b}

    common = sorted(set(a_map) & set(b_map))

    return [
        {
            "time": t,
            "a": a_map[t],
            "b": b_map[t],
        }
        for t in common
    ]


def _pivot_indices(rows, side: str, window: int):
    field = "high" if side == "high" else "low"
    pivots = []

    for i in range(window, len(rows) - window):
        current = rows[i]["a"][field]

        left = [
            rows[j]["a"][field]
            for j in range(i - window, i)
        ]

        right = [
            rows[j]["a"][field]
            for j in range(i + 1, i + window + 1)
        ]

        if side == "high":
            if current > max(left) and current >= max(right):
                pivots.append(i)
        else:
            if current < min(left) and current <= min(right):
                pivots.append(i)

    return pivots


def _pivot_indices_b(rows, side: str, window: int):
    field = "high" if side == "high" else "low"
    pivots = []

    for i in range(window, len(rows) - window):
        current = rows[i]["b"][field]

        left = [
            rows[j]["b"][field]
            for j in range(i - window, i)
        ]

        right = [
            rows[j]["b"][field]
            for j in range(i + 1, i + window + 1)
        ]

        if side == "high":
            if current > max(left) and current >= max(right):
                pivots.append(i)
        else:
            if current < min(left) and current <= min(right):
                pivots.append(i)

    return pivots


def _match_pivots(
    rows,
    pivots_a,
    pivots_b,
    max_gap_bars: int = 2,
):
    pairs = []
    used_b = set()

    for ia in pivots_a:
        candidates = [
            ib
            for ib in pivots_b
            if ib not in used_b
            and abs(ia - ib) <= max_gap_bars
        ]

        if not candidates:
            continue

        ib = min(
            candidates,
            key=lambda x: abs(ia - x),
        )

        used_b.add(ib)

        pairs.append((ia, ib))

    pairs.sort(
        key=lambda pair: max(pair[0], pair[1])
    )

    return pairs


def _analyze_highs(rows, pairs):
    if len(pairs) < 2:
        return {
            "detected": False,
            "reason": "Not enough matched swing highs",
        }

    previous = pairs[-2]
    current = pairs[-1]

    pa, pb = previous
    ca, cb = current

    previous_a = rows[pa]["a"]["high"]
    current_a = rows[ca]["a"]["high"]

    previous_b = rows[pb]["b"]["high"]
    current_b = rows[cb]["b"]["high"]

    a_hh = current_a > previous_a
    b_hh = current_b > previous_b

    detected = a_hh != b_hh

    leader = None

    if detected:
        leader = "NASDAQ" if a_hh else "US500"

    return {
        "detected": detected,
        "type": "bearish_smt" if detected else "none",
        "leader": leader,
        "NASDAQ": {
            "previous_high": previous_a,
            "current_high": current_a,
            "made_higher_high": a_hh,
            "time": rows[ca]["time"],
        },
        "US500": {
            "previous_high": previous_b,
            "current_high": current_b,
            "made_higher_high": b_hh,
            "time": rows[cb]["time"],
        },
    }


def _analyze_lows(rows, pairs):
    if len(pairs) < 2:
        return {
            "detected": False,
            "reason": "Not enough matched swing lows",
        }

    previous = pairs[-2]
    current = pairs[-1]

    pa, pb = previous
    ca, cb = current

    previous_a = rows[pa]["a"]["low"]
    current_a = rows[ca]["a"]["low"]

    previous_b = rows[pb]["b"]["low"]
    current_b = rows[cb]["b"]["low"]

    a_ll = current_a < previous_a
    b_ll = current_b < previous_b

    detected = a_ll != b_ll

    leader = None

    if detected:
        leader = "NASDAQ" if a_ll else "US500"

    return {
        "detected": detected,
        "type": "bullish_smt" if detected else "none",
        "leader": leader,
        "NASDAQ": {
            "previous_low": previous_a,
            "current_low": current_a,
            "made_lower_low": a_ll,
            "time": rows[ca]["time"],
        },
        "US500": {
            "previous_low": previous_b,
            "current_low": current_b,
            "made_lower_low": b_ll,
            "time": rows[cb]["time"],
        },
    }


def detect_smt(
    timeframe: str = "M15",
    count: int = 300,
    pivot_window: int = 2,
) -> dict:
    timeframe = timeframe.strip().upper()

    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"Unsupported SMT timeframe: {timeframe}"
        )

    if count < 50 or count > 2000:
        raise ValueError(
            "SMT count must be between 50 and 2000"
        )

    nasdaq = _fetch(
        "NASDAQ",
        timeframe,
        count,
    )

    us500 = _fetch(
        "US500",
        timeframe,
        count,
    )

    rows = _aligned(
        nasdaq,
        us500,
    )

    if len(rows) < 20:
        raise RuntimeError(
            "Not enough synchronized NASDAQ/US500 candles"
        )

    highs_a = _pivot_indices(
        rows,
        "high",
        pivot_window,
    )

    highs_b = _pivot_indices_b(
        rows,
        "high",
        pivot_window,
    )

    lows_a = _pivot_indices(
        rows,
        "low",
        pivot_window,
    )

    lows_b = _pivot_indices_b(
        rows,
        "low",
        pivot_window,
    )

    matched_highs = _match_pivots(
        rows,
        highs_a,
        highs_b,
    )

    matched_lows = _match_pivots(
        rows,
        lows_a,
        lows_b,
    )

    bearish = _analyze_highs(
        rows,
        matched_highs,
    )

    bullish = _analyze_lows(
        rows,
        matched_lows,
    )

    return {
        "pair": "NASDAQ-US500",
        "timeframe": timeframe,
        "candles_aligned": len(rows),
        "pivot_window": pivot_window,
        "bearish_smt": bearish,
        "bullish_smt": bullish,
        "rule": (
            "SMT is contextual evidence only. "
            "SMT alone is NOT an entry confirmation."
        ),
        "required_confirmation": (
            "Require HTF CSD/order-flow shift and LTF "
            "continuation/entry confirmation."
        ),
    }
