"""Mixed personal/prop account preparation for one Londres trade intent.

Phase 23 sits above the universal Phase 20 orchestrator. It resolves each
account's sizing equity first, then delegates broker-specific symbol, capability,
tick-value, volume-grid, target and 60/40 validation to Phase 20.

The key invariant is per-account isolation: two personal NinjaTrader accounts
and one prop-firm NinjaTrader account may replicate the same trade while using
three different risk bases and three independently calculated contract sizes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tradingagents.brokers.account_risk import (
    AccountClassification,
    AccountRiskBaseResolver,
    AccountRiskBaseResult,
    AccountRiskProfile,
    RiskBaseAccountAdapter,
)
from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent

from .multi_account import (
    ManagedBrokerAccount,
    MultiAccountBatchStatus,
    MultiAccountExecutionManager,
)


class Phase23AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_ACCOUNT_SNAPSHOT = "BLOCKED_ACCOUNT_SNAPSHOT"
    BLOCKED_ACCOUNT_RISK_BASE = "BLOCKED_ACCOUNT_RISK_BASE"
    BLOCKED_PHASE20_PREPARATION = "BLOCKED_PHASE20_PREPARATION"


@dataclass(frozen=True)
class ClassifiedManagedAccount:
    managed_account: ManagedBrokerAccount
    risk_profile: AccountRiskProfile

    def __post_init__(self) -> None:
        if self.managed_account.account_alias != self.risk_profile.account_alias:
            raise ValueError("managed account alias must match account risk profile alias")


@dataclass(frozen=True)
class Phase23AccountPlan:
    account_alias: str
    broker_type: str
    adapter_id: str
    status: Phase23AccountStatus
    classification: AccountClassification
    classification_status: str
    classification_source: str
    actual_account_equity: float | None
    risk_base: float | None
    risk_base_source: str | None
    nominal_account_size: float | None
    remaining_daily_loss_buffer: float | None
    remaining_drawdown_buffer: float | None
    selected_risk_fraction: float
    risk_cash_budget: float | None
    phase20_account_plan: dict[str, Any] | None
    preparation_ready: bool
    order_authorized: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["classification"] = self.classification.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["nominal_account_size_used_for_sizing"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload


@dataclass(frozen=True)
class Phase23MultiAccountPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase23AccountPlan, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    batch_ready_for_future_execution: bool
    order_authorized: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "risk_model": "PER_ACCOUNT_PERSONAL_EQUITY_OR_PROP_LOSS_BUFFER",
            "position_copy_mode": "REPLICATE_TRADE_INTENT_NOT_RAW_CONTRACT_COUNT",
            "order_authorized": False,
            "broker_order_placed": False,
            "account_environment": "HIDDEN_INTERNAL",
            "reason_codes": list(self.reason_codes),
        }


class MixedAccountExecutionManager:
    """Prepare one setup across personal and prop accounts independently."""

    def __init__(self) -> None:
        self._risk_base = AccountRiskBaseResolver()
        self._phase20 = MultiAccountExecutionManager()

    def prepare(
        self,
        *,
        intent: TradeIntent,
        accounts: list[ClassifiedManagedAccount] | tuple[ClassifiedManagedAccount, ...],
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> Phase23MultiAccountPlan:
        plans = tuple(self._prepare_account(intent=intent, account=item) for item in accounts)
        skipped = sum(plan.status is Phase23AccountStatus.SKIPPED_DISABLED for plan in plans)
        enabled = len(plans) - skipped
        ready = sum(plan.preparation_ready for plan in plans)
        blocked = enabled - ready

        if enabled == 0:
            batch_status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_ACCOUNTS_AVAILABLE_FOR_MIXED_ACCOUNT_REPLICATION",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            batch_status = (
                MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            )
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_PASS_ACCOUNT_RISK_AND_PHASE20",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
            )
        elif ready == enabled:
            batch_status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = (
                "ALL_ENABLED_ACCOUNTS_HAVE_INDEPENDENT_VALID_RISK_BASES_AND_PREPARATION",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
            )
        elif ready > 0:
            batch_status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_BLOCKED_PROP_OR_PERSONAL_ACCOUNTS",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
            )
        else:
            batch_status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = (
                "NO_ENABLED_ACCOUNT_PASSED_ACCOUNT_RISK_AND_PHASE20_PREPARATION",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
            )

        return Phase23MultiAccountPlan(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            policy=policy,
            status=batch_status,
            accounts=plans,
            enabled_accounts=enabled,
            ready_accounts=ready,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            batch_ready_for_future_execution=batch_ready,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=reasons,
        )

    def _prepare_account(
        self,
        *,
        intent: TradeIntent,
        account: ClassifiedManagedAccount,
    ) -> Phase23AccountPlan:
        managed = account.managed_account
        profile = account.risk_profile

        if not managed.enabled:
            return Phase23AccountPlan(
                account_alias=managed.account_alias,
                broker_type=managed.adapter.broker_type.value,
                adapter_id=managed.adapter.adapter_id,
                status=Phase23AccountStatus.SKIPPED_DISABLED,
                classification=AccountClassification.UNKNOWN,
                classification_status="NOT_EVALUATED",
                classification_source="NOT_EVALUATED",
                actual_account_equity=None,
                risk_base=None,
                risk_base_source=None,
                nominal_account_size=None,
                remaining_daily_loss_buffer=None,
                remaining_drawdown_buffer=None,
                selected_risk_fraction=managed.risk_fraction,
                risk_cash_budget=None,
                phase20_account_plan=None,
                preparation_ready=False,
                order_authorized=False,
                broker_order_placed=False,
                reason_codes=(
                    "ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
                    "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
                ),
            )

        try:
            actual_snapshot = managed.adapter.account_snapshot(managed.account_alias)
        except Exception:
            return self._blocked_without_risk_base(
                account=account,
                status=Phase23AccountStatus.BLOCKED_ACCOUNT_SNAPSHOT,
                reason="BROKER_ACCOUNT_SNAPSHOT_UNAVAILABLE_FOR_ACCOUNT_RISK_CLASSIFICATION",
            )

        risk_base = self._risk_base.resolve(
            profile=profile,
            actual_account_equity=actual_snapshot.equity,
        )
        if not risk_base.ready or risk_base.risk_base is None:
            return self._blocked_from_risk_base(
                account=account,
                risk_base=risk_base,
            )

        proxy = RiskBaseAccountAdapter(
            managed.adapter,
            account_alias=managed.account_alias,
            risk_base=risk_base.risk_base,
        )
        phase20_account = ManagedBrokerAccount(
            account_alias=managed.account_alias,
            adapter=proxy,
            symbol_map=managed.symbol_map,
            risk_fraction=managed.risk_fraction,
            enabled=True,
            max_risk_cash=managed.max_risk_cash,
        )
        phase20 = self._phase20.prepare(
            intent=intent,
            accounts=(phase20_account,),
            policy=OrchestrationPolicy.BEST_EFFORT,
        )
        phase20_plan = phase20.accounts[0].to_dict()
        phase20_ready = phase20.accounts[0].preparation_ready

        risk_cash_budget = risk_base.risk_base * managed.risk_fraction
        if managed.max_risk_cash is not None:
            risk_cash_budget = min(risk_cash_budget, managed.max_risk_cash)

        if not phase20_ready:
            return Phase23AccountPlan(
                account_alias=managed.account_alias,
                broker_type=managed.adapter.broker_type.value,
                adapter_id=managed.adapter.adapter_id,
                status=Phase23AccountStatus.BLOCKED_PHASE20_PREPARATION,
                classification=risk_base.classification,
                classification_status=risk_base.classification_status.value,
                classification_source=risk_base.classification_source.value,
                actual_account_equity=risk_base.actual_account_equity,
                risk_base=risk_base.risk_base,
                risk_base_source=risk_base.risk_base_source,
                nominal_account_size=risk_base.nominal_account_size,
                remaining_daily_loss_buffer=risk_base.remaining_daily_loss_buffer,
                remaining_drawdown_buffer=risk_base.remaining_drawdown_buffer,
                selected_risk_fraction=managed.risk_fraction,
                risk_cash_budget=risk_cash_budget,
                phase20_account_plan=phase20_plan,
                preparation_ready=False,
                order_authorized=False,
                broker_order_placed=False,
                reason_codes=(
                    *risk_base.reason_codes,
                    "ACCOUNT_RISK_BASE_VALID_BUT_PHASE20_PREPARATION_BLOCKED",
                    *phase20.accounts[0].reason_codes,
                    "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
                ),
            )

        return Phase23AccountPlan(
            account_alias=managed.account_alias,
            broker_type=managed.adapter.broker_type.value,
            adapter_id=managed.adapter.adapter_id,
            status=Phase23AccountStatus.READY,
            classification=risk_base.classification,
            classification_status=risk_base.classification_status.value,
            classification_source=risk_base.classification_source.value,
            actual_account_equity=risk_base.actual_account_equity,
            risk_base=risk_base.risk_base,
            risk_base_source=risk_base.risk_base_source,
            nominal_account_size=risk_base.nominal_account_size,
            remaining_daily_loss_buffer=risk_base.remaining_daily_loss_buffer,
            remaining_drawdown_buffer=risk_base.remaining_drawdown_buffer,
            selected_risk_fraction=managed.risk_fraction,
            risk_cash_budget=risk_cash_budget,
            phase20_account_plan=phase20_plan,
            preparation_ready=True,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(
                *risk_base.reason_codes,
                "PHASE20_SIZED_FROM_ACCOUNT_SPECIFIC_PHASE23_RISK_BASE",
                "PERSONAL_AND_PROP_ACCOUNTS_CAN_SHARE_ONE_TRADE_INTENT_WITH_DIFFERENT_SIZES",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23",
            ),
        )

    @staticmethod
    def _blocked_without_risk_base(
        *,
        account: ClassifiedManagedAccount,
        status: Phase23AccountStatus,
        reason: str,
    ) -> Phase23AccountPlan:
        managed = account.managed_account
        return Phase23AccountPlan(
            account_alias=managed.account_alias,
            broker_type=managed.adapter.broker_type.value,
            adapter_id=managed.adapter.adapter_id,
            status=status,
            classification=AccountClassification.UNKNOWN,
            classification_status="NOT_EVALUATED",
            classification_source="NOT_EVALUATED",
            actual_account_equity=None,
            risk_base=None,
            risk_base_source=None,
            nominal_account_size=None,
            remaining_daily_loss_buffer=None,
            remaining_drawdown_buffer=None,
            selected_risk_fraction=managed.risk_fraction,
            risk_cash_budget=None,
            phase20_account_plan=None,
            preparation_ready=False,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23"),
        )

    @staticmethod
    def _blocked_from_risk_base(
        *,
        account: ClassifiedManagedAccount,
        risk_base: AccountRiskBaseResult,
    ) -> Phase23AccountPlan:
        managed = account.managed_account
        return Phase23AccountPlan(
            account_alias=managed.account_alias,
            broker_type=managed.adapter.broker_type.value,
            adapter_id=managed.adapter.adapter_id,
            status=Phase23AccountStatus.BLOCKED_ACCOUNT_RISK_BASE,
            classification=risk_base.classification,
            classification_status=risk_base.classification_status.value,
            classification_source=risk_base.classification_source.value,
            actual_account_equity=risk_base.actual_account_equity,
            risk_base=None,
            risk_base_source=None,
            nominal_account_size=risk_base.nominal_account_size,
            remaining_daily_loss_buffer=risk_base.remaining_daily_loss_buffer,
            remaining_drawdown_buffer=risk_base.remaining_drawdown_buffer,
            selected_risk_fraction=managed.risk_fraction,
            risk_cash_budget=None,
            phase20_account_plan=None,
            preparation_ready=False,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(*risk_base.reason_codes, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE23"),
        )
