from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status

from .ctrader_mobile import router as ctrader_router
from .ctrader_mock import router as ctrader_mock_router
from .models import SUPPORTED_TIMEFRAMES, UTC, Bar, Quote
from .rolling_databento_feed import RollingDatabentoMarketFeed

feed = RollingDatabentoMarketFeed()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await feed.start()
    try:
        yield
    finally:
        await feed.stop()


app = FastAPI(
    title="Londres Market Data Gateway",
    version="0.3.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(ctrader_router)
app.include_router(ctrader_mock_router)


def _authorize(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("LONDRES_GATEWAY_BEARER_TOKEN", "").strip()
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


Auth = Annotated[None, Depends(_authorize)]


@app.get("/health")
def health() -> dict:
    return feed.status()


@app.get("/v1/market/candles")
def candles(
    _: Auth,
    symbol: str = Query(min_length=1, max_length=16),
    timeframe: str = Query(min_length=1, max_length=4),
    limit: int = Query(default=500, ge=1, le=20_000),
) -> dict:
    frame = timeframe.upper()
    if frame not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {frame}")
    try:
        bars = feed.candles(symbol, frame, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not bars:
        raise HTTPException(status_code=503, detail="Market-data cache is not ready")
    return {"candles": [_bar_payload(bar, frame) for bar in bars]}


@app.get("/v1/market/quote")
def quote(
    _: Auth,
    symbol: str = Query(min_length=1, max_length=16),
) -> dict:
    try:
        current = feed.quote(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _quote_payload(current)


def _bar_payload(bar: Bar, timeframe: str) -> dict:
    return {
        "symbol": bar.symbol,
        "timeframe": timeframe,
        "openTime": bar.open_time.astimezone(UTC).isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _quote_payload(quote: Quote) -> dict:
    return {
        "symbol": quote.symbol,
        "bid": quote.bid,
        "ask": quote.ask,
        "timestamp": quote.timestamp.astimezone(UTC).isoformat(),
    }
