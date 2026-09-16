"""MetaTrader 5 read-only bridge and universal Londres broker adapter.

The bridge is deliberately account-data-only. It consumes a sanitized snapshot
published by a local MT5 reader and exposes account, symbol and tick data through
the same broker-agnostic contracts used by cTrader and NinjaTrader. No order
submission, modification, cancellation or position-closing API is implemented.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerType,
    canonicalize_symbol,
)


class MT5BridgeError(RuntimeError):
    """Raised when the local MT5 read-only snapshot cannot be trusted."""


class MT5ReadOnlyBridge(Protocol):
    def public_status(self) -> dict[str, Any]: ...
    def snapshot(self) -> Mapping[str, Any]: ...
    def reconnect(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MT5DiscoveredAccount:
    account_alias: str
    masked_account: str
    provider: str | None
    server: str | None
    connected: bool
    currency: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["account_scope"] = "LIVE_BROKERAGE_ACCOUNTS_ONLY"
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["read_only"] = True
        payload["order_submission_enabled"] = False
        return payload


class MT5JsonBridgeTransport:
    """Read an atomically published MT5 snapshot from local JSON."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._snapshot: dict[str, Any] | None = None
        self._load()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._snapshot = None
            raise MT5BridgeError("MT5 bridge snapshot is unavailable") from exc
        if not isinstance(payload, dict):
            self._snapshot = None
            raise MT5BridgeError("MT5 bridge snapshot must be a JSON object")
        if payload.get("schema_version") != 1:
            self._snapshot = None
            raise MT5BridgeError("Unsupported MT5 bridge schema version")
        for key, expected in (
            ("terminal", dict),
            ("account", dict),
            ("instruments", dict),
            ("quotes", dict),
        ):
            if not isinstance(payload.get(key), expected):
                self._snapshot = None
                raise MT5BridgeError(f"MT5 bridge {key} has invalid type")
        self._snapshot = payload
        return payload

    def public_status(self) -> dict[str, Any]:
        payload = self._snapshot
        if payload is None:
            return {
                "status": "DISCONNECTED",
                "provider": "MetaTrader 5 local read-only bridge",
                "read_only": True,
                "order_submission_enabled": False,
            }
        terminal = payload.get("terminal") or {}
        return {
            "status": str(payload.get("bridge_status") or "UNKNOWN").upper(),
            "provider": "MetaTrader 5 local read-only bridge",
            "broker": terminal.get("company"),
            "server": terminal.get("server"),
            "read_only": True,
            "order_submission_enabled": False,
        }

    def snapshot(self) -> Mapping[str, Any]:
        if self._snapshot is None:
            return self._load()
        return self._snapshot

    def reconnect(self) -> dict[str, Any]:
        self._load()
        return self.public_status()


