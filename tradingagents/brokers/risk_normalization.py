"""Broker-native risk metadata normalization for Londres sizing.

Phase 21 turns a broker-agnostic ``BrokerInstrumentSpec`` into the exact
``InstrumentRiskSpec`` consumed by the deterministic Phase 11 sizing engine.
The conversion is deliberately fail-closed: a positive tick value must already
be resolved in the connected account's deposit currency by the broker adapter.
No generic fallback tick value, contract multiplier, or FX rate is guessed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tradingagents.ict.risk_sizing import InstrumentRiskSpec

from .contracts import BrokerAccountSnapshot, BrokerInstrumentSpec


class BrokerRiskNormalizationStatus(str, Enum):
    READY = "READY"
    WAIT_FOR_CONNECTED_ACCOUNT = "WAIT_FOR_CONNECTED_ACCOUNT"
    WAIT_FOR_ACCOUNT_CURRENCY = "WAIT_FOR_ACCOUNT_CURRENCY"
    WAIT_FOR_VERIFIED_TICK_VALUE = "WAIT_FOR_VERIFIED_TICK_VALUE"
    INSTRUMENT_ACCOUNT_CURRENCY_MISMATCH = "INSTRUMENT_ACCOUNT_CURRENCY_MISMATCH"
    INVALID_INSTRUMENT_METADATA = "INVALID_INSTRUMENT_METADATA"


@dataclass(frozen=True)
class BrokerRiskNormalizationResult:
    status: BrokerRiskNormalizationStatus
    account_alias: str
    canonical_symbol: str
    broker_symbol: str
    account_currency: str | None
    tick_size: float
    tick_value_account_currency: float | None
    tick_value_currency: str | None
    tick_value_source: str | None
    tick_value_timestamp_ms: int | None
    valuation_model: str | None
    instrument_risk_spec: InstrumentRiskSpec | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        if self.instrument_risk_spec is not None:
            payload["instrument_risk_spec"] = asdict(self.instrument_risk_spec)
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["manual_tick_value_allowed"] = False
        return payload


class BrokerRiskNormalizer:
    """Validate broker-resolved valuation metadata before deterministic sizing."""

    def normalize(
        self,
        *,
        account: BrokerAccountSnapshot,
        instrument: BrokerInstrumentSpec,
    ) -> BrokerRiskNormalizationResult:
        currency = str(account.currency or "").strip().upper()
        tick_currency = (
            str(instrument.tick_value_currency).strip().upper()
            if instrument.tick_value_currency
            else None
        )

        if not account.connected:
            return self._blocked(
                BrokerRiskNormalizationStatus.WAIT_FOR_CONNECTED_ACCOUNT,
                account=account,
                instrument=instrument,
                reason="CONNECTED_BROKER_ACCOUNT_REQUIRED_FOR_RISK_NORMALIZATION",
            )
        if not currency or currency == "UNKNOWN":
            return self._blocked(
                BrokerRiskNormalizationStatus.WAIT_FOR_ACCOUNT_CURRENCY,
                account=account,
                instrument=instrument,
                reason="BROKER_ACCOUNT_DEPOSIT_CURRENCY_REQUIRED",
            )
        if (
            not instrument.metadata_verified
            or instrument.tick_value_account_currency is None
            or instrument.tick_value_account_currency <= 0
        ):
            return self._blocked(
                BrokerRiskNormalizationStatus.WAIT_FOR_VERIFIED_TICK_VALUE,
                account=account,
                instrument=instrument,
                reason="VERIFIED_TICK_VALUE_IN_ACCOUNT_CURRENCY_REQUIRED",
            )
        if tick_currency is not None and tick_currency != currency:
            return self._blocked(
                BrokerRiskNormalizationStatus.INSTRUMENT_ACCOUNT_CURRENCY_MISMATCH,
                account=account,
                instrument=instrument,
                reason="TICK_VALUE_CURRENCY_MUST_MATCH_ACCOUNT_DEPOSIT_CURRENCY",
            )

        try:
            risk_spec = InstrumentRiskSpec(
                symbol=instrument.broker_symbol,
                tick_size=float(instrument.tick_size),
                tick_value_per_volume_unit=float(instrument.tick_value_account_currency),
                volume_step=float(instrument.volume_step),
                min_volume=float(instrument.min_volume),
                max_volume=float(instrument.max_volume),
                volume_unit=instrument.volume_unit,
                pip_size=(float(instrument.pip_size) if instrument.pip_size is not None else None),
            )
        except (TypeError, ValueError):
            return self._blocked(
                BrokerRiskNormalizationStatus.INVALID_INSTRUMENT_METADATA,
                account=account,
                instrument=instrument,
                reason="BROKER_INSTRUMENT_METADATA_CANNOT_FORM_VALID_RISK_SPEC",
            )

        return BrokerRiskNormalizationResult(
            status=BrokerRiskNormalizationStatus.READY,
            account_alias=account.account_alias,
            canonical_symbol=instrument.canonical_symbol,
            broker_symbol=instrument.broker_symbol,
            account_currency=currency,
            tick_size=instrument.tick_size,
            tick_value_account_currency=instrument.tick_value_account_currency,
            tick_value_currency=tick_currency or currency,
            tick_value_source=instrument.tick_value_source,
            tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
            valuation_model=instrument.valuation_model,
            instrument_risk_spec=risk_spec,
            reason_codes=(
                "BROKER_TICK_VALUE_RESOLVED_IN_ACCOUNT_CURRENCY",
                "BROKER_VOLUME_GRID_PRESERVED",
                "NO_GENERIC_TICK_VALUE_OR_CONTRACT_MULTIPLIER_GUESSED",
            ),
        )

    @staticmethod
    def _blocked(
        status: BrokerRiskNormalizationStatus,
        *,
        account: BrokerAccountSnapshot,
        instrument: BrokerInstrumentSpec,
        reason: str,
    ) -> BrokerRiskNormalizationResult:
        return BrokerRiskNormalizationResult(
            status=status,
            account_alias=account.account_alias,
            canonical_symbol=instrument.canonical_symbol,
            broker_symbol=instrument.broker_symbol,
            account_currency=account.currency,
            tick_size=instrument.tick_size,
            tick_value_account_currency=instrument.tick_value_account_currency,
            tick_value_currency=instrument.tick_value_currency,
            tick_value_source=instrument.tick_value_source,
            tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
            valuation_model=instrument.valuation_model,
            instrument_risk_spec=None,
            reason_codes=(reason, "RISK_SIZING_FAILS_CLOSED_UNTIL_METADATA_IS_VERIFIED"),
        )
