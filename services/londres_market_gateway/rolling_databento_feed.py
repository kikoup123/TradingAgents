from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime, timedelta
from typing import Any

from .databento_feed import DatabentoMarketFeed, db
from .models import UTC


class RollingDatabentoMarketFeed(DatabentoMarketFeed):
    """Databento feed that periodically reconnects live smart-symbol subscriptions.

    Databento continuous live subscriptions resolve to a concrete instrument when
    the subscription starts and do not remap automatically while that session
    remains open. Reconnecting forces NQ.v.0 / ES.v.0 / YM.v.0 to resolve again,
    which prevents a long-running gateway from remaining pinned to an old front
    contract through a volume-roll transition.
    """

    def __init__(self, *args: Any, live_session_minutes: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        configured = live_session_minutes
        if configured is None:
            configured = int(os.getenv("LONDRES_LIVE_SESSION_MINUTES", "360"))
        self.live_session_minutes = max(15, configured)
        self._last_live_reconnect_at: datetime | None = None

    def status(self) -> dict[str, Any]:
        payload = super().status()
        payload.update(
            {
                "liveSessionMinutes": self.live_session_minutes,
                "lastLiveReconnectAt": (
                    self._last_live_reconnect_at.astimezone(UTC).isoformat()
                    if self._last_live_reconnect_at is not None
                    else None
                ),
            }
        )
        return payload

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

        self._instrument_symbols.clear()
        self._live_connected = True
        self._last_error = None
        self._last_live_reconnect_at = datetime.now(UTC)

        async def consume() -> None:
            async for record in client:
                self._consume_record(record)

        try:
            await asyncio.wait_for(
                consume(),
                timeout=float(self.live_session_minutes * 60),
            )
        except asyncio.TimeoutError:
            # Normal rollover-safety reconnect. The outer live loop immediately
            # opens a fresh Databento session and resolves each continuous symbol
            # again to the current volume-leading contract.
            self._last_error = None
        finally:
            self._live_connected = False
            with contextlib.suppress(Exception):
                client.stop()
