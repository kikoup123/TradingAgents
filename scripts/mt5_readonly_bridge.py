#!/usr/bin/env python3
"""Publish a sanitized read-only MetaTrader 5 snapshot for Londres.

Run this script on the machine/session where the MT5 terminal is already logged
into the intended demo or live brokerage account. It never calls order_send,
never stores credentials, and never writes the full account login to the
snapshot. Account environment is retained only in the private local snapshot and
must never be surfaced by the public adapter.

Example:
    python scripts/mt5_readonly_bridge.py \
      --output ~/.londres/vantage-mt5.json \
      --symbol NASDAQ=NAS100 \
      --symbol SP500=SP500 \
      --symbol DOW=DJ30 \
      --symbol GOLD=XAUUSD
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any


def _finite_positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"MT5 {name} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise RuntimeError(f"MT5 {name} must be finite and > 0")
    return number


def _parse_symbol(value: str) -> tuple[str, str]:
    canonical, separator, broker_symbol = value.partition("=")
    canonical = canonical.strip().upper()
    broker_symbol = broker_symbol.strip()
    if separator != "=" or not canonical or not broker_symbol:
        raise argparse.ArgumentTypeError("--symbol must use CANONICAL=BROKER_SYMBOL")
    return canonical, broker_symbol


def _masked_login(login: Any) -> str:
    text = str(login or "")
    return f"••••{text[-4:]}" if text else "••••"


def _account_key(server: str, login: Any) -> str:
    material = f"{server}:{login}".encode()
    return hashlib.sha256(material).hexdigest()


def _private_trade_mode(mt5: Any, account_data: dict[str, Any]) -> str:
    raw_mode = int(account_data.get("trade_mode", -1))
    demo_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
    contest_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1))
    real_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2))
    if raw_mode == real_mode:
        return "REAL"
    if raw_mode == demo_mode:
        return "DEMO"
    if raw_mode == contest_mode:
        raise RuntimeError("Londres MT5 bridge does not accept contest accounts")
    raise RuntimeError("Londres MT5 bridge accepts demo or live brokerage accounts only")


def _snapshot(mt5: Any, symbols: list[tuple[str, str]]) -> dict[str, Any]:
    generated_at_ms = time.time_ns() // 1_000_000
    account = mt5.account_info()
    terminal = mt5.terminal_info()
    if account is None or terminal is None:
        raise RuntimeError(f"Unable to read MT5 account/terminal state: {mt5.last_error()}")

    account_data = account._asdict()
    terminal_data = terminal._asdict()
    private_trade_mode = _private_trade_mode(mt5, account_data)

    company = str(account_data.get("company") or "").strip()
    server = str(account_data.get("server") or "").strip()
    currency = str(account_data.get("currency") or "").strip().upper()
    login = account_data.get("login")
    if not company or not server or not currency or login is None:
        raise RuntimeError("MT5 account is missing company, server, currency or login metadata")

    instruments: dict[str, dict[str, Any]] = {}
    quotes: dict[str, dict[str, Any]] = {}
    for canonical, broker_symbol in symbols:
        if not mt5.symbol_select(broker_symbol, True):
            raise RuntimeError(f"Unable to select MT5 symbol {broker_symbol}: {mt5.last_error()}")
        info = mt5.symbol_info(broker_symbol)
        tick = mt5.symbol_info_tick(broker_symbol)
        if info is None or tick is None:
            raise RuntimeError(f"Unable to read MT5 symbol/tick {broker_symbol}: {mt5.last_error()}")
        symbol = info._asdict()
        tick_data = tick._asdict()
        tick_size = _finite_positive(
            symbol.get("trade_tick_size") or symbol.get("point"),
            f"{broker_symbol}.trade_tick_size",
        )
        tick_value_loss = _finite_positive(
            symbol.get("trade_tick_value_loss"),
            f"{broker_symbol}.trade_tick_value_loss",
        )
        instruments[broker_symbol] = {
            "symbol": broker_symbol,
            "canonical_symbol": canonical,
            "tick_size": tick_size,
            "point": _finite_positive(symbol.get("point"), f"{broker_symbol}.point"),
            "tick_value_loss": tick_value_loss,
            "tick_value_profit": float(symbol.get("trade_tick_value_profit") or tick_value_loss),
            "tick_value_currency": currency,
            "tick_value_source": "MT5_SYMBOL_INFO_TRADE_TICK_VALUE_LOSS",
            "tick_value_timestamp_ms": generated_at_ms,
            "valuation_model": "MT5_TRADE_TICK_VALUE_LOSS",
            "contract_size": _finite_positive(
                symbol.get("trade_contract_size"), f"{broker_symbol}.trade_contract_size"
            ),
            "volume_min": _finite_positive(symbol.get("volume_min"), f"{broker_symbol}.volume_min"),
            "volume_max": _finite_positive(symbol.get("volume_max"), f"{broker_symbol}.volume_max"),
            "volume_step": _finite_positive(
                symbol.get("volume_step"), f"{broker_symbol}.volume_step"
            ),
            "currency_profit": str(symbol.get("currency_profit") or "").upper(),
            "currency_margin": str(symbol.get("currency_margin") or "").upper(),
            "metadata_verified": True,
        }
        timestamp_ms = tick_data.get("time_msc")
        if timestamp_ms is None:
            timestamp_ms = int(tick_data.get("time") or 0) * 1000
        quotes[broker_symbol] = {
            "bid": float(tick_data.get("bid") or 0.0),
            "ask": float(tick_data.get("ask") or 0.0),
            "timestamp_ms": int(timestamp_ms),
        }

    connected = bool(terminal_data.get("connected", True))
    return {
        "schema_version": 1,
        "bridge_status": "CONNECTED" if connected else "DISCONNECTED",
        "generated_at_ms": generated_at_ms,
        "terminal": {
            "company": company,
            "server": server,
            "connected": connected,
            "platform": "MetaTrader 5",
        },
        "account": {
            "account_key": _account_key(server, login),
            "masked_account": _masked_login(login),
            "provider": company,
            "server": server,
            "connected": connected,
            "trade_mode": private_trade_mode,
            "currency": currency,
            "balance": float(account_data.get("balance") or 0.0),
            "equity": float(account_data.get("equity") or 0.0),
            "used_margin": float(account_data.get("margin") or 0.0),
            "free_margin": float(account_data.get("margin_free") or 0.0),
        },
        "instruments": instruments,
        "quotes": quotes,
        "read_only": True,
        "order_submission_enabled": False,
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a Londres MT5 read-only snapshot")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--symbol", action="append", required=True, type=_parse_symbol)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_ms <= 0:
        parser.error("--interval-ms must be > 0")

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit(
            "MetaTrader5 Python package is required on the machine hosting the MT5 terminal"
        ) from exc

    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        while True:
            _atomic_write(args.output.expanduser(), _snapshot(mt5, list(args.symbol)))
            if args.once:
                break
            time.sleep(args.interval_ms / 1000.0)
    except KeyboardInterrupt:
        return 0
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
