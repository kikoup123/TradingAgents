from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import UTC, Bar, Quote, aggregate_bars, merge_bars

try:
    import databento as db
except ImportError:  # pragma: no cover - exercised only without gateway extras installed
    db = None


@dataclass(frozen=True, slots=True)
class DatabentoConfig:
    dataset: str = "GLBX.MDP3"
    symbols: tuple[str, ...] = ("NQ", "ES", "YM")
    live: bool = True
    minute_history_days: int = 35
    hourly_history_days: int = 900
    bootstrap_delay_minutes: int = 15
    live_replay_minutes: int = 30

    @classmethod
    def from_env(cls) -> DatabentoConfig:
        symbols = tuple(
            symbol.strip().upper()
            for symbol in os.getenv("LONDRES_MARKET_SYMBOLS", "NQ,ES,YM").split(",")
            if symbol.strip()
        )
        live_value = os.getenv("LONDRES_MARKET_LIVE", "true").strip().lower()
        return cls(
            dataset=os.getenv("DATABENTO_DATASET", "GLBX.MDP3").strip(),
            symbols=symbols or ("NQ", "ES", "YM"),
            live=live_value in {"1", "true", "yes", "on"},
            minute_history_days=max(
                7, int(os.getenv("LONDRES_MINUTE_HISTORY_DAYS", "35"))
            ),
            hourly_history_days=max(
                400, int(os.getenv("LONDRES_HOURLY_HISTORY_DAYS", "900"))
            ),
            bootstrap_delay_minutes=max(
                10, int(os.getenv("LONDRES_BOOTSTRAP_DELAY_MINUTES", "15"))
            ),
            live_replay_minutes=max(
                5, int(os.getenv("LONDRES_LIVE_REPLAY_MINUTES", "30"))
            ),
        )


