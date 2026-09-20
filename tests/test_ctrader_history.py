from __future__ import annotations

from datetime import datetime, timezone

import tradingagents.dataflows.ctrader_history as history


def _bar(time: str, price: float) -> dict:
    return {
        "time": time,
        "open": price,
        "high": price + 1.0,
        "low": price - 1.0,
        "close": price + 0.25,
    }


def test_paginated_history_deduplicates_overlaps_and_sorts(monkeypatch) -> None:
    pages = [
        [
            _bar("2026-09-18T10:00:00+00:00", 10),
            _bar("2026-09-18T11:00:00+00:00", 11),
            _bar("2026-09-18T12:00:00+00:00", 12),
        ],
        [
            _bar("2026-09-18T08:00:00+00:00", 8),
            _bar("2026-09-18T09:00:00+00:00", 9),
            _bar("2026-09-18T10:00:00+00:00", 10),
        ],
        [
            _bar("2026-09-18T06:00:00+00:00", 6),
            _bar("2026-09-18T07:00:00+00:00", 7),
            _bar("2026-09-18T08:00:00+00:00", 8),
        ],
    ]
    calls: list[datetime] = []

    def fake_fetch_page(**kwargs):
        calls.append(kwargs["end_time"])
        return pages[len(calls) - 1]

    monkeypatch.setattr(history, "_fetch_page", fake_fetch_page)

    result = history.fetch_paginated_bars(
        symbol="NASDAQ",
        timeframe="H1",
        start_time="2026-09-18T07:00:00+00:00",
        end_time="2026-09-18T12:00:00+00:00",
        page_size=3,
    )

    assert result["pages_requested"] == 3
    assert result["count"] == 6
    assert [bar["time"] for bar in result["bars"]] == [
        "2026-09-18T07:00:00+00:00",
        "2026-09-18T08:00:00+00:00",
        "2026-09-18T09:00:00+00:00",
        "2026-09-18T10:00:00+00:00",
        "2026-09-18T11:00:00+00:00",
        "2026-09-18T12:00:00+00:00",
    ]
    assert result["execution_allowed"] is False


def test_paginated_history_stops_when_page_no_longer_moves_backward(
    monkeypatch,
) -> None:
    repeated = [
        _bar("2026-09-18T10:00:00+00:00", 10),
        _bar("2026-09-18T11:00:00+00:00", 11),
    ]
    calls = 0

    def fake_fetch_page(**kwargs):
        nonlocal calls
        calls += 1
        return repeated

    monkeypatch.setattr(history, "_fetch_page", fake_fetch_page)

    result = history.fetch_paginated_bars(
        symbol="NASDAQ",
        timeframe="H1",
        start_time="2026-09-01T00:00:00+00:00",
        end_time="2026-09-18T12:00:00+00:00",
        page_size=2,
        max_pages=10,
    )

    assert calls == 2
    assert result["count"] == 2


def test_history_range_normalizes_naive_datetimes_to_utc(monkeypatch) -> None:
    monkeypatch.setattr(
        history,
        "_fetch_page",
        lambda **kwargs: [
            _bar("2026-09-18T10:00:00+00:00", 10),
        ],
    )

    result = history.fetch_paginated_bars(
        symbol="NASDAQ",
        timeframe="H1",
        start_time=datetime(2026, 9, 18, 9),
        end_time=datetime(2026, 9, 18, 11, tzinfo=timezone.utc),
        page_size=10,
    )

    assert result["requested_start"] == "2026-09-18T09:00:00+00:00"
    assert result["requested_end"] == "2026-09-18T11:00:00+00:00"
    assert result["count"] == 1
