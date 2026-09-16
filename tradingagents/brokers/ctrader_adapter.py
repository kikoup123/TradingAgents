"""Universal broker-contract wrapper for the Phase 18 cTrader connector."""

from __future__ import annotations

from .contracts import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerType,
    canonicalize_symbol,
)
from .ctrader import CTraderReadOnlyConnector


class CTraderUniversalReadOnlyAdapter:
    """Expose cTrader data through the broker-agnostic Phase 19 contract.

    cTrader platform capabilities are described separately from connection
    permissions. This adapter remains read-only and has no order submission
    method. Tick value in account currency is intentionally unresolved until the
    broker-native conversion phase is implemented.
    """

    def __init__(
        self,
        connector: CTraderReadOnlyConnector,
        *,
        adapter_id: str = "ctrader-readonly",
    ) -> None:
        self._connector = connector
        self._adapter_id = adapter_id

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.CTRADER

    def public_status(self) -> dict:
        status = self._connector.public_status()
        return {
            "adapter_id": self.adapter_id,
            "broker_type": self.broker_type.value,
            "provider": status.get("provider"),
            "broker": status.get("broker"),
            "status": status.get("status"),
            "account": status.get("account"),
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            supports_market_orders=True,
            supports_limit_orders=True,
            supports_stop_orders=True,
            supports_server_side_sl=True,
            supports_server_side_tp=True,
            supports_stop_amendment=True,
            supports_partial_close=True,
            supports_native_oco=False,
            supports_streaming_quotes=True,
            supports_historical_bars=True,
            execution_enabled=False,
        )

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        if self._connector.public_status().get("status") != "CONNECTED":
            self._connector.connect()
        snapshot = self._connector.account_snapshot()
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=self.broker_type,
            broker_name=snapshot.get("broker"),
            masked_account=str(snapshot.get("masked_account") or "••••"),
            connected=snapshot.get("status") == "CONNECTED",
            currency=str(snapshot.get("currency") or "UNKNOWN"),
            balance=float(snapshot.get("balance") or 0.0),
            equity=float(snapshot.get("equity") or 0.0),
            used_margin=float(snapshot.get("used_margin") or 0.0),
            free_margin=float(snapshot.get("free_margin") or 0.0),
        )

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec:
        del account_alias
        snapshot = self._connector.symbol_snapshot(broker_symbol)
        return BrokerInstrumentSpec(
            canonical_symbol=canonicalize_symbol(canonical_symbol),
            broker_symbol=str(snapshot["symbol"]),
            tick_size=float(snapshot["display_tick_size"]),
            tick_value_account_currency=None,
            volume_step=float(snapshot["step_volume_units"]),
            min_volume=float(snapshot["min_volume_units"]),
            max_volume=float(snapshot["max_volume_units"]),
            volume_unit="units",
            pip_size=float(snapshot["pip_size"]),
            minimum_stop_distance=None,
            minimum_target_distance=None,
            metadata_verified=True,
        )

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote:
        del account_alias
        snapshot = self._connector.quote_snapshot(broker_symbol)
        return BrokerQuote(
            canonical_symbol=canonicalize_symbol(canonical_symbol),
            broker_symbol=str(snapshot["symbol"]),
            bid=(float(snapshot["bid"]) if snapshot.get("bid") is not None else None),
            ask=(float(snapshot["ask"]) if snapshot.get("ask") is not None else None),
            timestamp_ms=(
                int(snapshot["timestamp_ms"]) if snapshot.get("timestamp_ms") is not None else None
            ),
        )
