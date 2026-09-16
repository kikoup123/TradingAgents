"""NinjaTrader 8 read-only bridge and universal Londres broker adapter.

Phase 24 deliberately uses a local read-only bridge contract instead of giving
LLM-facing code direct access to NinjaTrader credentials or order APIs. A small
Windows/NinjaTrader-side producer can atomically publish the documented JSON
snapshot; this Python adapter only reads it.

No order-submission, order-amendment or position-close method exists here.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .account_risk import AccountClassification
from .contracts import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerType,
    canonicalize_symbol,
)
from .ninjatrader_futures import (
    NINJATRADER_EQUITY_INDEX_FUTURES,
    FuturesContractResolution,
    NinjaTraderFuturesContractResolver,
    parse_ninjatrader_contract_symbol,
)


class NinjaTraderBridgeError(RuntimeError):
    """Raised when the local NinjaTrader read-only bridge cannot be trusted."""


class NinjaTraderReadOnlyBridge(Protocol):
    def public_status(self) -> dict[str, Any]: ...

    def snapshot(self) -> Mapping[str, Any]: ...

    def reconnect(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class NinjaTraderDiscoveredAccount:
    account_alias: str
    masked_account: str
    provider: str | None
    connected: bool
    currency: str
    detected_classification: AccountClassification | None
    classification_metadata_verified: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["detected_classification"] = (
            self.detected_classification.value if self.detected_classification is not None else None
        )
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["read_only"] = True
        return payload


class NinjaTraderJsonBridgeTransport:
    """Read an atomically published NinjaTrader snapshot from local JSON.

    The transport never writes to NinjaTrader and contains no execution API. A
    bridge producer should write a temporary file and atomically replace the
    configured path so readers never observe half-written JSON.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._snapshot: dict[str, Any] | None = None
        self._load()

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            self._snapshot = None
            raise NinjaTraderBridgeError("NinjaTrader bridge snapshot is unavailable") from exc
        if not isinstance(payload, dict):
            self._snapshot = None
            raise NinjaTraderBridgeError("NinjaTrader bridge snapshot must be a JSON object")
        if payload.get("schema_version") != 1:
            self._snapshot = None
            raise NinjaTraderBridgeError("Unsupported NinjaTrader bridge schema version")
        if not isinstance(payload.get("accounts"), list):
            self._snapshot = None
            raise NinjaTraderBridgeError("NinjaTrader bridge accounts must be a list")
        if not isinstance(payload.get("instruments"), dict):
            self._snapshot = None
            raise NinjaTraderBridgeError("NinjaTrader bridge instruments must be an object")
        if not isinstance(payload.get("quotes"), dict):
            self._snapshot = None
            raise NinjaTraderBridgeError("NinjaTrader bridge quotes must be an object")
        if not isinstance(payload.get("rollovers"), dict):
            self._snapshot = None
            raise NinjaTraderBridgeError("NinjaTrader bridge rollovers must be an object")
        self._snapshot = payload
        return payload

    def public_status(self) -> dict[str, Any]:
        payload = self._snapshot
        if payload is None:
            return {
                "status": "DISCONNECTED",
                "provider": "NinjaTrader 8 local read-only bridge",
                "read_only": True,
                "order_submission_enabled": False,
            }
        return {
            "status": str(payload.get("bridge_status") or "UNKNOWN").upper(),
            "provider": "NinjaTrader 8 local read-only bridge",
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


class NinjaTraderUniversalReadOnlyAdapter:
    """Expose one NinjaTrader installation through the universal broker contract."""

    def __init__(
        self,
        bridge: NinjaTraderReadOnlyBridge,
        *,
        adapter_id: str = "ninjatrader-readonly",
    ) -> None:
        self._bridge = bridge
        self._adapter_id = adapter_id
        self._resolver = NinjaTraderFuturesContractResolver()
        self._alias_to_key: dict[str, str] = {}
        self._account_rows: dict[str, Mapping[str, Any]] = {}
        self.refresh_accounts()

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.NINJATRADER

    def public_status(self) -> dict[str, Any]:
        status = self._bridge.public_status()
        return {
            "adapter_id": self.adapter_id,
            "broker_type": self.broker_type.value,
            "provider": status.get("provider"),
            "status": str(status.get("status") or "UNKNOWN").upper(),
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def reconnect(self) -> dict[str, Any]:
        status = self._bridge.reconnect()
        self.refresh_accounts()
        return {
            "adapter_id": self.adapter_id,
            "broker_type": self.broker_type.value,
            "status": str(status.get("status") or "UNKNOWN").upper(),
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
            supports_native_oco=True,
            supports_streaming_quotes=True,
            supports_historical_bars=True,
            execution_enabled=False,
        )

    def refresh_accounts(self) -> tuple[NinjaTraderDiscoveredAccount, ...]:
        payload = self._bridge.snapshot()
        rows = payload.get("accounts")
        if not isinstance(rows, list):
            raise NinjaTraderBridgeError("NinjaTrader accounts payload is invalid")

        alias_to_key: dict[str, str] = {}
        account_rows: dict[str, Mapping[str, Any]] = {}
        discovered: list[NinjaTraderDiscoveredAccount] = []
        for row in rows:
            if not isinstance(row, dict):
                raise NinjaTraderBridgeError("NinjaTrader account row must be an object")
            key = str(row.get("account_key") or "").strip()
            if not key:
                raise NinjaTraderBridgeError("NinjaTrader account_key is required")
            alias = self._public_alias(key)
            if alias in alias_to_key:
                raise NinjaTraderBridgeError("NinjaTrader account alias collision")
            masked = str(row.get("masked_account") or "").strip()
            if not masked:
                raise NinjaTraderBridgeError("NinjaTrader bridge must provide masked_account")
            currency = str(row.get("currency") or "").strip().upper()
            if not currency:
                raise NinjaTraderBridgeError("NinjaTrader account currency is required")
            detected, verified = self._detected_classification(row)
            alias_to_key[alias] = key
            account_rows[alias] = row
            discovered.append(
                NinjaTraderDiscoveredAccount(
                    account_alias=alias,
                    masked_account=masked,
                    provider=(str(row.get("provider")).strip() if row.get("provider") else None),
                    connected=bool(row.get("connected")),
                    currency=currency,
                    detected_classification=detected,
                    classification_metadata_verified=verified,
                )
            )

        self._alias_to_key = alias_to_key
        self._account_rows = account_rows
        return tuple(discovered)

    def discover_accounts(self) -> tuple[NinjaTraderDiscoveredAccount, ...]:
        return self.refresh_accounts()

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        row = self._account_row(account_alias)
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=self.broker_type,
            broker_name=(str(row.get("provider")).strip() if row.get("provider") else None),
            masked_account=str(row["masked_account"]),
            connected=bool(row.get("connected")),
            currency=str(row["currency"]).upper(),
            balance=self._finite_number(row, "balance"),
            equity=self._finite_number(row, "equity"),
            used_margin=self._finite_number(row, "used_margin", default=0.0),
            free_margin=self._finite_number(row, "free_margin", default=0.0),
        )

    def detected_classification(self, account_alias: str) -> AccountClassification | None:
        row = self._account_row(account_alias)
        detected, _ = self._detected_classification(row)
        return detected

    def resolve_active_contract(self, root: str) -> FuturesContractResolution:
        payload = self._bridge.snapshot()
        rollovers = payload.get("rollovers")
        instruments = payload.get("instruments")
        if not isinstance(rollovers, dict) or not isinstance(instruments, dict):
            raise NinjaTraderBridgeError("NinjaTrader rollover/instrument metadata unavailable")
        normalized_instruments = {str(key).upper(): value for key, value in instruments.items()}
        normalized_rollovers = {str(key).upper(): value for key, value in rollovers.items()}
        return self._resolver.resolve(
            root=root,
            rollovers=normalized_rollovers,
            instruments=normalized_instruments,
        )

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec:
        account = self.account_snapshot(account_alias)
        try:
            parsed = parse_ninjatrader_contract_symbol(broker_symbol)
        except ValueError as exc:
            raise NinjaTraderBridgeError("Exact verified NinjaTrader contract required") from exc
        exchange_spec = NINJATRADER_EQUITY_INDEX_FUTURES.get(parsed.root)
        if exchange_spec is None:
            raise NinjaTraderBridgeError("Unsupported NinjaTrader futures root")
        if canonicalize_symbol(canonical_symbol) != canonicalize_symbol(
            exchange_spec.canonical_symbol
        ):
            raise NinjaTraderBridgeError("Canonical symbol does not match NinjaTrader futures root")

        payload = self._bridge.snapshot()
        instruments = payload.get("instruments")
        if not isinstance(instruments, dict):
            raise NinjaTraderBridgeError("NinjaTrader instruments metadata unavailable")
        row = instruments.get(parsed.symbol) or instruments.get(broker_symbol)
        if not isinstance(row, dict) or row.get("metadata_verified") is not True:
            raise NinjaTraderBridgeError("Verified NinjaTrader contract metadata required")

        resolution = self.resolve_active_contract(parsed.root)
        if not resolution.ready or resolution.active_contract != parsed.symbol:
            raise NinjaTraderBridgeError("CONTRACT_ROLLOVER_UNVERIFIED")
        if account.currency != "USD":
            raise NinjaTraderBridgeError(
                "Phase 24 requires USD account currency for static futures tick values"
            )

        max_quantity = self._finite_number(row, "max_quantity")
        if max_quantity < 1 or not math.isclose(max_quantity, round(max_quantity), abs_tol=1e-9):
            raise NinjaTraderBridgeError("Verified integer max_quantity is required")

        return BrokerInstrumentSpec(
            canonical_symbol=canonicalize_symbol(canonical_symbol),
            broker_symbol=parsed.symbol,
            tick_size=exchange_spec.tick_size,
            tick_value_account_currency=exchange_spec.tick_value_usd,
            tick_value_currency="USD",
            tick_value_source=exchange_spec.source,
            tick_value_timestamp_ms=None,
            valuation_model="EXCHANGE_DEFINED_STATIC_FUTURES_TICK_VALUE",
            volume_step=1.0,
            min_volume=1.0,
            max_volume=float(int(max_quantity)),
            volume_unit="contracts",
            pip_size=None,
            minimum_stop_distance=None,
            minimum_target_distance=None,
            metadata_verified=True,
        )

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote:
        self._account_row(account_alias)
        payload = self._bridge.snapshot()
        quotes = payload.get("quotes")
        if not isinstance(quotes, dict):
            raise NinjaTraderBridgeError("NinjaTrader quote map unavailable")
        row = quotes.get(str(broker_symbol).upper()) or quotes.get(str(broker_symbol))
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
            bid=(float(bid) if bid is not None else None),
            ask=(float(ask) if ask is not None else None),
            timestamp_ms=(int(timestamp) if timestamp is not None else None),
        )

    def bridge_metadata(self) -> dict[str, Any]:
        payload = self._bridge.snapshot()
        return {
            "schema_version": payload.get("schema_version"),
            "generated_at_ms": payload.get("generated_at_ms"),
            "bridge_status": payload.get("bridge_status"),
            "account_count": len(payload.get("accounts") or []),
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def _account_row(self, account_alias: str) -> Mapping[str, Any]:
        row = self._account_rows.get(account_alias)
        if row is None:
            self.refresh_accounts()
            row = self._account_rows.get(account_alias)
        if row is None:
            raise NinjaTraderBridgeError("Unknown NinjaTrader account alias")
        return row

    @staticmethod
    def _public_alias(account_key: str) -> str:
        digest = hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:8].upper()
        return f"NT-{digest}"

    @staticmethod
    def _detected_classification(
        row: Mapping[str, Any],
    ) -> tuple[AccountClassification | None, bool]:
        if row.get("provider_classification_verified") is not True:
            return None, False
        raw = str(row.get("provider_classification") or "").strip().upper()
        if raw == AccountClassification.PERSONAL.value:
            return AccountClassification.PERSONAL, True
        if raw == AccountClassification.PROP_FIRM.value:
            return AccountClassification.PROP_FIRM, True
        return None, False

    @staticmethod
    def _finite_number(
        row: Mapping[str, Any], name: str, *, default: float | None = None
    ) -> float:
        value = row.get(name, default)
        if value is None:
            raise NinjaTraderBridgeError(f"NinjaTrader account field {name} is required")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise NinjaTraderBridgeError(f"NinjaTrader field {name} must be numeric") from exc
        if not math.isfinite(number):
            raise NinjaTraderBridgeError(f"NinjaTrader field {name} must be finite")
        return number
