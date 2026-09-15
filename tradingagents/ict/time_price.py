from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

from .daily_profile import DailyProfileEngine, FIXED_UTC_MINUS_4
from .order_flow import _normalize_ohlc


@dataclass(frozen=True)
class OpenSpec:
    key: str
    label: str
    hour: int
    minute: int
    timezone: str = "America/New_York"


@dataclass(frozen=True)
class ONSConfig:
    key: str
    label: str
    timezone: str
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    range_type: str = "Wicks"
    show_half_deviations: bool = True
    projection_count: int = 2


@dataclass
class OpenLevelResult:
    key: str
    label: str
    price: float | None
    timestamp: str | None
    source_timezone: str
    relation: str
    touched: bool
    crossed: bool
    last_cross_time: str | None
    available: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ONSRangeResult:
    key: str
    label: str
    source_timezone: str
    range_type: str
    start_time: str | None
    end_time: str | None
    high: float | None
    low: float | None
    equilibrium: float | None
    range_size: float | None
    upper_projections: list[dict] = field(default_factory=list)
    lower_projections: list[dict] = field(default_factory=list)
    status: str = "UNAVAILABLE"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TimePriceResult:
    trading_day: str
    canonical_clock: str
    current_price: float
    as_of: str
    opens: dict[str, OpenLevelResult]
    ons: dict[str, ONSRangeResult]

    def to_dict(self) -> dict:
        return {
            "trading_day": self.trading_day,
            "canonical_clock": self.canonical_clock,
            "current_price": self.current_price,
            "as_of": self.as_of,
            "opens": {key: value.to_dict() for key, value in self.opens.items()},
            "ons": {key: value.to_dict() for key, value in self.ons.items()},
        }


DEFAULT_OPEN_SPECS = (
    OpenSpec("asian_open", "ASIAN OPEN", 19, 30),
    OpenSpec("midnight_open", "MIDNIGHT OPEN", 0, 0),
    OpenSpec("london_open", "LD OPEN", 1, 30),
    OpenSpec("open_0200", "02:00", 2, 0),
    OpenSpec("ny_premarket_open", "NY PREMARKET", 7, 30),
    OpenSpec("open_0830", "08:30", 8, 30),
    OpenSpec("equities_open", "EQUITIES OPEN", 9, 30),
    OpenSpec("open_1000", "10:00", 10, 0),
    OpenSpec("afternoon_open", "13:30", 13, 30),
    OpenSpec("open_1400", "14:00", 14, 0),
    OpenSpec("settlement_open", "SETTLEMENT", 18, 0),
)

DEFAULT_ONS_CONFIGS = (
    ONSConfig("ny_ons", "ONS — New York", "America/Chicago", 4, 0, 8, 0),
    ONSConfig("london_ons", "ONS — London", "Europe/London", 5, 0, 7, 0),
    ONSConfig("asia_ons", "ONS — Asia", "Asia/Tokyo", 7, 0, 9, 0),
)


