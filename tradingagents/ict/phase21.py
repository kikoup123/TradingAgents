"""Phase 21: broker-native tick-value and account-currency normalization."""

from __future__ import annotations

from tradingagents.brokers.contracts import BrokerAdapter, canonicalize_symbol
from tradingagents.brokers.risk_normalization import BrokerRiskNormalizer


class LondresPhase21BrokerRiskEngine:
    """Produce a fail-closed Phase 11 instrument spec from broker-native data.

    The broker adapter owns contract semantics and currency conversion. This
    engine only accepts a tick value that has already been resolved into the
    connected account's deposit currency; it never guesses a contract multiplier
    or an FX rate.
    """

    def __init__(self) -> None:
        self._normalizer = BrokerRiskNormalizer()

    def analyze(
        self,
        *,
        adapter: BrokerAdapter,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
    ) -> dict:
        account = adapter.account_snapshot(account_alias)
        instrument = adapter.instrument_snapshot(
            account_alias=account_alias,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
        )
        normalized = self._normalizer.normalize(account=account, instrument=instrument)
        return {
            "phase": "LONDRES_PHASE21_BROKER_NATIVE_RISK_NORMALIZATION",
            "account": account.public_dict(),
            "canonical_symbol": canonicalize_symbol(canonical_symbol),
            "broker_symbol": broker_symbol,
            "broker_instrument": instrument.public_dict(),
            "risk_normalization": normalized.to_dict(),
            "read_only": True,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        return {"broker_risk_normalization_state": context}
