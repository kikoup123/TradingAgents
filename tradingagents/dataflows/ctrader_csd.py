from __future__ import annotations

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL


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

TIMEFRAMES = {
    "M1",
    "M3",
    "M5",
    "M15",
    "M30",
    "H1",
    "H4",
    "D1",
    "W1",
}


def _normalize(symbol: str) -> str:
    key = symbol.strip().upper()

    if key not in SYMBOLS:
        raise ValueError(f"Unsupported CSD symbol: {symbol}")

    return SYMBOLS[key]


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


def _bullish(bar):
    return bar["close"] > bar["open"]


def _bearish(bar):
    return bar["close"] < bar["open"]


def _pivot_indices(bars, side: str, window: int):
    field = "high" if side == "high" else "low"
    result = []

    for i in range(window, len(bars) - window):
        current = bars[i][field]

        left = [
            bars[j][field]
            for j in range(i - window, i)
        ]

        right = [
            bars[j][field]
            for j in range(i + 1, i + window + 1)
        ]

        if side == "high":
            if current > max(left) and current >= max(right):
                result.append(i)
        else:
            if current < min(left) and current <= min(right):
                result.append(i)

    return result


def _opposing_stretch(
    bars,
    raid_index: int,
    direction: str,
):
    if direction == "bullish":
        predicate = _bearish
    else:
        predicate = _bullish

    end = raid_index

    if not predicate(bars[end]):
        end -= 1

    if end < 0 or not predicate(bars[end]):
        return None

    start = end

    while start - 1 >= 0 and predicate(bars[start - 1]):
        start -= 1

    indices = list(range(start, end + 1))

    return {
        "start_index": start,
        "end_index": end,
        "indices": indices,
    }


def _find_bullish_csd(
    bars,
    pivot_window: int,
    confirmation_bars: int,
):
    pivot_lows = _pivot_indices(
        bars,
        "low",
        pivot_window,
    )

    confirmed = []
    pending = []

    for raid_index in range(pivot_window + 1, len(bars)):
        prior = [
            p for p in pivot_lows
            if p < raid_index
        ]

        if not prior:
            continue

        reference_index = prior[-1]
        reference_low = bars[reference_index]["low"]
        raid = bars[raid_index]

        # Conservative liquidity raid:
        # trade below prior swing low and close back above it.
        if not (
            raid["low"] < reference_low
            and raid["close"] > reference_low
        ):
            continue

        stretch = _opposing_stretch(
            bars,
            raid_index,
            "bullish",
        )

        if stretch is None:
            continue

        stretch_bars = [
            bars[i]
            for i in stretch["indices"]
        ]

        # User rule:
        # reclaim OPEN of highest down-close candle.
        threshold = max(
            bar["open"]
            for bar in stretch_bars
        )

        confirm_index = None

        end = min(
            len(bars) - 1,
            raid_index + confirmation_bars,
        )

        for i in range(raid_index, end + 1):
            # Close is required. Wick above does not confirm.
            if bars[i]["close"] > threshold:
                confirm_index = i
                break

        protected_low = min(
            bars[i]["low"]
            for i in range(
                stretch["start_index"],
                (confirm_index if confirm_index is not None else raid_index) + 1,
            )
        )

        event = {
            "direction": "bullish",
            "liquidity": "sell_side_raid",
            "reference_swing_time": bars[reference_index]["time"],
            "reference_swing_low": reference_low,
            "raid_time": raid["time"],
            "raid_low": raid["low"],
            "stretch_start": bars[stretch["start_index"]]["time"],
            "stretch_end": bars[stretch["end_index"]]["time"],
            "stretch_candles": len(stretch["indices"]),
            "csd_threshold": threshold,
            "protected_low": protected_low,
            "confirmed": confirm_index is not None,
            "confirmation_time": (
                bars[confirm_index]["time"]
                if confirm_index is not None
                else None
            ),
            "confirmation_close": (
                bars[confirm_index]["close"]
                if confirm_index is not None
                else None
            ),
            "_confirm_index": confirm_index,
            "_raid_index": raid_index,
        }

        if confirm_index is None:
            pending.append(event)
        else:
            confirmed.append(event)

    latest_confirmed = (
        max(
            confirmed,
            key=lambda x: x["_confirm_index"],
        )
        if confirmed
        else None
    )

    latest_pending = (
        max(
            pending,
            key=lambda x: x["_raid_index"],
        )
        if pending
        else None
    )

    return latest_confirmed, latest_pending


