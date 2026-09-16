"""Phase 19: universal broker/account registry without execution capability."""

from __future__ import annotations

from collections.abc import Sequence

from .multi_account import ManagedBrokerAccount


class LondresPhase19BrokerRegistryEngine:
    """Expose sanitized broker/account capabilities to AgentState.

    Adapter-owned demo/live classification and credentials never enter this
    context. The registry describes configuration and platform capabilities only.
    """

    def analyze(self, *, accounts: Sequence[ManagedBrokerAccount]) -> dict:
        registry: list[dict] = []
        for account in accounts:
            try:
                status = account.adapter.public_status()
            except Exception:
                status = {"status": "UNAVAILABLE"}
            registry.append(
                {
                    **account.public_dict(),
                    "connection": status,
                    "capabilities": account.adapter.capabilities().public_dict(),
                    "account_environment": "HIDDEN_INTERNAL",
                    "order_submission_enabled": False,
                }
            )
        return {
            "phase": "LONDRES_PHASE19_UNIVERSAL_BROKER_REGISTRY",
            "accounts": registry,
            "account_count": len(registry),
            "broker_order_submission_enabled": False,
            "broker_order_placed": False,
            "account_environment": "HIDDEN_INTERNAL",
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        return {"broker_registry_state": context}
