from __future__ import annotations

from datetime import datetime

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL
from tradingagents.dataflows.ctrader_csd import analyze_csd_orderflow


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

LTF_TIMEFRAMES = {
    "M1",
    "M3",
    "M5",
    "M15",
}


def _normalize(symbol: str) -> str:
    key = symbol.strip().upper()

    if key not in SYMBOLS:
        raise ValueError(
            f"Unsupported continuation symbol: {symbol}"
        )

    return SYMBOLS[key]


def _fetch(
    symbol: str,
    timeframe: str,
    count: int,
) -> list[dict]:

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

    return payload["bars"]


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _bullish(bar: dict) -> bool:
    return bar["close"] > bar["open"]


def _bearish(bar: dict) -> bool:
    return bar["close"] < bar["open"]


def _find_ranges(
    bars: list[dict],
    direction: str,
) -> list[dict]:

    # User order-flow rule:
    #
    # Bullish:
    # down-close range is traded through to upside
    # and accepted above with a candle BODY close.
    #
    # Bearish:
    # inverse.

    opposing = (
        _bearish
        if direction == "bullish"
        else _bullish
    )

    ranges = []

    i = 0

    while i < len(bars):

        if not opposing(bars[i]):
            i += 1
            continue

        start = i
        end = i

        while (
            end + 1 < len(bars)
            and opposing(bars[end + 1])
        ):
            end += 1

        group = bars[start:end + 1]

        range_high = max(
            bar["high"]
            for bar in group
        )

        range_low = min(
            bar["low"]
            for bar in group
        )

        confirmation_index = None

        for j in range(end + 1, len(bars)):

            if direction == "bullish":

                # BODY close above entire down-close range.
                if bars[j]["close"] > range_high:
                    confirmation_index = j
                    break

            else:

                # BODY close below entire up-close range.
                if bars[j]["close"] < range_low:
                    confirmation_index = j
                    break

        if confirmation_index is not None:

            confirmation_slice = bars[
                start:confirmation_index + 1
            ]

            if direction == "bullish":

                protected_swing = min(
                    bar["low"]
                    for bar in confirmation_slice
                )

                failed = any(
                    bar["close"] < protected_swing
                    for bar in bars[
                        confirmation_index + 1:
                    ]
                )

            else:

                protected_swing = max(
                    bar["high"]
                    for bar in confirmation_slice
                )

                failed = any(
                    bar["close"] > protected_swing
                    for bar in bars[
                        confirmation_index + 1:
                    ]
                )

            ranges.append(
                {
                    "direction": direction,

                    "source_start_time":
                        bars[start]["time"],

                    "source_end_time":
                        bars[end]["time"],

                    "source_candles":
                        end - start + 1,

                    "source_range_low":
                        range_low,

                    "source_range_high":
                        range_high,

                    "confirmation_time":
                        bars[
                            confirmation_index
                        ]["time"],

                    "confirmation_close":
                        bars[
                            confirmation_index
                        ]["close"],

                    "protected_swing":
                        protected_swing,

                    "failed":
                        failed,

                    "still_holding":
                        not failed,

                    "_confirmation_index":
                        confirmation_index,
                }
            )

        i = end + 1

    return ranges