def _find_bearish_csd(
    bars,
    pivot_window: int,
    confirmation_bars: int,
):
    pivot_highs = _pivot_indices(
        bars,
        "high",
        pivot_window,
    )

    confirmed = []
    pending = []

    for raid_index in range(pivot_window + 1, len(bars)):
        prior = [
            p for p in pivot_highs
            if p < raid_index
        ]

        if not prior:
            continue

        reference_index = prior[-1]
        reference_high = bars[reference_index]["high"]
        raid = bars[raid_index]

        # Conservative BSL raid:
        # trade above prior swing high and close back below it.
        if not (
            raid["high"] > reference_high
            and raid["close"] < reference_high
        ):
            continue

        stretch = _opposing_stretch(
            bars,
            raid_index,
            "bearish",
        )

        if stretch is None:
            continue

        stretch_bars = [
            bars[i]
            for i in stretch["indices"]
        ]

        # Inverse user rule:
        # reclaim OPEN of lowest up-close candle.
        threshold = min(
            bar["open"]
            for bar in stretch_bars
        )

        confirm_index = None

        end = min(
            len(bars) - 1,
            raid_index + confirmation_bars,
        )

        for i in range(raid_index, end + 1):
            if bars[i]["close"] < threshold:
                confirm_index = i
                break

        protected_high = max(
            bars[i]["high"]
            for i in range(
                stretch["start_index"],
                (confirm_index if confirm_index is not None else raid_index) + 1,
            )
        )

        event = {
            "direction": "bearish",
            "liquidity": "buy_side_raid",
            "reference_swing_time": bars[reference_index]["time"],
            "reference_swing_high": reference_high,
            "raid_time": raid["time"],
            "raid_high": raid["high"],
            "stretch_start": bars[stretch["start_index"]]["time"],
            "stretch_end": bars[stretch["end_index"]]["time"],
            "stretch_candles": len(stretch["indices"]),
            "csd_threshold": threshold,
            "protected_high": protected_high,
            "confirmed": confirm_index is not None,
            "confirmation_time": (
                bars[confirm_index]["time"]
                if confirm_index is not None
                else None
            ),
            "confirmation_close": (
                bars[confirm_index]["close"]
                if confirm_index is not None
                else None
            ),
            "_confirm_index": confirm_index,
            "_raid_index": raid_index,
        }

        if confirm_index is None:
            pending.append(event)
        else:
            confirmed.append(event)

    latest_confirmed = (
        max(
            confirmed,
            key=lambda x: x["_confirm_index"],
        )
        if confirmed
        else None
    )

    latest_pending = (
        max(
            pending,
            key=lambda x: x["_raid_index"],
        )
        if pending
        else None
    )

    return latest_confirmed, latest_pending


def _find_post_csd_iof(
    bars,
    csd_event,
):
    if not csd_event or not csd_event["confirmed"]:
        return {
            "confirmed": False,
            "reason": "No confirmed CSD",
        }

    direction = csd_event["direction"]
    csd_index = csd_event["_confirm_index"]

    candidates = []

    for source_index in range(csd_index + 1, len(bars) - 1):
        source = bars[source_index]

        if direction == "bullish":
            if not _bearish(source):
                continue

            confirm_index = None

            for i in range(source_index + 1, len(bars)):
                # Full bearish source range must be accepted above.
                if bars[i]["close"] > source["high"]:
                    confirm_index = i
                    break

            if confirm_index is None:
                continue

            failed = any(
                bars[i]["close"] < source["low"]
                for i in range(confirm_index + 1, len(bars))
            )

            candidates.append(
                {
                    "direction": "bullish",
                    "source_time": source["time"],
                    "source_low": source["low"],
                    "source_high": source["high"],
                    "confirmation_time": bars[confirm_index]["time"],
                    "confirmation_close": bars[confirm_index]["close"],
                    "confirmed": True,
                    "failed": failed,
                    "still_holding": not failed,
                    "_confirm_index": confirm_index,
                }
            )

        else:
            if not _bullish(source):
                continue

            confirm_index = None

            for i in range(source_index + 1, len(bars)):
                if bars[i]["close"] < source["low"]:
                    confirm_index = i
                    break

            if confirm_index is None:
                continue

            failed = any(
                bars[i]["close"] > source["high"]
                for i in range(confirm_index + 1, len(bars))
            )

            candidates.append(
                {
                    "direction": "bearish",
                    "source_time": source["time"],
                    "source_low": source["low"],
                    "source_high": source["high"],
                    "confirmation_time": bars[confirm_index]["time"],
                    "confirmation_close": bars[confirm_index]["close"],
                    "confirmed": True,
                    "failed": failed,
                    "still_holding": not failed,
                    "_confirm_index": confirm_index,
                }
            )

    if not candidates:
        return {
            "confirmed": False,
            "reason": "No new post-CSD IOF range confirmed",
        }

    return max(
        candidates,
        key=lambda x: x["_confirm_index"],
    )


