"""Phase 22: broker connection, quote and valuation supervision."""

from __future__ import annotations

from tradingagents.brokers.contracts import BrokerAdapter
from tradingagents.brokers.supervision import (
    BrokerConnectionSupervisor,
    BrokerSupervisionPolicy,
)


class LondresPhase22BrokerSupervisionEngine:
    """Run one fail-closed broker supervision heartbeat.

    Phase 22 does not submit, amend, close or authorize any broker order. It
    verifies that the account data path is connected and that market-dependent
    quote/tick-value inputs are fresh according to an explicit caller policy.
    """

    def __init__(self) -> None:
        self._supervisor = BrokerConnectionSupervisor()

    def analyze(
        self,
        *,
        adapter: BrokerAdapter,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
        now_ms: int,
        policy: BrokerSupervisionPolicy,
    ) -> dict:
        result = self._supervisor.check(
            adapter=adapter,
            account_alias=account_alias,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            now_ms=now_ms,
            policy=policy,
        )
        return {
            "phase": "LONDRES_PHASE22_BROKER_CONNECTION_SUPERVISION",
            "policy": {
                "quote_max_age_ms": policy.quote_max_age_ms,
                "tick_value_max_age_ms": policy.tick_value_max_age_ms,
                "max_reconnect_attempts": policy.max_reconnect_attempts,
                "future_timestamp_tolerance_ms": policy.future_timestamp_tolerance_ms,
            },
            "supervision": result.to_dict(),
            "execution_data_ready": result.execution_data_ready,
            "read_only": True,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        return {"broker_supervision_state": context}
