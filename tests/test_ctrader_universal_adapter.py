from __future__ import annotations

from tradingagents.brokers import (
    BrokerType,
    CTraderUniversalReadOnlyAdapter,
)


class FakeConnector:
    def __init__(self) -> None:
        self.connected = False

    def public_status(self) -> dict:
        return {
            "provider": "cTrader Open API",
            "broker": "FP Markets",
            "status": "CONNECTED" if self.connected else "DISCONNECTED",
            "account": "••••7788",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def connect(self) -> dict:
        self.connected = True
        return self.public_status()

    def account_snapshot(self) -> dict:
        return {
            "broker": "FP Markets",
            "masked_account": "••••7788",
            "currency": "USD",
            "balance": 10_000.0,
            "equity": 10_050.0,
            "used_margin": 500.0,
            "free_margin": 9_550.0,
            "status": "CONNECTED",
            "account_environment": "HIDDEN_INTERNAL",
        }

    def symbol_snapshot(self, symbol: str) -> dict:
        return {
            "symbol_id": 42,
            "symbol": symbol,
            "digits": 2,
            "pip_position": 1,
            "min_volume_protocol": 100,
            "max_volume_protocol": 1_000_000,
            "step_volume_protocol": 100,
            "sl_distance": 10,
            "tp_distance": 10,
            "distance_set_in": "SYMBOL_DISTANCE_IN_POINTS",
        }

    def quote_snapshot(self, symbol: str) -> dict:
        return {
            "symbol_id": 42,
            "symbol": symbol,
            "bid": 21900.0,
            "ask": 21900.5,
            "timestamp_ms": 1_700_000_000_000,
        }


def test_ctrader_adapter_exposes_universal_sanitized_contract() -> None:
    adapter = CTraderUniversalReadOnlyAdapter(FakeConnector(), adapter_id="fp-1")
    account = adapter.account_snapshot("primary")
    instrument = adapter.instrument_snapshot(
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="US100",
    )
    quote = adapter.quote_snapshot(
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="US100",
    )

    assert adapter.broker_type == BrokerType.CTRADER
    assert account.masked_account == "••••7788"
    assert account.equity == 10_050.0
    assert instrument.tick_size == 0.01
    assert instrument.pip_size == 0.1
    assert instrument.min_volume == 1.0
    assert instrument.step_volume == 1.0
    assert instrument.max_volume == 10_000.0
    assert instrument.tick_value_account_currency is None
    assert instrument.risk_metadata_ready is False
    assert quote.bid == 21900.0
    assert quote.ask == 21900.5


def test_ctrader_universal_adapter_remains_read_only() -> None:
    adapter = CTraderUniversalReadOnlyAdapter(FakeConnector())
    capabilities = adapter.capabilities()
    status = adapter.public_status()
    assert capabilities.execution_enabled is False
    assert status["order_submission_enabled"] is False
    assert status["account_environment"] == "HIDDEN_INTERNAL"
    assert not hasattr(adapter, "place_order")