def _clean_internal_keys(obj):
    if isinstance(obj, dict):
        return {
            key: _clean_internal_keys(value)
            for key, value in obj.items()
            if not key.startswith("_")
        }

    if isinstance(obj, list):
        return [
            _clean_internal_keys(value)
            for value in obj
        ]

    return obj


def analyze_csd_orderflow(
    symbol: str,
    timeframe: str = "M15",
    count: int = 300,
    pivot_window: int = 2,
    confirmation_bars: int = 10,
) -> dict:
    symbol = _normalize(symbol)
    timeframe = timeframe.strip().upper()

    if timeframe not in TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe: {timeframe}"
        )

    if count < 50 or count > 2000:
        raise ValueError(
            "count must be between 50 and 2000"
        )

    if pivot_window < 1 or pivot_window > 10:
        raise ValueError(
            "pivot_window must be between 1 and 10"
        )

    bars = _fetch(
        symbol,
        timeframe,
        count,
    )

    bullish, bullish_pending = _find_bullish_csd(
        bars,
        pivot_window,
        confirmation_bars,
    )

    bearish, bearish_pending = _find_bearish_csd(
        bars,
        pivot_window,
        confirmation_bars,
    )

    confirmed_events = [
        event
        for event in (bullish, bearish)
        if event is not None
    ]

    active_csd = (
        max(
            confirmed_events,
            key=lambda x: x["_confirm_index"],
        )
        if confirmed_events
        else None
    )

    post_csd_iof = _find_post_csd_iof(
        bars,
        active_csd,
    )

    current_control = "none"

    if active_csd:
        direction = active_csd["direction"]

        if direction == "bullish":
            protected_broken = (
                bars[-1]["close"]
                < active_csd["protected_low"]
            )
        else:
            protected_broken = (
                bars[-1]["close"]
                > active_csd["protected_high"]
            )

        if protected_broken:
            current_control = "transition"
        elif (
            post_csd_iof.get("confirmed")
            and post_csd_iof.get("still_holding")
        ):
            current_control = f"{direction}_control"
        else:
            current_control = f"{direction}_csd_only"

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_analyzed": len(bars),

        "latest_bullish_csd": bullish,
        "latest_bearish_csd": bearish,

        "latest_pending_bullish_csd": bullish_pending,
        "latest_pending_bearish_csd": bearish_pending,

        "active_csd": active_csd,
        "post_csd_iof": post_csd_iof,

        "current_orderflow_control": current_control,

        "rules": {
            "bullish_csd": (
                "SSL raid -> opposing down-close stretch -> "
                "body close above highest down-close open"
            ),
            "bearish_csd": (
                "BSL raid -> opposing up-close stretch -> "
                "body close below lowest up-close open"
            ),
            "wick_only_confirmation": False,
            "csd_alone_confirms_continuation": False,
            "post_csd_iof_required_for_control": True,
        },

        "hierarchy": (
            "HTF context -> liquidity raid -> SMT -> CSD -> "
            "post-CSD IOF/IOFC -> LTF continuation"
        ),
    }

    return _clean_internal_keys(result)
