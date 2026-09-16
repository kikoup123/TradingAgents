from __future__ import annotations

from tradingagents.brokers import BrokerAccountSnapshot, BrokerCapabilities, BrokerInstrumentSpec, BrokerQuote, BrokerSymbolMap, BrokerType, TradeIntent
from tradingagents.ict import LondresPhase23LiveAccountRiskEngine, ManagedBrokerAccount


class Adapter:
    def __init__(self, alias: str, equity: float) -> None:
        self._alias = alias
        self._equity = equity

    @property
    def adapter_id(self) -> str:
        return self._alias

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.CUSTOM

    def public_status(self) -> dict:
        return {"status": "CONNECTED"}

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(supports_market_orders=True, supports_limit_orders=True, supports_stop_orders=True, supports_server_side_sl=True, supports_server_side_tp=True, supports_stop_amendment=True, supports_partial_close=True, supports_native_oco=True, supports_streaming_quotes=True, supports_historical_bars=True, execution_enabled=False)

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(account_alias=account_alias, broker_type=self.broker_type, broker_name="Live Broker", masked_account="••••1234", connected=True, currency="USD", balance=self._equity, equity=self._equity, used_margin=0.0, free_margin=self._equity)

    def instrument_snapshot(self, *, account_alias: str, canonical_symbol: str, broker_symbol: str):
        del account_alias, canonical_symbol
        return BrokerInstrumentSpec(canonical_symbol="NASDAQ", broker_symbol=broker_symbol, tick_size=1.0, tick_value_account_currency=1.0, volume_step=1.0, min_volume=1.0, max_volume=1000.0, volume_unit="contracts")

    def quote_snapshot(self, *, account_alias: str, canonical_symbol: str, broker_symbol: str):
        del account_alias
        return BrokerQuote(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, bid=100.0, ask=101.0, timestamp_ms=1)


def _managed(alias: str, equity: float) -> ManagedBrokerAccount:
    adapter = Adapter(alias, equity)
    return ManagedBrokerAccount(account_alias=alias, adapter=adapter, symbol_map=BrokerSymbolMap(BrokerType.CUSTOM, {"NASDAQ": "NAS"}), risk_fraction=0.03)


def test_phase23_uses_each_live_accounts_current_equity() -> None:
    intent = TradeIntent(trade_id="live-accounts", canonical_symbol="NASDAQ", direction="BEARISH", entry_price=100.0, stop_price=110.0, target_price=80.0, selected_exit_mode="FULL_AT_SD_2")
    result = LondresPhase23LiveAccountRiskEngine().prepare(intent=intent, accounts=(_managed("A", 10_000.0), _managed("B", 20_000.0)))
    assert result["status"] == "READY"
    assert result["risk_base_mode"] == "CURRENT_BROKER_ACCOUNT_EQUITY"
    assert result["account_scope"] == "LIVE_BROKERAGE_ACCOUNTS_ONLY"
    assert result["accounts"][0]["account_equity"] == 10_000.0
    assert result["accounts"][1]["account_equity"] == 20_000.0
    assert result["accounts"][0]["prepared_volume"] == 30.0
    assert result["accounts"][1]["prepared_volume"] == 60.0
    assert result["order_authorized"] is False
    assert result["broker_order_placed"] is False
