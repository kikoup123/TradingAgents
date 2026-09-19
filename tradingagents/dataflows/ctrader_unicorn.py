from __future__ import annotations

from datetime import datetime

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL


SYMBOLS = {
    "NASDAQ": "NASDAQ",
    "NQ": "NASDAQ",
    "US100": "NASDAQ",
    "USTECH100": "NASDAQ",
    "US TECH 100": "NASDAQ",
    "US500": "US500",
    "ES": "US500",
    "SP500": "US500",
    "SPX500": "US500",
    "XAUUSD": "XAUUSD",
    "GOLD": "XAUUSD",
}

ENTRY_TIMEFRAMES = ("M5", "M3", "M1")


def _normalize(symbol: str) -> str:
    key = symbol.strip().upper()

    if key not in SYMBOLS:
        raise ValueError(
            f"Unsupported Unicorn symbol: {symbol}"
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

    bars = payload["bars"]

    # Every deterministic price-structure algorithm
    # must operate strictly oldest -> newest.
    bars = sorted(
        bars,
        key=lambda bar: datetime.fromisoformat(
            bar["time"]
        ),
    )

    return bars


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _bullish(bar: dict) -> bool:
    return bar["close"] > bar["open"]


def _bearish(bar: dict) -> bool:
    return bar["close"] < bar["open"]


def _touch(
    bar: dict,
    low: float,
    high: float,
) -> bool:
    return (
        bar["high"] >= low
        and bar["low"] <= high
    )


def _overlap(
    low_a: float,
    high_a: float,
    low_b: float,
    high_b: float,
):
    low = max(low_a, low_b)
    high = min(high_a, high_b)

    if low >= high:
        return None

    return low, high



# ============================================================
# SWINGS + ACTUAL BREAKER BLOCKS
# ============================================================

def _pivot_low(
    bars: list[dict],
    index: int,
    window: int,
) -> bool:

    if (
        index < window
        or index + window >= len(bars)
    ):
        return False

    value = bars[index]["low"]

    return all(
        value < bars[j]["low"]
        for j in range(
            index - window,
            index + window + 1,
        )
        if j != index
    )


def _pivot_high(
    bars: list[dict],
    index: int,
    window: int,
) -> bool:

    if (
        index < window
        or index + window >= len(bars)
    ):
        return False

    value = bars[index]["high"]

    return all(
        value > bars[j]["high"]
        for j in range(
            index - window,
            index + window + 1,
        )
        if j != index
    )


def _latest_prior_pivot(
    bars: list[dict],
    before: int,
    direction: str,
    window: int,
):

    predicate = (
        _pivot_low
        if direction == "bullish"
        else _pivot_high
    )

    for i in range(
        before - 1,
        window - 1,
        -1,
    ):
        if predicate(
            bars,
            i,
            window,
        ):
            return i

    return None


def _find_breakers(
    bars: list[dict],
    timeframe: str,
    direction: str,
    pivot_window: int,
    confirmation_bars: int,
) -> list[dict]:
    """
    Trading Sand / ICT Breaker definition used here:

    Bullish:
    - opposing down-close candle or contiguous down-close range
    - raids a prior swing low
    - later BODY close above the complete opposing range
    - breaker remains valid while there is no later body close below it

    Bearish:
    exact inverse.
    """

    opposing = (
        _bearish
        if direction == "bullish"
        else _bullish
    )

    candidates = []

    i = pivot_window + 1

    while i < len(bars) - 2:

        if not opposing(
            bars[i]
        ):
            i += 1
            continue

        start = i
        end = i

        while (
            end + 1 < len(bars)
            and opposing(
                bars[end + 1]
            )
        ):
            end += 1

        prior_pivot = (
            _latest_prior_pivot(
                bars,
                start,
                direction,
                pivot_window,
            )
        )

        if prior_pivot is None:
            i = end + 1
            continue

        group = bars[
            start:end + 1
        ]

        range_low = min(
            bar["low"]
            for bar in group
        )

        range_high = max(
            bar["high"]
            for bar in group
        )

        if direction == "bullish":

            reference_level = (
                bars[
                    prior_pivot
                ]["low"]
            )

            liquidity_raid = (
                range_low
                < reference_level
            )

        else:

            reference_level = (
                bars[
                    prior_pivot
                ]["high"]
            )

            liquidity_raid = (
                range_high
                > reference_level
            )

        if not liquidity_raid:
            i = end + 1
            continue

        confirm_index = None

        final_index = min(
            len(bars) - 1,
            end + confirmation_bars,
        )

        for j in range(
            end + 1,
            final_index + 1,
        ):

            if (
                direction == "bullish"
                and bars[j]["close"]
                > range_high
            ):
                confirm_index = j
                break

            if (
                direction == "bearish"
                and bars[j]["close"]
                < range_low
            ):
                confirm_index = j
                break

        if confirm_index is None:
            i = end + 1
            continue

        if direction == "bullish":

            failed = any(
                bar["close"]
                < range_low
                for bar
                in bars[
                    confirm_index + 1:
                ]
            )

        else:

            failed = any(
                bar["close"]
                > range_high
                for bar
                in bars[
                    confirm_index + 1:
                ]
            )

        candidates.append(
            {
                "direction":
                    direction,

                "timeframe":
                    timeframe,

                "source_start_time":
                    bars[start]["time"],

                "source_end_time":
                    bars[end]["time"],

                "source_range_low":
                    range_low,

                "source_range_high":
                    range_high,

                "reference_swing_time":
                    bars[
                        prior_pivot
                    ]["time"],

                "reference_swing_level":
                    reference_level,

                "liquidity_raid":
                    True,

                "confirmation_time":
                    bars[
                        confirm_index
                    ]["time"],

                "confirmation_close":
                    bars[
                        confirm_index
                    ]["close"],

                "failed":
                    failed,

                "still_holding":
                    not failed,

                "_start_index":
                    start,

                "_end_index":
                    end,

                "_confirm_index":
                    confirm_index,
            }
        )

        i = end + 1

    return candidates



# ============================================================
# FAIR VALUE GAPS
# ============================================================

def _find_fvgs(
    bars: list[dict],
) -> list[dict]:

    fvgs = []

    for i in range(2, len(bars)):

        first = bars[i - 2]
        third = bars[i]

        # Bullish FVG / BISI
        if third["low"] > first["high"]:
            fvgs.append(
                {
                    "direction": "bullish",
                    "low": first["high"],
                    "high": third["low"],
                    "formed_time": third["time"],
                    "_index": i,
                }
            )

        # Bearish FVG / SIBI
        if third["high"] < first["low"]:
            fvgs.append(
                {
                    "direction": "bearish",
                    "low": third["high"],
                    "high": first["low"],
                    "formed_time": third["time"],
                    "_index": i,
                }
            )

    return fvgs


# ============================================================
# UNICORN = BREAKER + FVG OVERLAP
# ============================================================


def _find_unicorn_on_timeframe(
    bars: list[dict],
    timeframe: str,
    direction: str,
    pivot_window: int,
    confirmation_bars: int,
):

    breakers = _find_breakers(
        bars=bars,
        timeframe=timeframe,
        direction=direction,
        pivot_window=pivot_window,
        confirmation_bars=
            confirmation_bars,
    )

    # A failed breaker cannot form
    # the active Unicorn.
    breakers = [
        breaker
        for breaker in breakers
        if breaker["still_holding"]
    ]

    fvgs = _find_fvgs(
        bars
    )

    candidates = []

    for breaker in breakers:

        for fvg in fvgs:

            # Unicorn uses FVG in the
            # execution direction.
            if (
                fvg["direction"]
                != direction
            ):
                continue

            if (
                fvg["_index"]
                < breaker[
                    "_start_index"
                ]
            ):
                continue

            zone = _overlap(
                breaker[
                    "source_range_low"
                ],
                breaker[
                    "source_range_high"
                ],
                fvg["low"],
                fvg["high"],
            )

            if zone is None:
                continue

            zone_low, zone_high = (
                zone
            )

            retest_index = None

            search_from = max(
                fvg["_index"] + 1,
                breaker[
                    "_confirm_index"
                ] + 1,
            )

            for j in range(
                search_from,
                len(bars),
            ):

                if _touch(
                    bars[j],
                    zone_low,
                    zone_high,
                ):
                    retest_index = j
                    break

            candidates.append(
                {
                    "direction":
                        direction,

                    "timeframe":
                        timeframe,

                    "breaker_low":
                        breaker[
                            "source_range_low"
                        ],

                    "breaker_high":
                        breaker[
                            "source_range_high"
                        ],

                    "breaker_source_start":
                        breaker[
                            "source_start_time"
                        ],

                    "breaker_source_end":
                        breaker[
                            "source_end_time"
                        ],

                    "breaker_confirmation_time":
                        breaker[
                            "confirmation_time"
                        ],

                    "breaker_reference_swing_time":
                        breaker[
                            "reference_swing_time"
                        ],

                    "breaker_reference_swing_level":
                        breaker[
                            "reference_swing_level"
                        ],

                    "fvg_low":
                        fvg["low"],

                    "fvg_high":
                        fvg["high"],

                    "fvg_formed_time":
                        fvg[
                            "formed_time"
                        ],

                    "unicorn_low":
                        zone_low,

                    "unicorn_high":
                        zone_high,

                    "retested":
                        retest_index
                        is not None,

                    "retest_time": (
                        bars[
                            retest_index
                        ]["time"]
                        if retest_index
                        is not None
                        else None
                    ),

                    "_breaker":
                        breaker,

                    "_fvg_index":
                        fvg["_index"],

                    "_retest_index":
                        retest_index,

                    "_rank_index":
                        max(
                            breaker[
                                "_confirm_index"
                            ],
                            fvg["_index"],
                        ),
                }
            )

    if not candidates:
        return None

    # Latest valid Unicorn on this TF.
    return max(
        candidates,
        key=lambda item: (
            item[
                "_rank_index"
            ],
            item["retested"],
        ),
    )


def _find_unicorn(
    symbol: str,
    direction: str,
    count: int,
    pivot_window: int,
    confirmation_bars: int,
):

    candidates = []

    for timeframe in ENTRY_TIMEFRAMES:

        bars = _fetch(
            symbol,
            timeframe,
            count,
        )

        candidate = (
            _find_unicorn_on_timeframe(
                bars=bars,
                timeframe=timeframe,
                direction=direction,
                pivot_window=
                    pivot_window,
                confirmation_bars=
                    confirmation_bars,
            )
        )

        if candidate is not None:
            candidates.append(
                candidate
            )

    if not candidates:
        return None

    # A retested structure takes priority,
    # then choose the most recent one
    # across M5 / M3 / M1.
    return max(
        candidates,
        key=lambda item: (
            item["retested"],
            _dt(
                item[
                    "fvg_formed_time"
                ]
            ),
        ),
    )



# ============================================================
# SECOND CSD AFTER IFVG RETEST
# ============================================================

def _find_second_csd(
    bars: list[dict],
    start_index: int,
    direction: str,
    confirmation_bars: int,
):

    opposing = (
        _bearish
        if direction == "bullish"
        else _bullish
    )

    source_start = None

    for i in range(
        start_index,
        len(bars),
    ):
        if opposing(bars[i]):
            source_start = i
            break

    if source_start is None:
        return None

    source_end = source_start

    while (
        source_end + 1 < len(bars)
        and opposing(
            bars[source_end + 1]
        )
    ):
        source_end += 1

    stretch = bars[
        source_start:source_end + 1
    ]

    if direction == "bullish":
        threshold = max(
            bar["open"]
            for bar in stretch
        )
    else:
        threshold = min(
            bar["open"]
            for bar in stretch
        )

    confirm_index = None

    final_index = min(
        len(bars) - 1,
        source_end + confirmation_bars,
    )

    for i in range(
        source_end + 1,
        final_index + 1,
    ):

        if direction == "bullish":

            # BODY close required.
            if bars[i]["close"] > threshold:
                confirm_index = i
                break

        else:

            if bars[i]["close"] < threshold:
                confirm_index = i
                break

    if confirm_index is None:
        return {
            "confirmed": False,
            "direction": direction,
            "threshold": threshold,
            "source_start_time":
                bars[source_start]["time"],
            "source_end_time":
                bars[source_end]["time"],
            "_confirm_index": None,
        }

    if direction == "bullish":
        protected_swing = min(
            bar["low"]
            for bar in bars[
                source_start:
                confirm_index + 1
            ]
        )
    else:
        protected_swing = max(
            bar["high"]
            for bar in bars[
                source_start:
                confirm_index + 1
            ]
        )

    return {
        "confirmed": True,
        "direction": direction,

        "source_start_time":
            bars[source_start]["time"],

        "source_end_time":
            bars[source_end]["time"],

        "threshold":
            threshold,

        "confirmation_time":
            bars[confirm_index]["time"],

        "confirmation_close":
            bars[confirm_index]["close"],

        "protected_swing":
            protected_swing,

        "_confirm_index":
            confirm_index,
    }


# ============================================================
# NEW IOF RANGE AFTER SECOND CSD
# ============================================================

def _find_new_iof(
    bars: list[dict],
    start_index: int,
    direction: str,
    confirmation_bars: int = 20,
):

    opposing = (
        _bearish
        if direction == "bullish"
        else _bullish
    )

    for source_start in range(
        start_index,
        len(bars) - 1,
    ):

        if not opposing(
            bars[source_start]
        ):
            continue

        source_end = source_start

        while (
            source_end + 1 < len(bars)
            and opposing(
                bars[source_end + 1]
            )
        ):
            source_end += 1

        group = bars[
            source_start:
            source_end + 1
        ]

        range_low = min(
            bar["low"]
            for bar in group
        )

        range_high = max(
            bar["high"]
            for bar in group
        )

        confirm_index = None

        final_index = min(
            len(bars) - 1,
            source_end
            + confirmation_bars,
        )

        for i in range(
            source_end + 1,
            final_index + 1,
        ):

            if direction == "bullish":

                if (
                    bars[i]["close"]
                    > range_high
                ):
                    confirm_index = i
                    break

            else:

                if (
                    bars[i]["close"]
                    < range_low
                ):
                    confirm_index = i
                    break

        if confirm_index is None:
            continue

        if direction == "bullish":

            failed = any(
                bar["close"] < range_low
                for bar in bars[
                    confirm_index + 1:
                ]
            )

        else:

            failed = any(
                bar["close"] > range_high
                for bar in bars[
                    confirm_index + 1:
                ]
            )

        return {
            "confirmed": True,
            "direction": direction,

            "source_start_time":
                bars[source_start]["time"],

            "source_end_time":
                bars[source_end]["time"],

            "source_range_low":
                range_low,

            "source_range_high":
                range_high,

            "confirmation_time":
                bars[confirm_index]["time"],

            "confirmation_close":
                bars[confirm_index]["close"],

            "failed":
                failed,

            "still_holding":
                not failed,

            "_confirm_index":
                confirm_index,
        }

    return {
        "confirmed": False,
        "direction": direction,
        "reason": (
            "No new post-second-CSD IOF range "
            "has been body-closed through."
        ),
    }


# ============================================================
# HOUSING CANDLE / NEGATED FVG / IFVG
# ============================================================


def _evaluate_housing_candidate(
    bars: list[dict],
    timeframe: str,
    direction: str,
    unicorn: dict,
    opposing_fvg: dict,
    confirmation_bars: int,
):

    fvg_low = (
        opposing_fvg["low"]
    )

    fvg_high = (
        opposing_fvg["high"]
    )

    # The nested opposing FVG must
    # actually exist inside / overlap
    # the Unicorn structure.
    internal_zone = _overlap(
        unicorn[
            "unicorn_low"
        ],
        unicorn[
            "unicorn_high"
        ],
        fvg_low,
        fvg_high,
    )

    if internal_zone is None:
        return None

    # --------------------------------------------------
    # EXPLICIT FVG NEGATION
    #
    # Bullish:
    # bearish FVG must receive BODY close
    # above its complete upper boundary.
    #
    # Bearish:
    # exact inverse.
    # --------------------------------------------------

    negation_index = None

    for i in range(
        opposing_fvg[
            "_index"
        ] + 1,
        len(bars),
    ):

        if (
            direction == "bullish"
            and bars[i]["close"]
            > fvg_high
        ):
            negation_index = i
            break

        if (
            direction == "bearish"
            and bars[i]["close"]
            < fvg_low
        ):
            negation_index = i
            break

    if negation_index is None:

        return {
            "stage_rank":
                1,

            "status":
                "WAIT_FVG_NEGATION",

            "timeframe":
                timeframe,

            "opposing_fvg_direction":
                opposing_fvg[
                    "direction"
                ],

            "opposing_fvg_low":
                fvg_low,

            "opposing_fvg_high":
                fvg_high,
        }

    # --------------------------------------------------
    # HOUSING CANDLE
    #
    # Its full wick range must contain
    # the now-negated FVG.
    #
    # The negation candle itself is
    # allowed to be the Housing Candle.
    # --------------------------------------------------

    # The Housing Candle is the middle candle
    # of the three-candle FVG structure.
    #
    # opposing_fvg["_index"] is candle 3,
    # therefore candle 2 is index - 1.
    housing_index = (
        opposing_fvg["_index"] - 1
    )

    if housing_index < 0:
        housing_index = None

    elif not (
        bars[housing_index]["low"]
        <= fvg_low
        and bars[housing_index]["high"]
        >= fvg_high
    ):
        housing_index = None

    if housing_index is None:

        return {
            "stage_rank":
                2,

            "status":
                "WAIT_HOUSING_CANDLE",

            "timeframe":
                timeframe,

            "fvg_negated":
                True,

            "fvg_negation_time":
                bars[
                    negation_index
                ]["time"],

            "opposing_fvg_low":
                fvg_low,

            "opposing_fvg_high":
                fvg_high,
        }

    housing = (
        bars[
            housing_index
        ]
    )

    # User-defined entry location:
    # Housing Candle range at the IFVG.
    #
    # Restrict it to the actual nested
    # FVG / Unicorn overlap.
    entry_zone = _overlap(
        unicorn[
            "unicorn_low"
        ],
        unicorn[
            "unicorn_high"
        ],
        max(
            housing["low"],
            fvg_low,
        ),
        min(
            housing["high"],
            fvg_high,
        ),
    )

    if entry_zone is None:

        return {
            "stage_rank":
                2,

            "status":
                "WAIT_VALID_ENTRY_ZONE",

            "timeframe":
                timeframe,

            "housing_time":
                housing["time"],
        }

    entry_low, entry_high = (
        entry_zone
    )

    # --------------------------------------------------
    # IFVG CONFIRMATION
    #
    # A LATER candle must BODY CLOSE
    # through the Housing Candle.
    # --------------------------------------------------

    ifvg_confirm_index = None

    final_index = min(
        len(bars) - 1,
        housing_index
        + confirmation_bars,
    )

    for i in range(
        max(
            housing_index + 1,
            negation_index,
        ),
        final_index + 1,
    ):

        if (
            direction == "bullish"
            and bars[i]["close"]
            > housing["high"]
        ):
            ifvg_confirm_index = i
            break

        if (
            direction == "bearish"
            and bars[i]["close"]
            < housing["low"]
        ):
            ifvg_confirm_index = i
            break

    if ifvg_confirm_index is None:

        return {
            "stage_rank":
                3,

            "status":
                "WAIT_IFVG_BODY_CLOSE",

            "timeframe":
                timeframe,

            "fvg_negated":
                True,

            "fvg_negation_time":
                bars[
                    negation_index
                ]["time"],

            "housing_time":
                housing["time"],

            "housing_low":
                housing["low"],

            "housing_high":
                housing["high"],

            "entry_zone_low":
                entry_low,

            "entry_zone_high":
                entry_high,
        }

    # --------------------------------------------------
    # RETEST OF IFVG / HOUSING RANGE
    # --------------------------------------------------

    retest_index = None

    for i in range(
        ifvg_confirm_index + 1,
        len(bars),
    ):

        if _touch(
            bars[i],
            entry_low,
            entry_high,
        ):
            retest_index = i
            break

    if retest_index is None:

        return {
            "stage_rank":
                4,

            "status":
                "WAIT_IFVG_RETEST",

            "timeframe":
                timeframe,

            "fvg_negated":
                True,

            "fvg_negation_time":
                bars[
                    negation_index
                ]["time"],

            "housing_time":
                housing["time"],

            "housing_low":
                housing["low"],

            "housing_high":
                housing["high"],

            "ifvg_low":
                fvg_low,

            "ifvg_high":
                fvg_high,

            "ifvg_confirmation_time":
                bars[
                    ifvg_confirm_index
                ]["time"],

            "entry_zone_low":
                entry_low,

            "entry_zone_high":
                entry_high,
        }

    # --------------------------------------------------
    # BREAKER MUST STILL HOLD
    # --------------------------------------------------

    if direction == "bullish":

        breaker_invalidated = any(
            bar["close"]
            < unicorn[
                "breaker_low"
            ]
            for bar
            in bars[
                retest_index:
            ]
        )

    else:

        breaker_invalidated = any(
            bar["close"]
            > unicorn[
                "breaker_high"
            ]
            for bar
            in bars[
                retest_index:
            ]
        )

    if breaker_invalidated:

        return {
            "stage_rank":
                5,

            "status":
                "INVALIDATED",

            "reason": (
                "Price body-closed beyond "
                "the Breaker Block after "
                "the IFVG retest."
            ),

            "timeframe":
                timeframe,

            "entry_zone_low":
                entry_low,

            "entry_zone_high":
                entry_high,
        }

    # --------------------------------------------------
    # SECOND CSD
    # --------------------------------------------------

    second_csd = _find_second_csd(
        bars,
        retest_index,
        direction,
        confirmation_bars,
    )

    if (
        second_csd is None
        or not second_csd.get(
            "confirmed"
        )
    ):

        return {
            "stage_rank":
                5,

            "status":
                "WAIT_SECOND_CSD",

            "timeframe":
                timeframe,

            "fvg_negated":
                True,

            "fvg_negation_time":
                bars[
                    negation_index
                ]["time"],

            "housing_time":
                housing["time"],

            "housing_low":
                housing["low"],

            "housing_high":
                housing["high"],

            "ifvg_low":
                fvg_low,

            "ifvg_high":
                fvg_high,

            "ifvg_confirmation_time":
                bars[
                    ifvg_confirm_index
                ]["time"],

            "retest_time":
                bars[
                    retest_index
                ]["time"],

            "entry_zone_low":
                entry_low,

            "entry_zone_high":
                entry_high,

            "second_csd":
                second_csd,
        }

    # --------------------------------------------------
    # NEW IOF AFTER SECOND CSD
    # --------------------------------------------------

    new_iof = _find_new_iof(
        bars,
        second_csd[
            "_confirm_index"
        ] + 1,
        direction,
    )

    if not new_iof.get(
        "confirmed"
    ):

        return {
            "stage_rank":
                6,

            "status":
                "WAIT_NEW_IOF",

            "timeframe":
                timeframe,

            "fvg_negated":
                True,

            "fvg_negation_time":
                bars[
                    negation_index
                ]["time"],

            "housing_time":
                housing["time"],

            "housing_low":
                housing["low"],

            "housing_high":
                housing["high"],

            "ifvg_low":
                fvg_low,

            "ifvg_high":
                fvg_high,

            "ifvg_confirmation_time":
                bars[
                    ifvg_confirm_index
                ]["time"],

            "retest_time":
                bars[
                    retest_index
                ]["time"],

            "entry_zone_low":
                entry_low,

            "entry_zone_high":
                entry_high,

            "second_csd":
                second_csd,

            "new_iof":
                new_iof,
        }

    if new_iof.get(
        "failed"
    ):

        return {
            "stage_rank":
                7,

            "status":
                "INVALIDATED",

            "reason": (
                "The new IOF range created "
                "after the second CSD failed."
            ),

            "timeframe":
                timeframe,

            "entry_zone_low":
                entry_low,

            "entry_zone_high":
                entry_high,

            "second_csd":
                second_csd,

            "new_iof":
                new_iof,
        }

    # --------------------------------------------------
    # COMPLETE ENTRY MODEL
    # --------------------------------------------------

    return {
        "stage_rank":
            8,

        "status":
            "ENTRY_MODEL_CONFIRMED",

        "timeframe":
            timeframe,

        "direction":
            direction,

        "fvg_negated":
            True,

        "fvg_negation_time":
            bars[
                negation_index
            ]["time"],

        "housing_time":
            housing["time"],

        "housing_low":
            housing["low"],

        "housing_high":
            housing["high"],

        "negated_fvg_direction":
            opposing_fvg[
                "direction"
            ],

        "ifvg_low":
            fvg_low,

        "ifvg_high":
            fvg_high,

        "ifvg_confirmation_time":
            bars[
                ifvg_confirm_index
            ]["time"],

        "retest_time":
            bars[
                retest_index
            ]["time"],

        "entry_zone_low":
            entry_low,

        "entry_zone_high":
            entry_high,

        "second_csd":
            second_csd,

        "new_iof":
            new_iof,

        "stop_reference": (
            unicorn[
                "breaker_low"
            ]
            if direction
            == "bullish"
            else unicorn[
                "breaker_high"
            ]
        ),

        "stop_rule": (
            "Below Breaker Block"
            if direction
            == "bullish"
            else
            "Above Breaker Block"
        ),

        "target_rule": (
            "Most recent opposing "
            "liquidity high/low from "
            "validated Trading Sand context."
        ),

        # Still deliberately undefined.
        "exact_order_price_defined":
            False,
    }



def _find_housing_model(
    symbol: str,
    direction: str,
    unicorn: dict,
    count: int,
    confirmation_bars: int,
):

    unicorn_tf = unicorn[
        "timeframe"
    ]

    start_index = (
        ENTRY_TIMEFRAMES.index(
            unicorn_tf
        )
    )

    search_tfs = (
        ENTRY_TIMEFRAMES[
            start_index:
        ]
    )

    best_pending = None

    opposite_direction = (
        "bearish"
        if direction == "bullish"
        else "bullish"
    )

    start_time = unicorn["breaker_source_start"]

    for timeframe in search_tfs:

        bars = _fetch(
            symbol,
            timeframe,
            count,
        )

        fvgs = _find_fvgs(
            bars
        )

        for fvg in fvgs:

            if (
                fvg["direction"]
                != opposite_direction
            ):
                continue

            if (
                _dt(
                    fvg["formed_time"]
                )
                < _dt(start_time)
            ):
                continue

            candidate = (
                _evaluate_housing_candidate(
                    bars=bars,
                    timeframe=timeframe,
                    direction=direction,
                    unicorn=unicorn,
                    opposing_fvg=fvg,
                    confirmation_bars=
                        confirmation_bars,
                )
            )

            if candidate is None:
                continue

            if (
                candidate["status"]
                == "ENTRY_MODEL_CONFIRMED"
            ):
                return candidate

            if (
                best_pending is None
                or candidate[
                    "stage_rank"
                ]
                > best_pending[
                    "stage_rank"
                ]
            ):
                best_pending = (
                    candidate
                )

    return (
        best_pending
        if best_pending is not None
        else {
            "stage_rank": 0,
            "status":
                "WAIT_NEGATED_FVG",
            "reason": (
                "No internal opposing FVG "
                "eligible for Housing-Candle "
                "negation was found inside "
                "the Unicorn structure."
            ),
        }
    )


def _clean(obj):
    if isinstance(obj, dict):
        return {
            key: _clean(value)
            for key, value
            in obj.items()
            if not key.startswith("_")
        }

    if isinstance(obj, list):
        return [
            _clean(value)
            for value in obj
        ]

    return obj



def evaluate_unicorn_entry(
    symbol: str,
    direction: str,
    count: int = 500,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
) -> dict:

    symbol = _normalize(
        symbol
    )

    direction = (
        direction
        .strip()
        .lower()
    )

    if direction not in {
        "bullish",
        "bearish",
    }:
        raise ValueError(
            "direction must be "
            "bullish or bearish"
        )

    if (
        pivot_window < 1
        or pivot_window > 10
    ):
        raise ValueError(
            "pivot_window must be "
            "between 1 and 10"
        )

    # --------------------------------------------------
    # Actual Breaker + directional FVG
    # --------------------------------------------------

    unicorn = _find_unicorn(
        symbol=symbol,
        direction=direction,
        count=count,
        pivot_window=
            pivot_window,
        confirmation_bars=
            confirmation_bars,
    )

    if unicorn is None:

        return {
            "symbol":
                symbol,

            "direction":
                direction,

            "status":
                "WAIT_UNICORN",

            "entry_gate_passed":
                False,

            "reason": (
                "No deterministic actual "
                "Breaker Block + directional "
                "FVG overlap was found."
            ),

            "execution_allowed":
                False,
        }

    if not unicorn[
        "retested"
    ]:

        return {
            "symbol":
                symbol,

            "direction":
                direction,

            "status":
                "WAIT_UNICORN_RETEST",

            "entry_gate_passed":
                False,

            "unicorn":
                _clean(
                    unicorn
                ),

            "reason": (
                "Unicorn exists, but "
                "price has not yet returned "
                "to the Breaker/FVG overlap."
            ),

            "execution_allowed":
                False,
        }

    # --------------------------------------------------
    # Nested Housing Candle / IFVG model
    # --------------------------------------------------

    housing = _find_housing_model(
        symbol=symbol,
        direction=direction,
        unicorn=unicorn,
        count=count,
        confirmation_bars=
            confirmation_bars,
    )

    confirmed = (
        housing.get(
            "status"
        )
        == "ENTRY_MODEL_CONFIRMED"
    )

    invalidated = (
        housing.get(
            "status"
        )
        == "INVALIDATED"
    )

    return {
        "symbol":
            symbol,

        "direction":
            direction,

        "status":
            housing.get(
                "status"
            ),

        "entry_gate_passed":
            confirmed,

        "entry_model_invalidated":
            invalidated,

        "unicorn":
            _clean(
                unicorn
            ),

        "housing_ifvg":
            _clean(
                housing
            ),

        "entry_zone": (
            {
                "low":
                    housing.get(
                        "entry_zone_low"
                    ),

                "high":
                    housing.get(
                        "entry_zone_high"
                    ),
            }
            if housing.get(
                "entry_zone_low"
            ) is not None
            else None
        ),

        "stop_reference":
            housing.get(
                "stop_reference"
            ),

        "stop_rule":
            housing.get(
                "stop_rule"
            ),

        "target_rule":
            housing.get(
                "target_rule"
            ),

        "exact_order_price_defined":
            False,

        "execution_allowed":
            False,

        "hierarchy": (
            "actual Breaker -> "
            "directional FVG overlap -> "
            "Unicorn retest -> "
            "explicit opposing-FVG negation -> "
            "Housing Candle -> "
            "body close through Housing Candle -> "
            "IFVG retest -> "
            "second CSD -> "
            "new IOF"
        ),
    }

