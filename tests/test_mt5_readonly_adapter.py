from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tradingagents.brokers.mt5 import (
    MT5BridgeError,
    MT5UniversalReadOnlyAdapter,
)

NOW_MS = 1_800_000_000_000


class MemoryBridge:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def public_status(self) -> dict:
        return {
            "status": self.payload.get("bridge_status", "DISCONNECTED"),
            "provider": "MetaTrader 5 test bridge",
            "broker": self.payload.get("terminal", {}).get("company"),
            "server": self.payload.get("terminal", {}).get("server"),
            "read_only": True,
            "order_submission_enabled": False,
        }

    def snapshot(self) -> dict:
        return self.payload

    def reconnect(self) -> dict:
        return self.public_status()


def _payload() -> dict:
    return {
        "schema_version": 1,
        "bridge_status": "CONNECTED",
        "generated_at_ms": NOW_MS - 100,
        "terminal": {
            "company": "Vantage Global Prime",
            "server": "Vantage-Live",
            "connected": True,
        },
        "account": {
            "account_key": "hashed-private-account-key",
            "masked_account": "••••4321",
            "provider": "Vantage Global Prime",
            "server": "Vantage-Live",
            "connected": True,
            "trade_mode": "REAL",
            "currency": "USD",
            "balance": 10_000.0,
            "equity": 10_000.0,
            "used_margin": 100.0,
            "free_margin": 9_900.0,
        },
        "instruments": {
            "NAS100": {
                "symbol": "NAS100",
                "canonical_symbol": "NASDAQ",
                "tick_size": 0.1,
                "point": 0.1,
                "tick_value_loss": 0.1,
                "tick_value_currency": "USD",
                "tick_value_source": "MT5_SYMBOL_INFO_TRADE_TICK_VALUE_LOSS",
                "tick_value_timestamp_ms": NOW_MS - 500,
                "valuation_model": "MT5_TRADE_TICK_VALUE_LOSS",
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
                "metadata_verified": True,
            }
        },
        "quotes": {
            "NAS100": {
                "bid": 24_999.9,
                "ask": 25_000.0,
                "timestamp_ms": NOW_MS - 1_000,
            }
        },
    }


def test_mt5_adapter_exposes_sanitized_vantage_live_account() -> None:
    adapter = MT5UniversalReadOnlyAdapter(MemoryBridge(_payload()))
    discovered = adapter.discover_accounts()[0]
    account = adapter.account_snapshot(discovered.account_alias)
    instrument = adapter.instrument_snapshot(
        account_alias=discovered.account_alias,
        canonical_symbol="NASDAQ",
        broker_symbol="NAS100",
    )
    quote = adapter.quote_snapshot(
        account_alias=discovered.account_alias,
        canonical_symbol="NASDAQ",
        broker_symbol="NAS100",
    )

    assert discovered.account_alias.startswith("MT5-")
    assert "hashed-private" not in discovered.account_alias
    assert account.masked_account == "••••4321"
    assert account.broker_name == "Vantage Global Prime"
    assert account.equity == 10_000.0
    assert instrument.tick_size == 0.1
    assert instrument.tick_value_account_currency == 0.1
    assert instrument.tick_value_currency == "USD"
    assert instrument.volume_unit == "lots"
    assert quote.bid == 24_999.9
    assert quote.ask == 25_000.0
    assert adapter.capabilities().execution_enabled is False
    assert adapter.public_status()["order_submission_enabled"] is False
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "submit_order")


def test_mt5_adapter_rejects_non_live_account() -> None:
    payload = deepcopy(_payload())
    payload["account"]["trade_mode"] = "DEMO"
    with pytest.raises(MT5BridgeError, match="live brokerage accounts only"):
        MT5UniversalReadOnlyAdapter(MemoryBridge(payload))


def test_mt5_adapter_requires_tick_value_in_account_currency() -> None:
    payload = deepcopy(_payload())
    payload["instruments"]["NAS100"]["tick_value_currency"] = "EUR"
    adapter = MT5UniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = adapter.discover_accounts()[0].account_alias
    with pytest.raises(MT5BridgeError, match="account currency"):
        adapter.instrument_snapshot(
            account_alias=alias,
            canonical_symbol="NASDAQ",
            broker_symbol="NAS100",
        )


def test_mt5_adapter_requires_explicit_canonical_symbol_match() -> None:
    payload = deepcopy(_payload())
    payload["instruments"]["NAS100"]["canonical_symbol"] = "SP500"
    adapter = MT5UniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = adapter.discover_accounts()[0].account_alias
    with pytest.raises(MT5BridgeError, match="canonical symbol"):
        adapter.instrument_snapshot(
            account_alias=alias,
            canonical_symbol="NASDAQ",
            broker_symbol="NAS100",
        )


def test_mt5_bridge_script_contains_no_order_submission_surface() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "mt5_readonly_bridge.py").read_text(
        encoding="utf-8"
    )
    assert "order_send(" not in source
    assert "positions_get(" not in source
    assert "orders_send" not in source
    assert '"order_submission_enabled": False' in source