class TimePriceEngine:
    """Deterministic port of the user's opening-price + ONS Pine logic.

    The profile stack keeps its canonical fixed UTC-4 trading day. Opening-price
    specifications preserve the Pine script's `America/New_York` clock by
    default, while each ONS preserves its own Pine timezone. All outputs are
    normalized back to fixed UTC-4 timestamps so downstream agents use one
    canonical clock.

    Exact opening prices are only emitted when a bar exists at the configured
    minute. Missing data is reported as unavailable rather than guessed.
    """

    def __init__(
        self,
        *,
        open_specs: tuple[OpenSpec, ...] = DEFAULT_OPEN_SPECS,
        ons_configs: tuple[ONSConfig, ...] = DEFAULT_ONS_CONFIGS,
    ) -> None:
        self.open_specs = open_specs
        self.ons_configs = ons_configs

    def analyze(
        self,
        minute_bars: pd.DataFrame,
        *,
        as_of: pd.Timestamp | str | None = None,
        current_price: float | None = None,
    ) -> TimePriceResult:
        data = DailyProfileEngine._canonicalize(_normalize_ohlc(minute_bars))
        if as_of is not None:
            resolved_as_of = self._canonical_timestamp(as_of)
            data = data.loc[data.index <= resolved_as_of]
            if data.empty:
                raise ValueError("no bars exist at or before as_of")
        else:
            resolved_as_of = data.index[-1]

        trading_day = DailyProfileEngine._trading_day_key(resolved_as_of)
        keys = [DailyProfileEngine._trading_day_key(ts) for ts in data.index]
        current = data.loc[[key == trading_day for key in keys]].copy()
        if current.empty:
            raise ValueError("no bars exist for the active fixed UTC-4 trading day")

        px = float(current.iloc[-1]["close"] if current_price is None else current_price)

        opens = {
            spec.key: self._open_level(current, spec=spec, current_price=px)
            for spec in self.open_specs
        }
        ons = {
            config.key: self._ons_range(current, config=config, as_of=resolved_as_of)
            for config in self.ons_configs
        }

        return TimePriceResult(
            trading_day=trading_day.isoformat(),
            canonical_clock="UTC-4_FIXED",
            current_price=px,
            as_of=resolved_as_of.isoformat(),
            opens=opens,
            ons=ons,
        )

    @staticmethod
    def _canonical_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize(FIXED_UTC_MINUS_4)
        return ts.tz_convert(FIXED_UTC_MINUS_4)

    @staticmethod
    def _clock_mask(index: pd.DatetimeIndex, spec: OpenSpec) -> pd.Series:
        local = index.tz_convert(ZoneInfo(spec.timezone))
        return pd.Series(
            (local.hour == spec.hour) & (local.minute == spec.minute),
            index=index,
        )

    def _open_level(
        self,
        current: pd.DataFrame,
        *,
        spec: OpenSpec,
        current_price: float,
    ) -> OpenLevelResult:
        mask = self._clock_mask(current.index, spec)
        matches = current.loc[mask.to_numpy()]
        if matches.empty:
            return OpenLevelResult(
                key=spec.key,
                label=spec.label,
                price=None,
                timestamp=None,
                source_timezone=spec.timezone,
                relation="UNAVAILABLE",
                touched=False,
                crossed=False,
                last_cross_time=None,
                available=False,
            )

        row = matches.iloc[-1]
        timestamp = matches.index[-1]
        level = float(row["open"])
        post = current.loc[current.index >= timestamp]
        revisit = current.loc[current.index > timestamp]

        relation = "AT"
        if current_price > level:
            relation = "ABOVE"
        elif current_price < level:
            relation = "BELOW"

        touched = bool(
            ((revisit["low"] <= level) & (revisit["high"] >= level)).any()
        ) if not revisit.empty else False

        closes = post["close"] - level
        signs = closes.apply(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
        nonzero = signs[signs != 0]
        previous = nonzero.shift(1)
        crossings = nonzero[(previous.notna()) & (nonzero != previous)]
        crossed = not crossings.empty
        last_cross_time = crossings.index[-1].isoformat() if crossed else None

        return OpenLevelResult(
            key=spec.key,
            label=spec.label,
            price=level,
            timestamp=timestamp.isoformat(),
            source_timezone=spec.timezone,
            relation=relation,
            touched=touched,
            crossed=crossed,
            last_cross_time=last_cross_time,
            available=True,
        )

    def _ons_range(
        self,
        current: pd.DataFrame,
        *,
        config: ONSConfig,
        as_of: pd.Timestamp,
    ) -> ONSRangeResult:
        if config.range_type not in {"Wicks", "Bodies"}:
            raise ValueError("ONS range_type must be 'Wicks' or 'Bodies'")
        if config.projection_count < 1:
            raise ValueError("ONS projection_count must be >= 1")

        source_zone = ZoneInfo(config.timezone)
        source_index = current.index.tz_convert(source_zone)
        start_clock = time(config.start_hour, config.start_minute)
        end_clock = time(config.end_hour, config.end_minute)
        local_times = [ts.time().replace(tzinfo=None) for ts in source_index]

        if start_clock < end_clock:
            mask = [(clock >= start_clock and clock < end_clock) for clock in local_times]
        else:
            mask = [(clock >= start_clock or clock < end_clock) for clock in local_times]

        session = current.loc[mask].copy()
        if session.empty:
            return ONSRangeResult(
                key=config.key,
                label=config.label,
                source_timezone=config.timezone,
                range_type=config.range_type,
                start_time=None,
                end_time=None,
                high=None,
                low=None,
                equilibrium=None,
                range_size=None,
            )

        local_session_index = session.index.tz_convert(source_zone)
        source_dates = pd.Index([ts.date() for ts in local_session_index])

        if start_clock < end_clock:
            selected_anchor = source_dates[-1]
            same_session = source_dates == selected_anchor
        else:
            # Anchor an overnight session to the date on which its start occurs.
            anchors = []
            for ts in local_session_index:
                clock = ts.time().replace(tzinfo=None)
                anchor = ts.date() if clock >= start_clock else (ts - pd.Timedelta(days=1)).date()
                anchors.append(anchor)
            anchors = pd.Index(anchors)
            selected_anchor = anchors[-1]
            same_session = anchors == selected_anchor

        session = session.loc[same_session]
        local_session_index = session.index.tz_convert(source_zone)

        if config.range_type == "Wicks":
            high = float(session["high"].max())
            low = float(session["low"].min())
        else:
            body_high = session[["open", "close"]].max(axis=1)
            body_low = session[["open", "close"]].min(axis=1)
            high = float(body_high.max())
            low = float(body_low.min())

        equilibrium = (high + low) / 2.0
        range_size = high - low
        multiplier = 0.5 if config.show_half_deviations else 1.0
        upper = []
        lower = []
        for i in range(1, config.projection_count + 1):
            deviation = i * multiplier
            upper.append({"deviation": deviation, "price": high + deviation * range_size})
            lower.append({"deviation": -deviation, "price": low - deviation * range_size})

        first = session.index[0]
        anchor = pd.Timestamp(selected_anchor).tz_localize(source_zone)
        expected_end = anchor + pd.Timedelta(
            hours=config.end_hour, minutes=config.end_minute
        )
        if start_clock >= end_clock:
            expected_end += pd.Timedelta(days=1)
        expected_end = expected_end.tz_convert(FIXED_UTC_MINUS_4)
        status = "ACTIVE" if as_of < expected_end else "COMPLETE"

        return ONSRangeResult(
            key=config.key,
            label=config.label,
            source_timezone=config.timezone,
            range_type=config.range_type,
            start_time=first.isoformat(),
            end_time=expected_end.isoformat(),
            high=high,
            low=low,
            equilibrium=equilibrium,
            range_size=range_size,
            upper_projections=upper,
            lower_projections=lower,
            status=status,
        )
