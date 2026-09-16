"""Canonical-to-broker symbol mapping for interchangeable broker adapters."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import BrokerType, canonicalize_symbol


class SymbolMappingError(ValueError):
    """Raised when an account has no explicit mapping for a canonical symbol."""


@dataclass(frozen=True)
class BrokerSymbolMap:
    """Explicit symbol map for one broker/account.

    Broker symbol names are intentionally not guessed. CFD names, suffixes and
    futures contracts differ by broker/account and can change over time.
    """

    broker_type: BrokerType
    mapping: dict[str, str]

    def __post_init__(self) -> None:
        normalized: dict[str, str] = {}
        for canonical, broker_symbol in self.mapping.items():
            key = canonicalize_symbol(canonical)
            value = str(broker_symbol).strip()
            if not value:
                raise ValueError(f"Empty broker symbol mapping for {canonical!r}")
            normalized[key] = value
        object.__setattr__(self, "mapping", normalized)

    def resolve(self, canonical_symbol: str) -> str:
        canonical = canonicalize_symbol(canonical_symbol)
        broker_symbol = self.mapping.get(canonical)
        if broker_symbol is None:
            raise SymbolMappingError(
                f"No explicit {self.broker_type.value} symbol mapping for canonical symbol {canonical}"
            )
        return broker_symbol

    def public_dict(self) -> dict[str, object]:
        return {
            "broker_type": self.broker_type.value,
            "canonical_symbols": sorted(self.mapping),
            "mapping_count": len(self.mapping),
        }