class DatabentoMarketFeed:
    """One Databento session normalized into the Londres app candle/quote contract.

    The vendor API key stays on this server. The iOS app never receives it.
    Historical requests seed the in-memory cache once; the live API then appends
    closed M1 bars and BBO updates. Higher Londres timeframes are derived from
    cached bars, so app polling does not repeatedly bill large historical pulls.
    """

    def __init__(self, config: DatabentoConfig | None = None) -> None:
        self.config = config or DatabentoConfig.from_env()
        self._lock = threading.RLock()
        self._minute: dict[str, list[Bar]] = {symbol: [] for symbol in self.config.symbols}
        self._hour: dict[str, list[Bar]] = {symbol: [] for symbol in self.config.symbols}
        self._quotes: dict[str, Quote] = {}
        self._instrument_symbols: dict[int, str] = {}
        self._live_task: asyncio.Task[None] | None = None
        self._ready = False
        self._live_connected = False
        self._last_error: str | None = None
        self._last_bootstrap_at: datetime | None = None

    async def start(self) -> None:
        self._require_runtime()
        await asyncio.to_thread(self._bootstrap)
        self._ready = True
        if self.config.live:
            self._live_task = asyncio.create_task(self._live_loop())

    async def stop(self) -> None:
        task = self._live_task
        self._live_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._live_connected = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": "databento",
                "dataset": self.config.dataset,
                "symbols": list(self.config.symbols),
                "ready": self._ready,
                "liveEnabled": self.config.live,
                "liveConnected": self._live_connected,
                "lastBootstrapAt": _iso(self._last_bootstrap_at),
                "lastError": self._last_error,
                "minuteBars": {
                    symbol: len(self._minute.get(symbol, [])) for symbol in self.config.symbols
                },
                "hourBars": {
                    symbol: len(self._hour.get(symbol, [])) for symbol in self.config.symbols
                },
            }

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        root = self._validate_symbol(symbol)
        if limit <= 0 or limit > 20_000:
            raise ValueError("limit must be between 1 and 20000")

        now = datetime.now(UTC)
        with self._lock:
            minute = list(self._minute[root])
            hourly = list(self._hour[root])

        if timeframe == "M1":
            result = aggregate_bars(minute, "M1", as_of=now)
        elif timeframe in {"M5", "M15"}:
            result = aggregate_bars(minute, timeframe, as_of=now)
        else:
            recent_hourly = aggregate_bars(minute, "H1", as_of=now)
            complete_hourly = merge_bars(hourly, recent_hourly)
            if timeframe == "H1":
                result = complete_hourly
            elif timeframe in {"H4", "D", "W"}:
                result = aggregate_bars(complete_hourly, timeframe, as_of=now)
            else:
                raise ValueError(f"unsupported timeframe: {timeframe}")
        return result[-limit:]

    def quote(self, symbol: str) -> Quote:
        root = self._validate_symbol(symbol)
        with self._lock:
            quote = self._quotes.get(root)
            minute = list(self._minute[root])
        if quote is not None:
            return quote
        if not self.config.live and minute:
            last = minute[-1]
            return Quote(
                symbol=root,
                bid=last.close,
                ask=last.close,
                timestamp=last.open_time + timedelta(minutes=1),
            )
        raise LookupError(f"no live quote available for {root}")

    def _require_runtime(self) -> None:
        if db is None:
            raise RuntimeError(
                "Databento gateway dependencies are not installed. "
                "Install services/londres_market_gateway/requirements.txt."
            )
        if not os.getenv("DATABENTO_API_KEY", "").strip():
            raise RuntimeError("DATABENTO_API_KEY is required on the gateway server")

    def _bootstrap(self) -> None:
        assert db is not None
        client = db.Historical()
        now = datetime.now(UTC)
        end = now - timedelta(minutes=self.config.bootstrap_delay_minutes)

        for symbol in self.config.symbols:
            minute = self._historical_bars(
                client,
                symbol=symbol,
                schema="ohlcv-1m",
                start=end - timedelta(days=self.config.minute_history_days),
                end=end,
            )
            hourly = self._historical_bars(
                client,
                symbol=symbol,
                schema="ohlcv-1h",
                start=end - timedelta(days=self.config.hourly_history_days),
                end=end,
            )
            with self._lock:
                self._minute[symbol] = minute
                self._hour[symbol] = hourly
        self._last_bootstrap_at = now
        self._last_error = None

    def _historical_bars(
        self,
        client: Any,
        *,
        symbol: str,
        schema: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        data = client.timeseries.get_range(
            dataset=self.config.dataset,
            schema=schema,
            stype_in="continuous",
            symbols=f"{symbol}.v.0",
            start=start.isoformat(),
            end=end.isoformat(),
        )
        frame = data.to_df()
        bars: list[Bar] = []
        for timestamp, row in frame.iterrows():
            open_time = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
            bars.append(
                Bar(
                    symbol=symbol,
                    open_time=_aware(open_time),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]) if row.get("volume") is not None else None,
                )
            )
        return bars

    async def _live_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._live_session()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._live_connected = False
                self._last_error = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _live_session(self) -> None:
        assert db is not None
        client = db.Live()
        symbols = [f"{symbol}.v.0" for symbol in self.config.symbols]
        replay_start = datetime.now(UTC) - timedelta(minutes=self.config.live_replay_minutes)
        client.subscribe(
            dataset=self.config.dataset,
            schema="ohlcv-1m",
            stype_in="continuous",
            symbols=symbols,
            start=replay_start.isoformat(),
        )
        client.subscribe(
            dataset=self.config.dataset,
            schema="bbo-1s",
            stype_in="continuous",
            symbols=symbols,
            start=replay_start.isoformat(),
        )
        self._live_connected = True
        self._last_error = None
        try:
            async for record in client:
                self._consume_record(record)
        finally:
            self._live_connected = False
            with contextlib.suppress(Exception):
                client.stop()

    def _consume_record(self, record: Any) -> None:
        if hasattr(record, "stype_in_symbol") and hasattr(record, "instrument_id"):
            requested = _text(record.stype_in_symbol).upper()
            root = requested.split(".", maxsplit=1)[0]
            if root in self.config.symbols:
                self._instrument_symbols[int(record.instrument_id)] = root
            return

        instrument_id = getattr(record, "instrument_id", None)
        if instrument_id is None:
            if record.__class__.__name__ == "ErrorMsg":
                raise RuntimeError(_text(getattr(record, "err", "Databento live error")))
            return
        symbol = self._instrument_symbols.get(int(instrument_id))
        if symbol is None:
            return

        if all(hasattr(record, field) for field in ("pretty_open", "pretty_high", "pretty_low", "pretty_close")):
            bar = Bar(
                symbol=symbol,
                open_time=_record_time(record, "pretty_ts_event"),
                open=float(record.pretty_open),
                high=float(record.pretty_high),
                low=float(record.pretty_low),
                close=float(record.pretty_close),
                volume=float(getattr(record, "volume", 0)),
            )
            with self._lock:
                self._minute[symbol] = merge_bars(self._minute[symbol], [bar])[-70_000:]
            return

        if hasattr(record, "bid_px_00") and hasattr(record, "ask_px_00"):
            bid_raw = int(record.bid_px_00)
            ask_raw = int(record.ask_px_00)
            max_int64 = (1 << 63) - 1
            if bid_raw >= max_int64 - 10 or ask_raw >= max_int64 - 10:
                return
            quote = Quote(
                symbol=symbol,
                bid=bid_raw / 1_000_000_000,
                ask=ask_raw / 1_000_000_000,
                timestamp=_record_time(record, "pretty_ts_recv", "pretty_ts_event"),
            )
            with self._lock:
                self._quotes[symbol] = quote

    def _validate_symbol(self, symbol: str) -> str:
        root = symbol.strip().upper()
        if root not in self.config.symbols:
            raise ValueError(f"unsupported symbol: {root}")
        return root


def _record_time(record: Any, *fields: str) -> datetime:
    for field in fields:
        value = getattr(record, field, None)
        if value is not None:
            return _parse_datetime(value)
    raise ValueError("record has no usable timestamp")


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    text = _text(value).strip().replace("Z", "+00:00")
    return _aware(datetime.fromisoformat(text))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
