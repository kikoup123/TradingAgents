"""Phase 20: prepare one Londres setup independently across many accounts."""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent

from .multi_account import ManagedBrokerAccount, MultiAccountExecutionManager


class LondresPhase20MultiAccountEngine:
    """Broker-agnostic multi-account trade-intent replication.

    This layer computes account-specific volumes and validates management
    compatibility. It never authorizes or sends a broker order.
    """

    def __init__(self) -> None:
        self.manager = MultiAccountExecutionManager()

    def prepare(
        self,
        *,
        intent: TradeIntent,
        accounts: Sequence[ManagedBrokerAccount],
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict:
        result = self.manager.prepare(intent=intent, accounts=tuple(accounts), policy=policy)
        payload = result.to_dict()
        payload["phase"] = "LONDRES_PHASE20_MULTI_ACCOUNT_PREPARATION"
        payload["execution_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload

    @staticmethod
    def state_update(context: dict) -> dict:
        return {"multi_account_execution_state": context}
