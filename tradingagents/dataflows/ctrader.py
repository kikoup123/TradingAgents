from __future__ import annotations

import requests


BRIDGE_URL = "http://127.0.0.1:8765"


_SYMBOL_ALIASES = {
    "XAUUSD": "XAUUSD",
    "XAU": "XAUUSD",
    "GOLD": "XAUUSD",

    "NASDAQ": "NASDAQ",
    "NAS100": "NASDAQ",
    "US100": "NASDAQ",
    "USTEC": "NASDAQ",
    "USTECH100": "NASDAQ",
    "US TECH 100": "NASDAQ",
    "NQ": "NASDAQ",

    "US500": "US500",
    "SPX500": "US500",
    "SP500": "US500",
    "S&P500": "US500",
    "S&P 500": "US500",
    "ES": "US500",
}

_ALLOWED_TIMEFRAMES = {
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


def _normalize_symbol(symbol: str) -> str:
    key = symbol.strip().upper()

    if key not in _SYMBOL_ALIASES:
        raise ValueError(
            f"Unsupported cTrader symbol: {symbol}. "
            "Supported instruments are XAUUSD, NASDAQ and US500."
        )

    return _SYMBOL_ALIASES[key]


def get_ctrader_health() -> dict:
    response = requests.get(
        f"{BRIDGE_URL}/health",
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def get_ctrader_bars(
    symbol: str,
    timeframe: str,
    count: int = 200,
) -> str:
    """
    Retrieve FP Markets OHLCV bars through the local cTrader bridge.

    Supported instruments:
      XAUUSD
      NASDAQ / NQ / US100
      US500 / ES / SPX500

    Supported timeframes:
      M1 M3 M5 M15 M30 H1 H4 D1 W1
    """

    canonical_symbol = _normalize_symbol(symbol)
    timeframe = timeframe.strip().upper()

    if timeframe not in _ALLOWED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported cTrader timeframe: {timeframe}"
        )

    if count < 1 or count > 2000:
        raise ValueError(
            "count must be between 1 and 2000"
        )

    response = requests.get(
        f"{BRIDGE_URL}/bars",
        params={
            "symbol": canonical_symbol,
            "timeframe": timeframe,
            "count": count,
        },
        timeout=50,
    )

    response.raise_for_status()

    payload = response.json()

    if "error" in payload:
        raise RuntimeError(
            f"cTrader bridge error: {payload['error']}"
        )

    bars = payload.get("bars", [])

    lines = [
        (
            f"# FP Markets cTrader data | "
            f"{canonical_symbol} | "
            f"{timeframe}"
        ),
        (
            f"# Account: {payload.get('account_id')} | "
            f"Environment: {payload.get('environment')}"
        ),
        f"# Records: {len(bars)}",
        "",
        "time,open,high,low,close,volume",
    ]

    for bar in bars:
        lines.append(
            f"{bar['time']},"
            f"{bar['open']},"
            f"{bar['high']},"
            f"{bar['low']},"
            f"{bar['close']},"
            f"{bar['volume']}"
        )

    return "\n".join(lines)
