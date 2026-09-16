"""Broker-agnostic contracts for Londres multi-broker orchestration.

These models deliberately separate strategy intent from broker implementation.
Credentials, full account identifiers and demo/live classification belong inside
individual adapters and are never represented by the public contracts below.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Protocol


class BrokerType(str, Enum):
    CTRADER = "CTRADER"
    NINJATRADER = "NINJATRADER"
    MT5 = "MT5"
    INTERACTIVE_BROKERS = "INTERACTIVE_BROKERS"
    TRADOVATE = "TRADOVATE"
    CUSTOM = "CUSTOM"


class OrchestrationPolicy(str, Enum):
    BEST_EFFORT = "BEST_EFFORT"
    ALL_OR_NONE = "ALL_OR_NONE"


class CanonicalSymbol(str, Enum):
    GOLD = "GOLD"
    NASDAQ = "NASDAQ"
    SP500 = "SP500"
    DOW = "DOW"
    DXY = "DXY"
    EURUSD = "EURUSD"
    GBPUSD = "GBPUSD"
    BTC = "BTC"
    CRUDE = "CRUDE"


_CANONICAL_ALIASES = {
    "XAUUSD": CanonicalSymbol.GOLD.value,
    "GOLD": CanonicalSymbol.GOLD.value,
    "GC": CanonicalSymbol.GOLD.value,
    "NASDAQ": CanonicalSymbol.NASDAQ.value,
    "NAS100": CanonicalSymbol.NASDAQ.value,
    "US100": CanonicalSymbol.NASDAQ.value,
    "NQ": CanonicalSymbol.NASDAQ.value,
    "SP500": CanonicalSymbol.SP500.value,
    "US500": CanonicalSymbol.SP500.value,
    "SPX": CanonicalSymbol.SP500.value,
    "ES": CanonicalSymbol.SP500.value,
    "DOW": CanonicalSymbol.DOW.value,
    "US30": CanonicalSymbol.DOW.value,
    "YM": CanonicalSymbol.DOW.value,
    "DXY": CanonicalSymbol.DXY.value,
    "EURUSD": CanonicalSymbol.EURUSD.value,
    "GBPUSD": CanonicalSymbol.GBPUSD.value,
    "BTC": CanonicalSymbol.BTC.value,
    "BTCUSD": CanonicalSymbol.BTC.value,
    "CRUDE": CanonicalSymbol.CRUDE.value,
    "WTI": CanonicalSymbol.CRUDE.value,
    "CL": CanonicalSymbol.CRUDE.value,
}


def canonicalize_symbol(symbol: str) -> str:
    normalized = "".join(character for character in symbol.upper() if character.isalnum())
    return _CANONICAL_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class BrokerCapabilities:
    supports_market_orders: bool
    supports_limit_orders: bool
    supports_stop_orders: bool
    supports_server_side_sl: bool
    supports_server_side_tp: bool
    supports_stop_amendment: bool
    supports_partial_close: bool
    supports_native_oco: bool
    supports_streaming_quotes: bool
    supports_historical_bars: bool
    execution_enabled: bool = False

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["broker_order_submission_enabled"] = bool(self.execution_enabled)
        return payload


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    account_alias: str
    broker_type: BrokerType
    broker_name: str | None
    masked_account: str
    connected: bool
    currency: str
    balance: float
    equity: float
    used_margin: float
    free_margin: float

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if not self.masked_account.strip():
            raise ValueError("masked_account is required")
        if self.connected and self.equity <= 0:
            raise ValueError("connected account equity must be > 0")

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["broker_type"] = self.broker_type.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


@dataclass(frozen=True)
class BrokerInstrumentSpec:
    canonical_symbol: str
    broker_symbol: str
    tick_size: float
    volume_step: float
    min_volume: float
    max_volume: float
    volume_unit: str
    tick_value_account_currency: float | None = None
    pip_size: float | None = None
    minimum_stop_distance: float | None = None
    minimum_target_distance: float | None = None
    metadata_verified: bool = True

    def __post_init__(self) -> None:
        for name, value in {
            "tick_size": self.tick_size,
            "volume_step": self.volume_step,
            "min_volume": self.min_volume,
            "max_volume": self.max_volume,
        }.items():
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.min_volume > self.max_volume:
            raise ValueError("min_volume cannot exceed max_volume")
        if self.tick_value_account_currency is not None and self.tick_value_account_currency <= 0:
            raise ValueError("tick_value_account_currency must be > 0 when supplied")
        if self.pip_size is not None and self.pip_size <= 0:
            raise ValueError("pip_size must be > 0 when supplied")

    @property
    def risk_metadata_ready(self) -> bool:
        return bool(self.metadata_verified and self.tick_value_account_currency is not None)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerQuote:
    canonical_symbol: str
    broker_symbol: str
    bid: float | None
    ask: float | None
    timestamp_ms: int | None


@dataclass(frozen=True)
class TradeIntent:
    """One Londres setup expressed independently from any broker account."""

    trade_id: str
    canonical_symbol: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    selected_exit_mode: str
    structural_break_even: bool = True
    partial_fraction: float | None = None
    partial_trigger_price: float | None = None
    runner_fraction: float | None = None
    runner_target_price: float | None = None
    execution_style: str = "MARKET_ON_SIGNAL"

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("trade_id is required")
        if self.direction not in {"BULLISH", "BEARISH"}:
            raise ValueError("direction must be BULLISH or BEARISH")
        if self.entry_price <= 0 or self.stop_price <= 0 or self.target_price <= 0:
            raise ValueError("entry, stop and target prices must be > 0")
        if self.direction == "BULLISH":
            if not self.stop_price < self.entry_price < self.target_price:
                raise ValueError("bullish intent requires stop < entry < target")
        elif not self.target_price < self.entry_price < self.stop_price:
            raise ValueError("bearish intent requires target < entry < stop")
        hold = self.selected_exit_mode == "HOLD_HTF_LIQUIDITY"
        fractions = (self.partial_fraction, self.runner_fraction)
        if hold:
            if any(value is None for value in fractions):
                raise ValueError("hold mode requires partial_fraction and runner_fraction")
            if not math.isclose(float(self.partial_fraction), 0.60, abs_tol=1e-12):
                raise ValueError("hold mode requires exact 60% partial fraction")
            if not math.isclose(float(self.runner_fraction), 0.40, abs_tol=1e-12):
                raise ValueError("hold mode requires exact 40% runner fraction")
            if self.partial_trigger_price is None or self.runner_target_price is None:
                raise ValueError("hold mode requires partial and runner target prices")

    @property
    def canonical(self) -> str:
        return canonicalize_symbol(self.canonical_symbol)


class BrokerAdapter(Protocol):
    """Read/normalize contract implemented by each broker integration.

    No order-submission method is part of Phase 19. Future execution adapters
    will consume already-validated account-specific plans in a separate layer.
    """

    @property
    def adapter_id(self) -> str: ...

    @property
    def broker_type(self) -> BrokerType: ...

    def public_status(self) -> dict[str, Any]: ...

    def capabilities(self) -> BrokerCapabilities: ...

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot: ...

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec: ...

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote: ...
