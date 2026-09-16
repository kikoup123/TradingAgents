from datetime import datetime, timedelta, timezone

from services.londres_market_gateway.models import Bar, aggregate_bars, bucket_start

UTC = timezone.utc


def _bar(open_time: datetime, price: float = 100.0) -> Bar:
    return Bar(
        symbol="NQ",
        open_time=open_time,
        open=price,
        high=price + 2,
        low=price - 2,
        close=price + 1,
        volume=10,
    )


def test_h4_bucket_uses_fixed_utc_minus_four_driver_schedule() -> None:
    # 06:37 in fixed UTC-4 is 10:37 UTC, so the H4 driver opens at 10:00 UTC.
    timestamp = datetime(2026, 9, 16, 10, 37, tzinfo=UTC)
    assert bucket_start(timestamp, "H4") == datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

    # The 18:00 fixed UTC-4 candle begins at 22:00 UTC.
    timestamp = datetime(2026, 9, 16, 23, 15, tzinfo=UTC)
    assert bucket_start(timestamp, "H4") == datetime(2026, 9, 16, 22, 0, tzinfo=UTC)


def test_daily_bucket_rolls_at_1800_fixed_utc_minus_four() -> None:
    before_roll = datetime(2026, 9, 16, 21, 59, tzinfo=UTC)
    after_roll = datetime(2026, 9, 16, 22, 0, tzinfo=UTC)

    assert bucket_start(before_roll, "D") == datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
    assert bucket_start(after_roll, "D") == datetime(2026, 9, 16, 22, 0, tzinfo=UTC)


def test_weekly_bucket_starts_monday_1800_fixed_utc_minus_four() -> None:
    monday_after_open = datetime(2026, 9, 14, 22, 30, tzinfo=UTC)
    monday_before_open = datetime(2026, 9, 14, 21, 30, tzinfo=UTC)

    assert bucket_start(monday_after_open, "W") == datetime(2026, 9, 14, 22, 0, tzinfo=UTC)
    assert bucket_start(monday_before_open, "W") == datetime(2026, 9, 7, 22, 0, tzinfo=UTC)


def test_five_minute_aggregation_returns_only_closed_buckets() -> None:
    start = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
    bars = [_bar(start + timedelta(minutes=minute), 100 + minute) for minute in range(7)]

    result = aggregate_bars(
        bars,
        "M5",
        as_of=datetime(2026, 9, 16, 14, 7, tzinfo=UTC),
    )

    assert len(result) == 1
    first = result[0]
    assert first.open_time == start
    assert first.open == 100
    assert first.close == 105
    assert first.high == 106
    assert first.low == 98
    assert first.volume == 50


def test_h4_aggregation_preserves_open_high_low_close_order() -> None:
    start = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    bars = [
        _bar(start, 100),
        _bar(start + timedelta(hours=1), 105),
        _bar(start + timedelta(hours=2), 103),
        _bar(start + timedelta(hours=3), 110),
    ]

    result = aggregate_bars(
        bars,
        "H4",
        as_of=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
    )

    assert len(result) == 1
    candle = result[0]
    assert candle.open == 100
    assert candle.high == 112
    assert candle.low == 98
    assert candle.close == 111
    assert candle.volume == 40
