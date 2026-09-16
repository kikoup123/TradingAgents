"""Deterministic cTrader tick-value and FX-conversion helpers.

cTrader Open API exposes symbol geometry and asset conversion chains, but it
does not expose the cTrader Automate ``Symbol.TickValue`` property directly.
For linear cTrader volume units, one tick of P/L for one volume unit is one
``tick_size`` amount in the symbol quote asset.  This module converts that quote
asset amount into the account deposit currency using broker-supplied conversion
symbols and live bid/ask prices.

For risk sizing we deliberately use the adverse side of every conversion leg so
the cash-loss magnitude is not understated by spread.  No synthetic FX rate is
guessed and no stale/static contract multiplier is inserted.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CTraderConversionLeg:
    symbol_id: int
    symbol: str
    base_asset_id: int
    quote_asset_id: int
    bid: float
    ask: float
    timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        if self.base_asset_id == self.quote_asset_id:
            raise ValueError("conversion leg base and quote assets must differ")
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError("conversion bid/ask must be positive")
        if self.ask + 1e-15 < self.bid:
            raise ValueError("conversion ask cannot be below bid")


@dataclass(frozen=True)
class CTraderTickValueSnapshot:
    symbol: str
    account_currency: str
    tick_size: float
    tick_value_account_currency: float
    quote_asset_id: int
    deposit_asset_id: int
    conversion_rate: float
    conversion_symbols: tuple[str, ...]
    conversion_timestamp_ms: int | None
    valuation_model: str = "CTRADER_LINEAR_VOLUME_UNIT_CONSERVATIVE_LOSS_FX"
    verified: bool = True

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tick_value_source"] = "CTRADER_OPEN_API_CONVERSION_CHAIN"
        payload["manual_tick_value_allowed"] = False
        return payload


def conservative_loss_conversion_rate(
    *,
    first_asset_id: int,
    last_asset_id: int,
    legs: list[CTraderConversionLeg] | tuple[CTraderConversionLeg, ...],
) -> tuple[float, int | None]:
    """Return the adverse executable conversion rate for a loss magnitude.

    Moving from base -> quote uses ASK instead of BID so the converted loss is
    not understated. Moving quote -> base uses 1/BID instead of 1/ASK for the
    same reason. The chain must connect exactly from ``first_asset_id`` to
    ``last_asset_id``; any discontinuity fails closed.
    """

    if first_asset_id == last_asset_id:
        return 1.0, None
    if not legs:
        raise ValueError("conversion chain is required when assets differ")

    current = int(first_asset_id)
    rate = 1.0
    timestamps: list[int] = []
    for leg in legs:
        if leg.timestamp_ms is not None:
            timestamps.append(int(leg.timestamp_ms))
        if current == leg.base_asset_id:
            rate *= leg.ask
            current = leg.quote_asset_id
        elif current == leg.quote_asset_id:
            rate *= 1.0 / leg.bid
            current = leg.base_asset_id
        else:
            raise ValueError("conversion chain is not contiguous from the requested first asset")
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("conversion chain produced an invalid rate")

    if current != int(last_asset_id):
        raise ValueError("conversion chain did not terminate at the requested deposit asset")
    return rate, (max(timestamps) if timestamps else None)


def resolve_linear_tick_value(
    *,
    symbol: str,
    account_currency: str,
    tick_size: float,
    quote_asset_id: int,
    deposit_asset_id: int,
    conversion_legs: list[CTraderConversionLeg] | tuple[CTraderConversionLeg, ...] = (),
) -> CTraderTickValueSnapshot:
    """Resolve cash value of one tick for one cTrader volume unit."""

    if tick_size <= 0 or not math.isfinite(tick_size):
        raise ValueError("tick_size must be finite and > 0")
    currency = account_currency.strip().upper()
    if not currency:
        raise ValueError("account_currency is required")

    rate, timestamp_ms = conservative_loss_conversion_rate(
        first_asset_id=int(quote_asset_id),
        last_asset_id=int(deposit_asset_id),
        legs=conversion_legs,
    )
    tick_value = float(tick_size) * rate
    if tick_value <= 0 or not math.isfinite(tick_value):
        raise ValueError("resolved tick value must be finite and > 0")

    return CTraderTickValueSnapshot(
        symbol=symbol,
        account_currency=currency,
        tick_size=float(tick_size),
        tick_value_account_currency=tick_value,
        quote_asset_id=int(quote_asset_id),
        deposit_asset_id=int(deposit_asset_id),
        conversion_rate=rate,
        conversion_symbols=tuple(leg.symbol for leg in conversion_legs),
        conversion_timestamp_ms=timestamp_ms,
    )