def evaluate_ltf_continuation(
    symbol: str,
    ltf_timeframe: str = "M5",
    csd_timeframe: str = "M15",
    count: int = 500,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
) -> dict:

    symbol = _normalize(symbol)

    ltf_timeframe = (
        ltf_timeframe
        .strip()
        .upper()
    )

    csd_timeframe = (
        csd_timeframe
        .strip()
        .upper()
    )

    if ltf_timeframe not in LTF_TIMEFRAMES:
        raise ValueError(
            f"Unsupported LTF timeframe: "
            f"{ltf_timeframe}"
        )

    if count < 50 or count > 2000:
        raise ValueError(
            "count must be between 50 and 2000"
        )

    # ------------------------------------------
    # Step 1:
    # Deterministic higher-timeframe CSD / IOF.
    # ------------------------------------------

    csd = analyze_csd_orderflow(
        symbol=symbol,
        timeframe=csd_timeframe,
        count=count,
        pivot_window=pivot_window,
        confirmation_bars=confirmation_bars,
    )

    control = csd[
        "current_orderflow_control"
    ]

    if control not in (
        "bullish_control",
        "bearish_control",
    ):

        return {
            "symbol": symbol,
            "ltf_timeframe": ltf_timeframe,
            "csd_timeframe": csd_timeframe,

            "status": "WAIT",

            "ltf_gate_passed": False,

            "reason": (
                "Higher-timeframe deterministic "
                "post-CSD order-flow control is "
                "not confirmed."
            ),

            "higher_timeframe_control":
                control,

            "hierarchy": (
                "Liquidity -> SMT -> CSD -> "
                "IOFC -> LTF continuation"
            ),

            "full_setup_ready": False,
        }

    direction = (
        "bullish"
        if control == "bullish_control"
        else "bearish"
    )

    active_csd = csd.get(
        "active_csd"
    ) or {}

    post_csd_iof = csd.get(
        "post_csd_iof"
    ) or {}

    # Begin LTF evaluation only after the
    # higher-timeframe continuation control
    # was actually confirmed.

    control_time = (
        post_csd_iof.get(
            "confirmation_time"
        )
        or active_csd.get(
            "confirmation_time"
        )
    )

    if not control_time:

        return {
            "symbol": symbol,
            "status": "WAIT",
            "ltf_gate_passed": False,
            "reason": (
                "No usable higher-timeframe "
                "control confirmation timestamp."
            ),
            "full_setup_ready": False,
        }

    # ------------------------------------------
    # Step 2:
    # Get lower-timeframe cTrader candles.
    # ------------------------------------------

    bars = _fetch(
        symbol,
        ltf_timeframe,
        count,
    )

    control_dt = _dt(
        control_time
    )

    eligible = [
        bar
        for bar in bars
        if _dt(bar["time"]) >= control_dt
    ]

    if len(eligible) < 5:

        return {
            "symbol": symbol,
            "direction": direction,
            "status": "WAIT",
            "ltf_gate_passed": False,

            "reason": (
                "Not enough lower-timeframe "
                "candles after HTF control "
                "confirmation."
            ),

            "control_confirmation_time":
                control_time,

            "ltf_candles_after_control":
                len(eligible),

            "full_setup_ready": False,
        }

    # ------------------------------------------
    # Step 3:
    # Search for confirmed LTF order-flow ranges.
    # ------------------------------------------

    ranges = _find_ranges(
        eligible,
        direction,
    )

    if not ranges:

        return {
            "symbol": symbol,
            "direction": direction,

            "higher_timeframe_control":
                control,

            "status": "WAIT",

            "ltf_gate_passed": False,

            "reason": (
                "No lower-timeframe opposing-close "
                "range has been traded through and "
                "accepted with a body close."
            ),

            "control_confirmation_time":
                control_time,

            "current_price":
                eligible[-1]["close"],

            "full_setup_ready": False,

            "note": (
                "No continuation is inferred "
                "without body-close confirmation."
            ),
        }

    latest = max(
        ranges,
        key=lambda x:
            x["_confirmation_index"],
    )

    current_price = (
        eligible[-1]["close"]
    )

    # ------------------------------------------
    # Step 4:
    # Protected-swing integrity.
    # ------------------------------------------

    if direction == "bullish":

        protected_intact = (
            current_price
            >= latest["protected_swing"]
        )

    else:

        protected_intact = (
            current_price
            <= latest["protected_swing"]
        )

    # ------------------------------------------
    # Step 5:
    # Final LTF state.
    # ------------------------------------------

    if (
        latest["failed"]
        or not protected_intact
    ):

        status = "INVALIDATED"
        gate = False

        reason = (
            "The confirmed lower-timeframe "
            "continuation structure subsequently "
            "failed its protected swing."
        )

    elif latest["still_holding"]:

        status = "VALID_CONTINUATION"
        gate = True

        reason = (
            "Lower-timeframe opposing-close range "
            "was traded through with a body close "
            "and the protected swing remains intact."
        )

    else:

        status = "WAIT"
        gate = False

        reason = (
            "Lower-timeframe structure is not "
            "sufficiently confirmed."
        )

    clean_latest = {
        key: value
        for key, value in latest.items()
        if not key.startswith("_")
    }

    return {
        "symbol": symbol,

        "direction": direction,

        "csd_timeframe":
            csd_timeframe,

        "ltf_timeframe":
            ltf_timeframe,

        "higher_timeframe_control":
            control,

        "control_confirmation_time":
            control_time,

        "latest_ltf_orderflow":
            clean_latest,

        "current_price":
            current_price,

        "protected_swing_intact":
            protected_intact,

        "status":
            status,

        "ltf_gate_passed":
            gate,

        # IMPORTANT:
        # This does NOT bypass SMT or the rest
        # of the complete setup hierarchy.
        "full_setup_ready":
            False,

        "reason":
            reason,

        "rules": {
            "wick_only_break_is_confirmation":
                False,

            "body_close_required":
                True,

            "protected_swing_required":
                True,

            "smt_gate_not_evaluated_here":
                True,
        },

        "hierarchy": (
            "HTF context -> liquidity -> SMT -> "
            "CSD -> post-CSD IOF -> "
            "LTF continuation -> entry model"
        ),
    }
