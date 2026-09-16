"""Phase 23: mixed personal/prop account risk bases and replication."""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent

from .prop_accounts import ClassifiedManagedAccount, MixedAccountExecutionManager


class LondresPhase23MixedAccountRiskEngine:
    """Prepare one Londres setup across mixed personal and prop accounts.

    Personal accounts use actual account equity as the 3/5/10% risk base. Prop
    accounts use the smallest verified active loss buffer, never their nominal
    advertised account size. Every account remains independently sized and no
    broker order is authorized or submitted by this phase.
    """

    def __init__(self) -> None:
        self.manager = MixedAccountExecutionManager()

    def prepare(
        self,
        *,
        intent: TradeIntent,
        accounts: Sequence[ClassifiedManagedAccount],
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict:
        result = self.manager.prepare(intent=intent, accounts=tuple(accounts), policy=policy)
        payload = result.to_dict()
        payload["phase"] = "LONDRES_PHASE23_MIXED_ACCOUNT_PROP_RISK_PREPARATION"
        payload["execution_enabled"] = False
        payload["order_submission_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload

    @staticmethod
    def state_update(context: dict) -> dict:
        return {"mixed_account_risk_state": context}
