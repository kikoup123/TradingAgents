"""Phase 29: FP Markets cTrader and Vantage MT5 read-only broker parity.

This phase brings the two CFD broker paths to the same deterministic preparation
boundary: explicit broker identity, explicit symbol mapping, fresh supervised
quotes/tick values, and independent sizing from each account's current equity.
Demo/live environment remains private broker-routing metadata. It does not
authorize or submit orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import BrokerType, OrchestrationPolicy, TradeIntent
from tradingagents.brokers.supervision import BrokerConnectionSupervisor, BrokerSupervisionPolicy
from tradingagents.brokers.symbols import SymbolMappingError

from .multi_account import ManagedBrokerAccount, MultiAccountBatchStatus
from .phase23 import LondresPhase23LiveAccountRiskEngine


class Phase29BrokerVenue(str, Enum):
    FP_MARKETS_CTRADER = "FP_MARKETS_CTRADER"
    VANTAGE_MT5 = "VANTAGE_MT5"


class Phase29AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_BROKER_TYPE = "BLOCKED_BROKER_TYPE"
    BLOCKED_SYMBOL_MAPPING = "BLOCKED_SYMBOL_MAPPING"
    BLOCKED_ADAPTER = "BLOCKED_ADAPTER"
    BLOCKED_PROVIDER_IDENTITY = "BLOCKED_PROVIDER_IDENTITY"
    BLOCKED_SUPERVISION = "BLOCKED_SUPERVISION"
    BLOCKED_PHASE23 = "BLOCKED_PHASE23"
    BLOCKED_BATCH_POLICY = "BLOCKED_BATCH_POLICY"


@dataclass(frozen=True)
class Phase29BrokerBinding:
    venue: Phase29BrokerVenue
    account: ManagedBrokerAccount


@dataclass(frozen=True)
class Phase29AccountPlan:
    account_alias: str
    venue: Phase29BrokerVenue
    broker_type: BrokerType
    status: Phase29AccountStatus
    trade_id: str
    canonical_symbol: str
    broker_symbol: str | None
    broker_name: str | None
    supervision_state: dict[str, Any] | None
    phase23_account_plan: dict[str, Any] | None
    preparation_ready: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["venue"] = self.venue.value
        payload["broker_type"] = self.broker_type.value
        payload["status"] = self.status.value
        payload["account_scope"] = "BROKERAGE_ACCOUNTS"
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["execution_enabled"] = False
        payload["order_submission_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload


@dataclass(frozen=True)
class Phase29BrokerParityPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase29AccountPlan, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    batch_ready_for_future_execution: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE29_FP_VANTAGE_READ_ONLY_BROKER_PARITY",
            "trade_id": self.trade_id,
            "canonical_symbol": self.canonical_symbol,
            "policy": self.policy.value,
            "status": self.status.value,
            "accounts": [account.to_dict() for account in self.accounts],
            "enabled_accounts": self.enabled_accounts,
            "ready_accounts": self.ready_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "batch_ready_for_future_execution": self.batch_ready_for_future_execution,
            "risk_base_mode": "CURRENT_BROKER_ACCOUNT_EQUITY",
            "replication_mode": "TRADE_INTENT_NOT_RAW_LOT_COPYING",
            "broker_parity_scope": [
                Phase29BrokerVenue.FP_MARKETS_CTRADER.value,
                Phase29BrokerVenue.VANTAGE_MT5.value,
            ],
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase29BrokerParityEngine:
    """Prepare FP Markets cTrader and Vantage MT5 accounts under one policy."""

    _EXPECTED_TYPES = {
        Phase29BrokerVenue.FP_MARKETS_CTRADER: BrokerType.CTRADER,
        Phase29BrokerVenue.VANTAGE_MT5: BrokerType.MT5,
    }

    def __init__(self) -> None:
        self._supervisor = BrokerConnectionSupervisor()
        self._phase23 = LondresPhase23LiveAccountRiskEngine()

    def prepare(
        self,
        *,
        intent: TradeIntent,
        bindings: tuple[Phase29BrokerBinding, ...] | list[Phase29BrokerBinding],
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict[str, Any]:
        aliases = [binding.account.account_alias for binding in bindings]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Phase 29 account aliases must be unique")

        plans = tuple(
            self._prepare_account(
                intent=intent,
                binding=binding,
                now_ms=now_ms,
                supervision_policy=supervision_policy,
            )
            for binding in bindings
        )
        enabled = sum(plan.status is not Phase29AccountStatus.SKIPPED_DISABLED for plan in plans)
        skipped = len(plans) - enabled
        ready = sum(plan.preparation_ready for plan in plans)
        blocked = enabled - ready

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_FP_MARKETS_OR_VANTAGE_ACCOUNT",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE and blocked:
            plans = tuple(
                replace(
                    plan,
                    status=Phase29AccountStatus.BLOCKED_BATCH_POLICY,
                    preparation_ready=False,
                    reason_codes=plan.reason_codes
                    + ("ALL_OR_NONE_REVOKED_OTHERWISE_READY_PHASE29_ACCOUNT",),
                )
                if plan.preparation_ready
                else plan
                for plan in plans
            )
            ready = 0
            blocked = enabled
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("ALL_OR_NONE_REQUIRES_EVERY_ENABLED_PHASE29_ACCOUNT_READY",)
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = ("FP_MARKETS_AND_VANTAGE_PATHS_PASS_READ_ONLY_PARITY",)
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = ("BEST_EFFORT_ISOLATES_BLOCKED_BROKER_ACCOUNT",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("NO_PHASE29_ACCOUNT_PASSED_BROKER_PARITY",)

        return Phase29BrokerParityPlan(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            policy=policy,
            status=status,
            accounts=plans,
            enabled_accounts=enabled,
            ready_accounts=ready,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            batch_ready_for_future_execution=batch_ready,
            reason_codes=reasons + ("NO_BROKER_ORDER_SUBMISSION_IN_PHASE29",),
        ).to_dict()

    def _prepare_account(
        self,
        *,
        intent: TradeIntent,
        binding: Phase29BrokerBinding,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
    ) -> Phase29AccountPlan:
        account = binding.account
        expected_type = self._EXPECTED_TYPES[binding.venue]
        if not account.enabled:
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.SKIPPED_DISABLED,
                reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
            )
        if account.adapter.broker_type is not expected_type:
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.BLOCKED_BROKER_TYPE,
                reason=f"{binding.venue.value}_REQUIRES_{expected_type.value}",
            )
        try:
            broker_symbol = account.symbol_map.resolve(intent.canonical)
        except SymbolMappingError:
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.BLOCKED_SYMBOL_MAPPING,
                reason="EXPLICIT_BROKER_SYMBOL_MAPPING_REQUIRED_NO_GUESSING",
            )
        try:
            snapshot = account.adapter.account_snapshot(account.account_alias)
        except Exception:
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.BLOCKED_ADAPTER,
                broker_symbol=broker_symbol,
                reason="BROKER_ACCOUNT_SNAPSHOT_UNAVAILABLE",
            )
        if not self._provider_matches(binding.venue, snapshot.broker_name):
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.BLOCKED_PROVIDER_IDENTITY,
                broker_symbol=broker_symbol,
                broker_name=snapshot.broker_name,
                reason="BROKER_IDENTITY_DOES_NOT_MATCH_CONFIGURED_PHASE29_VENUE",
            )

        supervision = self._supervisor.check(
            adapter=account.adapter,
            account_alias=account.account_alias,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            now_ms=now_ms,
            policy=supervision_policy,
        )
        if not supervision.execution_data_ready:
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.BLOCKED_SUPERVISION,
                broker_symbol=broker_symbol,
                broker_name=snapshot.broker_name,
                supervision_state=supervision.to_dict(),
                reason=f"BROKER_SUPERVISION_NOT_READY_{supervision.status.value}",
            )

        phase23 = self._phase23.prepare(
            intent=intent,
            accounts=(account,),
            policy=OrchestrationPolicy.BEST_EFFORT,
        )
        account_plans = phase23.get("accounts") or []
        phase23_account = account_plans[0] if len(account_plans) == 1 else None
        if not isinstance(phase23_account, dict) or phase23_account.get("preparation_ready") is not True:
            phase23_status = (
                str(phase23_account.get("status")) if isinstance(phase23_account, dict) else "MISSING"
            )
            return self._blocked(
                intent=intent,
                binding=binding,
                status=Phase29AccountStatus.BLOCKED_PHASE23,
                broker_symbol=broker_symbol,
                broker_name=snapshot.broker_name,
                supervision_state=supervision.to_dict(),
                phase23_account_plan=phase23_account,
                reason=f"PHASE23_ACCOUNT_RISK_PREPARATION_NOT_READY_{phase23_status}",
            )

        return Phase29AccountPlan(
            account_alias=account.account_alias,
            venue=binding.venue,
            broker_type=account.adapter.broker_type,
            status=Phase29AccountStatus.READY,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            broker_name=snapshot.broker_name,
            supervision_state=supervision.to_dict(),
            phase23_account_plan=phase23_account,
            preparation_ready=True,
            reason_codes=(
                "BROKER_IDENTITY_VERIFIED",
                "BROKER_SUPERVISION_HEALTHY",
                "CURRENT_ACCOUNT_EQUITY_RISK_SIZING_READY",
                "TRADE_INTENT_REPLICATED_WITH_INDEPENDENT_ACCOUNT_VOLUME",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29",
            ),
        )

    @staticmethod
    def _provider_matches(venue: Phase29BrokerVenue, broker_name: str | None) -> bool:
        normalized = "".join(character for character in str(broker_name or "").upper() if character.isalnum())
        if venue is Phase29BrokerVenue.FP_MARKETS_CTRADER:
            return "FPMARKETS" in normalized
        if venue is Phase29BrokerVenue.VANTAGE_MT5:
            return "VANTAGE" in normalized
        return False

    @staticmethod
    def _blocked(
        *,
        intent: TradeIntent,
        binding: Phase29BrokerBinding,
        status: Phase29AccountStatus,
        reason: str,
        broker_symbol: str | None = None,
        broker_name: str | None = None,
        supervision_state: dict[str, Any] | None = None,
        phase23_account_plan: dict[str, Any] | None = None,
    ) -> Phase29AccountPlan:
        return Phase29AccountPlan(
            account_alias=binding.account.account_alias,
            venue=binding.venue,
            broker_type=binding.account.adapter.broker_type,
            status=status,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            broker_name=broker_name,
            supervision_state=supervision_state,
            phase23_account_plan=phase23_account_plan,
            preparation_ready=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29"),
        )

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"broker_parity_state": context}