class MT5UniversalReadOnlyAdapter:
    """Expose one already-connected MT5 terminal through universal contracts."""

    def __init__(
        self,
        bridge: MT5ReadOnlyBridge,
        *,
        adapter_id: str = "mt5-readonly",
    ) -> None:
        self._bridge = bridge
        self._adapter_id = adapter_id
        self._account_alias: str | None = None
        self._account_row: Mapping[str, Any] | None = None
        self.refresh_account()

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.MT5

    def public_status(self) -> dict[str, Any]:
        status = self._bridge.public_status()
        return {
            "adapter_id": self.adapter_id,
            "broker_type": self.broker_type.value,
            "provider": status.get("provider"),
            "broker": status.get("broker"),
            "server": status.get("server"),
            "status": str(status.get("status") or "UNKNOWN").upper(),
            "account_scope": "LIVE_BROKERAGE_ACCOUNTS_ONLY",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def reconnect(self) -> dict[str, Any]:
        status = self._bridge.reconnect()
        self.refresh_account()
        return {
            **self.public_status(),
            "status": str(status.get("status") or "UNKNOWN").upper(),
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

    def refresh_account(self) -> MT5DiscoveredAccount:
        payload = self._bridge.snapshot()
        row = payload.get("account")
        terminal = payload.get("terminal")
        if not isinstance(row, dict) or not isinstance(terminal, dict):
            raise MT5BridgeError("MT5 account/terminal payload is invalid")
        if str(row.get("trade_mode") or "").upper() != "REAL":
            raise MT5BridgeError("Londres MT5 adapter accepts live brokerage accounts only")
        key = str(row.get("account_key") or "").strip()
        masked = str(row.get("masked_account") or "").strip()
        currency = str(row.get("currency") or "").strip().upper()
        if not key or not masked or not currency:
            raise MT5BridgeError("MT5 account_key, masked_account and currency are required")
        alias = self._public_alias(key)
        self._account_alias = alias
        self._account_row = row
        provider = str(row.get("provider") or terminal.get("company") or "").strip() or None
        server = str(row.get("server") or terminal.get("server") or "").strip() or None
        return MT5DiscoveredAccount(
            account_alias=alias,
            masked_account=masked,
            provider=provider,
            server=server,
            connected=bool(row.get("connected")),
            currency=currency,
        )

    def discover_accounts(self) -> tuple[MT5DiscoveredAccount, ...]:
        return (self.refresh_account(),)

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        row = self._require_account(account_alias)
        terminal = self._bridge.snapshot().get("terminal") or {}
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=self.broker_type,
            broker_name=str(row.get("provider") or terminal.get("company") or "").strip() or None,
            masked_account=str(row["masked_account"]),
            connected=bool(row.get("connected")),
            currency=str(row["currency"]).upper(),
            balance=self._finite_number(row, "balance"),
            equity=self._finite_number(row, "equity"),
            used_margin=self._finite_number(row, "used_margin", default=0.0),
            free_margin=self._finite_number(row, "free_margin", default=0.0),
        )

    def instrument_snapshot(
        self,
        *,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
    ) -> BrokerInstrumentSpec:
        account = self.account_snapshot(account_alias)
        instruments = self._bridge.snapshot().get("instruments")
        if not isinstance(instruments, dict):
            raise MT5BridgeError("MT5 instrument map unavailable")
        row = instruments.get(broker_symbol)
        if not isinstance(row, dict) or row.get("metadata_verified") is not True:
            raise MT5BridgeError("Verified MT5 symbol metadata required")
        row_canonical = canonicalize_symbol(str(row.get("canonical_symbol") or ""))
        expected_canonical = canonicalize_symbol(canonical_symbol)
        if row_canonical != expected_canonical:
            raise MT5BridgeError("MT5 canonical symbol does not match explicit mapping")
        tick_size = self._positive_number(row, "tick_size")
        tick_value = self._positive_number(row, "tick_value_loss")
        tick_currency = str(row.get("tick_value_currency") or "").strip().upper()
        if tick_currency != account.currency:
            raise MT5BridgeError("MT5 tick value must be verified in account currency")
        volume_min = self._positive_number(row, "volume_min")
        volume_max = self._positive_number(row, "volume_max")
        volume_step = self._positive_number(row, "volume_step")
        if volume_min > volume_max:
            raise MT5BridgeError("MT5 volume_min cannot exceed volume_max")
        tick_timestamp = row.get("tick_value_timestamp_ms")
        if tick_timestamp is None:
            raise MT5BridgeError("MT5 tick-value verification timestamp is required")
        return BrokerInstrumentSpec(
            canonical_symbol=expected_canonical,
            broker_symbol=str(row.get("symbol") or broker_symbol),
            tick_size=tick_size,
            tick_value_account_currency=tick_value,
            tick_value_currency=tick_currency,
            tick_value_source=str(row.get("tick_value_source") or "MT5_SYMBOL_INFO"),
            tick_value_timestamp_ms=int(tick_timestamp),
            valuation_model=str(row.get("valuation_model") or "MT5_TRADE_TICK_VALUE_LOSS"),
            volume_step=volume_step,
            min_volume=volume_min,
            max_volume=volume_max,
            volume_unit="lots",
            pip_size=(
                self._positive_number(row, "point") if row.get("point") is not None else None
            ),
            minimum_stop_distance=None,
            minimum_target_distance=None,
            metadata_verified=True,
        )

    def quote_snapshot(
        self,
        *,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
    ) -> BrokerQuote:
        self._require_account(account_alias)
        quotes = self._bridge.snapshot().get("quotes")
        if not isinstance(quotes, dict):
            raise MT5BridgeError("MT5 quote map unavailable")
        row = quotes.get(broker_symbol)
        if not isinstance(row, dict):
            return BrokerQuote(
                canonical_symbol=canonicalize_symbol(canonical_symbol),
                broker_symbol=broker_symbol,
                bid=None,
                ask=None,
                timestamp_ms=None,
            )
        bid = row.get("bid")
        ask = row.get("ask")
        timestamp = row.get("timestamp_ms")
        return BrokerQuote(
            canonical_symbol=canonicalize_symbol(canonical_symbol),
            broker_symbol=broker_symbol,
            bid=(self._finite_value(bid, "bid") if bid is not None else None),
            ask=(self._finite_value(ask, "ask") if ask is not None else None),
            timestamp_ms=(int(timestamp) if timestamp is not None else None),
        )

    def bridge_metadata(self) -> dict[str, Any]:
        payload = self._bridge.snapshot()
        return {
            "schema_version": payload.get("schema_version"),
            "generated_at_ms": payload.get("generated_at_ms"),
            "bridge_status": payload.get("bridge_status"),
            "account_count": 1,
            "account_scope": "LIVE_BROKERAGE_ACCOUNTS_ONLY",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def _require_account(self, account_alias: str) -> Mapping[str, Any]:
        if self._account_alias != account_alias or self._account_row is None:
            self.refresh_account()
        if self._account_alias != account_alias or self._account_row is None:
            raise MT5BridgeError("Unknown MT5 account alias")
        return self._account_row

    @staticmethod
    def _public_alias(account_key: str) -> str:
        digest = hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:8].upper()
        return f"MT5-{digest}"

    @classmethod
    def _finite_number(
        cls,
        row: Mapping[str, Any],
        name: str,
        *,
        default: float | None = None,
    ) -> float:
        value = row.get(name, default)
        if value is None:
            raise MT5BridgeError(f"MT5 field {name} is required")
        return cls._finite_value(value, name)

    @classmethod
    def _positive_number(cls, row: Mapping[str, Any], name: str) -> float:
        number = cls._finite_number(row, name)
        if number <= 0:
            raise MT5BridgeError(f"MT5 field {name} must be > 0")
        return number

    @staticmethod
    def _finite_value(value: Any, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MT5BridgeError(f"MT5 field {name} must be numeric") from exc
        if not math.isfinite(number):
            raise MT5BridgeError(f"MT5 field {name} must be finite")
        return number
