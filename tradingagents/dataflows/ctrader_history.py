"""Paginated historical bar retrieval through the local cTrader bridge.

The bridge worker is limited to 2,000 bars per request. This module pages
backward by request end-time, de-duplicates overlapping pages, and returns
chronologically sorted OHLC bars for a requested UTC range.

It is data-only. No trading or order execution is exposed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from tradingagents.dataflows.ctrader import BRIDGE_URL


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fetch_page(
    *,
    symbol: str,
    timeframe: str,
    count: int,
    end_time: datetime,
) -> list[dict[str, Any]]:
    response = requests.get(
        f"{BRIDGE_URL}/bars",
        params={
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count,
            "to": end_time.isoformat(),
        },
        timeout=55,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload.get("bars", [])


def fetch_paginated_bars(
    *,
    symbol: str,
    timeframe: str,
    start_time: datetime | str,
    end_time: datetime | str,
    page_size: int = 2000,
    max_pages: int = 200,
) -> dict[str, Any]:
    """Fetch a historical range by paging backward from the requested end time.

    Pages can overlap because cTrader trendbar requests are time-window based.
    Bars are keyed by their ISO timestamp, so overlaps are de-duplicated before
    chronological sorting.
    """

    if page_size < 1 or page_size > 2000:
        raise ValueError("page_size must be between 1 and 2000")
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")

    start = _as_utc(start_time)
    end = _as_utc(end_time)
    if start >= end:
        raise ValueError("start_time must be earlier than end_time")

    unique: dict[str, dict[str, Any]] = {}
    cursor = end
    pages_requested = 0
    previous_earliest: datetime | None = None

    while pages_requested < max_pages:
        page = _fetch_page(
            symbol=symbol,
            timeframe=timeframe,
            count=page_size,
            end_time=cursor,
        )
        pages_requested += 1

        if not page:
            break

        page_times: list[datetime] = []
        for bar in page:
            bar_time = _as_utc(bar["time"])
            page_times.append(bar_time)
            unique[bar_time.isoformat()] = {
                **bar,
                "time": bar_time.isoformat(),
            }

        earliest = min(page_times)
        if earliest <= start:
            break

        if previous_earliest is not None and earliest >= previous_earliest:
            break

        previous_earliest = earliest
        cursor = earliest - timedelta(minutes=1)

    bars = [
        bar
        for _, bar in sorted(unique.items())
        if start <= _as_utc(bar["time"]) <= end
    ]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "page_size": page_size,
        "pages_requested": pages_requested,
        "bars": bars,
        "count": len(bars),
        "first": bars[0]["time"] if bars else None,
        "last": bars[-1]["time"] if bars else None,
        "execution_allowed": False,
    }
