"""Phase 23: live-account-only risk sizing and multi-account replication."""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent

from .multi_account import ManagedBrokerAccount, MultiAccountExecutionManager


class LondresPhase23LiveAccountRiskEngine:
    """Prepare one Londres setup across live brokerage accounts.

    Every enabled account sizes independently from its current broker-reported
    equity and the selected 3/5/10% risk tier. The engine replicates trade intent,
    never raw lots/contracts, and never submits an order.
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
        payload["phase"] = "LONDRES_PHASE23_LIVE_ACCOUNT_RISK_PREPARATION"
        payload["risk_base_mode"] = "CURRENT_BROKER_ACCOUNT_EQUITY"
        payload["account_scope"] = "LIVE_BROKERAGE_ACCOUNTS_ONLY"
        payload["execution_enabled"] = False
        payload["order_submission_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload

    @staticmethod
    def state_update(context: dict) -> dict:
        return {"live_account_risk_state": context}
