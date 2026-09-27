"""Verified futures metadata and fail-closed NinjaTrader rollover resolution.

Phase 24 supports only the equity-index futures roots explicitly listed here.
The contract economics are exchange-defined metadata, while the active contract
must still be supplied and verified by the local NinjaTrader bridge. Londres
never guesses the current quarterly contract and never silently switches a
standard contract to its Micro counterpart (or vice versa).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .contracts import canonicalize_symbol


class FuturesContractResolutionStatus(str, Enum):
    READY = "READY"
    ROOT_UNSUPPORTED = "ROOT_UNSUPPORTED"
    CONTRACT_ROLLOVER_UNVERIFIED = "CONTRACT_ROLLOVER_UNVERIFIED"
    CONTRACT_SYMBOL_INVALID = "CONTRACT_SYMBOL_INVALID"
    CONTRACT_ROOT_MISMATCH = "CONTRACT_ROOT_MISMATCH"
    CONTRACT_METADATA_UNAVAILABLE = "CONTRACT_METADATA_UNAVAILABLE"
    CONTRACT_METADATA_UNVERIFIED = "CONTRACT_METADATA_UNVERIFIED"
    CONTRACT_SPEC_MISMATCH = "CONTRACT_SPEC_MISMATCH"


@dataclass(frozen=True)
class ExchangeFuturesSpec:
    root: str
    canonical_symbol: str
    exchange: str
    tick_size: float
    point_value_usd: float
    tick_value_usd: float
    volume_step: float = 1.0
    min_volume: float = 1.0
    source: str = "CME_GROUP_VERIFIED_PRODUCT_SPEC"

    def __post_init__(self) -> None:
        if not self.root.strip():
            raise ValueError("root is required")
        for name, value in {
            "tick_size": self.tick_size,
            "point_value_usd": self.point_value_usd,
            "tick_value_usd": self.tick_value_usd,
            "volume_step": self.volume_step,
            "min_volume": self.min_volume,
        }.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        expected = self.tick_size * self.point_value_usd
        if not math.isclose(expected, self.tick_value_usd, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("tick_value_usd must equal tick_size * point_value_usd")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# CME Group product specifications used by Phase 24. Runtime bridge metadata
# must match these values exactly enough to pass verification; the registry is
# not used to infer the active contract month.
NINJATRADER_EQUITY_INDEX_FUTURES: dict[str, ExchangeFuturesSpec] = {
    "NQ": ExchangeFuturesSpec(
        root="NQ",
        canonical_symbol="NASDAQ",
        exchange="CME",
        tick_size=0.25,
        point_value_usd=20.0,
        tick_value_usd=5.0,
    ),
    "MNQ": ExchangeFuturesSpec(
        root="MNQ",
        canonical_symbol="NASDAQ",
        exchange="CME",
        tick_size=0.25,
        point_value_usd=2.0,
        tick_value_usd=0.50,
    ),
    "ES": ExchangeFuturesSpec(
        root="ES",
        canonical_symbol="SP500",
        exchange="CME",
        tick_size=0.25,
        point_value_usd=50.0,
        tick_value_usd=12.50,
    ),
    "MES": ExchangeFuturesSpec(
        root="MES",
        canonical_symbol="SP500",
        exchange="CME",
        tick_size=0.25,
        point_value_usd=5.0,
        tick_value_usd=1.25,
    ),
    "YM": ExchangeFuturesSpec(
        root="YM",
        canonical_symbol="DOW",
        exchange="CBOT",
        tick_size=1.0,
        point_value_usd=5.0,
        tick_value_usd=5.0,
    ),
    "MYM": ExchangeFuturesSpec(
        root="MYM",
        canonical_symbol="DOW",
        exchange="CBOT",
        tick_size=1.0,
        point_value_usd=0.50,
        tick_value_usd=0.50,
    ),
}


_CONTRACT_PATTERN = re.compile(r"^(?P<root>[A-Z]+)\s+(?P<month>\d{2})-(?P<year>\d{2})$")
_QUARTER_MONTHS = {3, 6, 9, 12}


@dataclass(frozen=True)
class ParsedNinjaTraderContract:
    symbol: str
    root: str
    month: int
    year: int


@dataclass(frozen=True)
class FuturesContractResolution:
    status: FuturesContractResolutionStatus
    root: str
    canonical_symbol: str | None
    active_contract: str | None
    rollover_source: str | None
    rollover_timestamp_ms: int | None
    exchange_spec: ExchangeFuturesSpec | None
    reason_codes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status is FuturesContractResolutionStatus.READY and self.active_contract is not None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def parse_ninjatrader_contract_symbol(symbol: str) -> ParsedNinjaTraderContract:
    normalized = " ".join(str(symbol).upper().split())
    match = _CONTRACT_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError("NinjaTrader futures contract must use ROOT MM-YY format")
    month = int(match.group("month"))
    if month not in _QUARTER_MONTHS:
        raise ValueError("supported equity-index futures require a quarterly contract month")
    return ParsedNinjaTraderContract(
        symbol=normalized,
        root=match.group("root"),
        month=month,
        year=2000 + int(match.group("year")),
    )


class NinjaTraderFuturesContractResolver:
    """Resolve a verified active contract from bridge-provided rollover metadata."""

    def resolve(
        self,
        *,
        root: str,
        rollovers: Mapping[str, Mapping[str, Any]],
        instruments: Mapping[str, Mapping[str, Any]],
    ) -> FuturesContractResolution:
        normalized_root = str(root).strip().upper()
        spec = NINJATRADER_EQUITY_INDEX_FUTURES.get(normalized_root)
        if spec is None:
            return self._blocked(
                FuturesContractResolutionStatus.ROOT_UNSUPPORTED,
                root=normalized_root,
                reason="NINJATRADER_FUTURES_ROOT_NOT_IN_VERIFIED_PHASE24_REGISTRY",
            )

        rollover = rollovers.get(normalized_root)
        if not rollover or rollover.get("verified") is not True:
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_ROLLOVER_UNVERIFIED,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="CONTRACT_ROLLOVER_UNVERIFIED",
            )
        source = str(rollover.get("source") or "").strip()
        active_contract = str(rollover.get("active_contract") or "").strip().upper()
        timestamp = rollover.get("as_of_ms")
        if not source or not active_contract or timestamp is None or int(timestamp) < 0:
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_ROLLOVER_UNVERIFIED,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="VERIFIED_ROLLOVER_REQUIRES_ACTIVE_CONTRACT_SOURCE_AND_TIMESTAMP",
            )

        try:
            parsed = parse_ninjatrader_contract_symbol(active_contract)
        except ValueError:
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_SYMBOL_INVALID,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="ACTIVE_CONTRACT_SYMBOL_IS_NOT_VALID_QUARTERLY_NINJATRADER_FORMAT",
            )
        if parsed.root != normalized_root:
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_ROOT_MISMATCH,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="ACTIVE_CONTRACT_ROOT_DOES_NOT_MATCH_EXPLICIT_ACCOUNT_ROOT_SELECTION",
            )

        metadata = instruments.get(parsed.symbol)
        if metadata is None:
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_METADATA_UNAVAILABLE,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="ACTIVE_CONTRACT_NOT_PRESENT_IN_NINJATRADER_BRIDGE_INSTRUMENTS",
            )
        if metadata.get("metadata_verified") is not True:
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_METADATA_UNVERIFIED,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="NINJATRADER_CONTRACT_METADATA_NOT_VERIFIED",
            )
        if not self._metadata_matches(spec=spec, metadata=metadata):
            return self._blocked(
                FuturesContractResolutionStatus.CONTRACT_SPEC_MISMATCH,
                root=normalized_root,
                canonical_symbol=spec.canonical_symbol,
                exchange_spec=spec,
                reason="NINJATRADER_CONTRACT_METADATA_CONFLICTS_WITH_VERIFIED_EXCHANGE_SPEC",
            )

        return FuturesContractResolution(
            status=FuturesContractResolutionStatus.READY,
            root=normalized_root,
            canonical_symbol=canonicalize_symbol(spec.canonical_symbol),
            active_contract=parsed.symbol,
            rollover_source=source,
            rollover_timestamp_ms=int(timestamp),
            exchange_spec=spec,
            reason_codes=(
                "ACTIVE_CONTRACT_VERIFIED_BY_NINJATRADER_BRIDGE",
                "CONTRACT_MONTH_NOT_GUESSED_BY_LONDRES",
                "STANDARD_AND_MICRO_ROOTS_ARE_NEVER_SILENTLY_SUBSTITUTED",
                "RUNTIME_CONTRACT_METADATA_MATCHES_VERIFIED_EXCHANGE_SPEC",
            ),
        )

    @staticmethod
    def _metadata_matches(*, spec: ExchangeFuturesSpec, metadata: Mapping[str, Any]) -> bool:
        try:
            root = str(metadata["root"]).upper()
            canonical = canonicalize_symbol(str(metadata["canonical_symbol"]))
            tick_size = float(metadata["tick_size"])
            point_value = float(metadata["point_value"])
            tick_value = float(metadata["tick_value"])
            currency = str(metadata["currency"]).upper()
        except (KeyError, TypeError, ValueError):
            return False
        return bool(
            root == spec.root
            and canonical == canonicalize_symbol(spec.canonical_symbol)
            and currency == "USD"
            and math.isclose(tick_size, spec.tick_size, rel_tol=0.0, abs_tol=1e-12)
            and math.isclose(point_value, spec.point_value_usd, rel_tol=0.0, abs_tol=1e-9)
            and math.isclose(tick_value, spec.tick_value_usd, rel_tol=0.0, abs_tol=1e-9)
        )

    @staticmethod
    def _blocked(
        status: FuturesContractResolutionStatus,
        *,
        root: str,
        reason: str,
        canonical_symbol: str | None = None,
        exchange_spec: ExchangeFuturesSpec | None = None,
    ) -> FuturesContractResolution:
        return FuturesContractResolution(
            status=status,
            root=root,
            canonical_symbol=canonical_symbol,
            active_contract=None,
            rollover_source=None,
            rollover_timestamp_ms=None,
            exchange_spec=exchange_spec,
            reason_codes=(reason, "FUTURES_CONTRACT_RESOLUTION_FAILS_CLOSED"),
        )
