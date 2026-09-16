from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

FIXED_UTC_MINUS_4 = timezone(timedelta(hours=-4), name="UTC-4")
SUPPORTED_TIMEFRAMES = frozenset({"M1", "M5", "M15", "H1", "H4", "D", "W"})


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("open_time must be timezone-aware")
        prices = (self.open, self.high, self.low, self.close)
        if not all(price > 0 for price in prices):
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar OHLC geometry is invalid")
        if self.high < self.low:
            raise ValueError("bar high must be >= low")
        if self.volume is not None and self.volume < 0:
            raise ValueError("bar volume must be >= 0")


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("quote timestamp must be timezone-aware")
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("quote bid/ask is invalid")


def _londres_anchor(local_time: datetime) -> datetime:
    anchor = local_time.replace(hour=18, minute=0, second=0, microsecond=0)
    if local_time < anchor:
        anchor -= timedelta(days=1)
    return anchor


def bucket_start(timestamp: datetime, timeframe: str) -> datetime:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")

    utc_time = timestamp.astimezone(UTC)
    if timeframe == "M1":
        return utc_time.replace(second=0, microsecond=0)
    if timeframe == "M5":
        minute = utc_time.minute - (utc_time.minute % 5)
        return utc_time.replace(minute=minute, second=0, microsecond=0)
    if timeframe == "M15":
        minute = utc_time.minute - (utc_time.minute % 15)
        return utc_time.replace(minute=minute, second=0, microsecond=0)
    if timeframe == "H1":
        return utc_time.replace(minute=0, second=0, microsecond=0)

    local = utc_time.astimezone(FIXED_UTC_MINUS_4)
    if timeframe == "H4":
        anchor = _londres_anchor(local)
        elapsed = local - anchor
        slot = int(elapsed.total_seconds() // (4 * 60 * 60))
        return (anchor + timedelta(hours=slot * 4)).astimezone(UTC)
    if timeframe == "D":
        return _londres_anchor(local).astimezone(UTC)

    monday = local - timedelta(days=local.weekday())
    weekly_anchor = monday.replace(hour=18, minute=0, second=0, microsecond=0)
    if local < weekly_anchor:
        weekly_anchor -= timedelta(days=7)
    return weekly_anchor.astimezone(UTC)


def timeframe_duration(timeframe: str) -> timedelta:
    durations = {
        "M1": timedelta(minutes=1),
        "M5": timedelta(minutes=5),
        "M15": timedelta(minutes=15),
        "H1": timedelta(hours=1),
        "H4": timedelta(hours=4),
        "D": timedelta(days=1),
        "W": timedelta(days=7),
    }
    try:
        return durations[timeframe]
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe: {timeframe}") from exc


def aggregate_bars(
    bars: list[Bar],
    timeframe: str,
    *,
    as_of: datetime | None = None,
    closed_only: bool = True,
) -> list[Bar]:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if not bars:
        return []

    cutoff = (as_of or datetime.now(UTC)).astimezone(UTC)
    grouped: dict[datetime, list[Bar]] = {}
    for bar in sorted(bars, key=lambda item: item.open_time):
        key = bucket_start(bar.open_time, timeframe)
        grouped.setdefault(key, []).append(bar)

    duration = timeframe_duration(timeframe)
    output: list[Bar] = []
    for start in sorted(grouped):
        if closed_only and start + duration > cutoff:
            continue
        members = grouped[start]
        volume_values = [member.volume for member in members if member.volume is not None]
        output.append(
            Bar(
                symbol=members[0].symbol,
                open_time=start,
                open=members[0].open,
                high=max(member.high for member in members),
                low=min(member.low for member in members),
                close=members[-1].close,
                volume=sum(volume_values) if volume_values else None,
            )
        )
    return output


def merge_bars(*series: list[Bar]) -> list[Bar]:
    merged: dict[datetime, Bar] = {}
    for bars in series:
        for bar in bars:
            merged[bar.open_time.astimezone(UTC)] = bar
    return [merged[key] for key in sorted(merged)]
