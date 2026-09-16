"""Phase 24: NinjaTrader read-only discovery, rollover and mixed-account preparation."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tradingagents.brokers.account_risk import (
    AccountClassification,
    AccountClassificationSource,
    AccountRiskProfile,
    PropFirmRiskLimits,
)
from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent, canonicalize_symbol
from tradingagents.brokers.ninjatrader import (
    NinjaTraderDiscoveredAccount,
    NinjaTraderUniversalReadOnlyAdapter,
)
from tradingagents.brokers.ninjatrader_futures import NINJATRADER_EQUITY_INDEX_FUTURES
from tradingagents.brokers.supervision import (
    BrokerConnectionSupervisor,
    BrokerSupervisionPolicy,
)
from tradingagents.brokers.symbols import BrokerSymbolMap

from .multi_account import ManagedBrokerAccount, MultiAccountBatchStatus
from .prop_accounts import ClassifiedManagedAccount, MixedAccountExecutionManager
from .risk_sizing import ALLOWED_RISK_FRACTIONS


class Phase24AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_ACCOUNT_NOT_DISCOVERED = "BLOCKED_ACCOUNT_NOT_DISCOVERED"
    BLOCKED_ROOT_MAPPING = "BLOCKED_ROOT_MAPPING"
    BLOCKED_CONTRACT_RESOLUTION = "BLOCKED_CONTRACT_RESOLUTION"
    BLOCKED_SUPERVISION = "BLOCKED_SUPERVISION"
    BLOCKED_PHASE23 = "BLOCKED_PHASE23"
    BLOCKED_ADAPTER_ERROR = "BLOCKED_ADAPTER_ERROR"


@dataclass(frozen=True)
class NinjaTraderAccountBinding:
    """Private user/configuration binding for one discovered NinjaTrader account."""

    account_alias: str
    root_map: dict[str, str]
    risk_fraction: float
    configured_classification: AccountClassification | None = None
    classification_source: AccountClassificationSource = AccountClassificationSource.UNKNOWN
    prop_limits: PropFirmRiskLimits | None = None
    enabled: bool = True
    max_risk_cash: float | None = None

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if not any(
            math.isclose(self.risk_fraction, allowed, abs_tol=1e-12)
            for allowed in ALLOWED_RISK_FRACTIONS
        ):
            raise ValueError("risk_fraction must be exactly 0.03, 0.05, or 0.10")
        if self.max_risk_cash is not None and self.max_risk_cash <= 0:
            raise ValueError("max_risk_cash must be > 0 when supplied")
        normalized: dict[str, str] = {}
        for canonical, root in self.root_map.items():
            key = canonicalize_symbol(canonical)
            value = str(root).strip().upper()
            if not value:
                raise ValueError("NinjaTrader futures root cannot be blank")
            normalized[key] = value
        object.__setattr__(self, "root_map", normalized)


@dataclass(frozen=True)
class Phase24AccountPlan:
    account_alias: str
    masked_account: str | None
    provider: str | None
    status: Phase24AccountStatus
    canonical_symbol: str
    selected_root: str | None
    active_contract: str | None
    detected_classification: str | None
    classification_metadata_verified: bool
    supervision_state: dict[str, Any] | None
    phase23_account_plan: dict[str, Any] | None
    preparation_ready: bool
    order_authorized: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["read_only"] = True
        payload["order_submission_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload


@dataclass(frozen=True)
class Phase24MultiAccountPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase24AccountPlan, ...]
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
            "platform": "NINJATRADER",
            "replication_mode": "ONE_TRADE_INTENT_PER_ACCOUNT_INDEPENDENT_RISK_AND_CONTRACT_PLAN",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase24NinjaTraderReadOnlyEngine:
    """Prepare mixed NinjaTrader accounts using verified local read-only data."""

    def __init__(self, adapter: NinjaTraderUniversalReadOnlyAdapter) -> None:
        self.adapter = adapter
        self._supervisor = BrokerConnectionSupervisor()
        self._phase23 = MixedAccountExecutionManager()

    def discover(self) -> dict[str, Any]:
        accounts = self.adapter.discover_accounts()
        return {
            "phase": "LONDRES_PHASE24_NINJATRADER_READ_ONLY_DISCOVERY",
            "platform": "NINJATRADER",
            "bridge": self.adapter.bridge_metadata(),
            "accounts": [account.to_dict() for account in accounts],
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
        }

    def prepare(
        self,
        *,
        intent: TradeIntent,
        bindings: list[NinjaTraderAccountBinding] | tuple[NinjaTraderAccountBinding, ...],
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict[str, Any]:
        discovered = {item.account_alias: item for item in self.adapter.discover_accounts()}
        plans = tuple(
            self._prepare_account(
                intent=intent,
                binding=binding,
                discovered=discovered.get(binding.account_alias),
                now_ms=now_ms,
                supervision_policy=supervision_policy,
            )
            for binding in bindings
        )
        skipped = sum(plan.status is Phase24AccountStatus.SKIPPED_DISABLED for plan in plans)
        enabled = len(plans) - skipped
        ready = sum(plan.preparation_ready for plan in plans)
        blocked = enabled - ready

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_NINJATRADER_ACCOUNTS",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            status = MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_NINJATRADER_ACCOUNT_TO_PASS_PHASE24",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE24",
            )
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = (
                "ALL_ENABLED_NINJATRADER_ACCOUNTS_READY_FOR_FUTURE_EXECUTION_ADAPTER",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE24",
            )
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_BLOCKED_NINJATRADER_ACCOUNTS",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE24",
            )
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = (
                "NO_NINJATRADER_ACCOUNT_PASSED_PHASE24_PREPARATION",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE24",
            )

        result = Phase24MultiAccountPlan(
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
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=reasons,
        )
        payload = result.to_dict()
        payload["phase"] = "LONDRES_PHASE24_NINJATRADER_READ_ONLY_PREPARATION"
        payload["bridge"] = self.adapter.bridge_metadata()
        return payload

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"ninjatrader_readonly_state": context}

    def _prepare_account(
        self,
        *,
        intent: TradeIntent,
        binding: NinjaTraderAccountBinding,
        discovered: NinjaTraderDiscoveredAccount | None,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
    ) -> Phase24AccountPlan:
        canonical = intent.canonical
        if not binding.enabled:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.SKIPPED_DISABLED,
                discovered=discovered,
                reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
            )
        if discovered is None:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_ACCOUNT_NOT_DISCOVERED,
                discovered=None,
                reason="CONFIGURED_NINJATRADER_ACCOUNT_ALIAS_NOT_DISCOVERED",
            )

        root = binding.root_map.get(canonical)
        if root is None:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_ROOT_MAPPING,
                discovered=discovered,
                reason="EXPLICIT_ACCOUNT_FUTURES_ROOT_MAPPING_REQUIRED",
            )
        spec = NINJATRADER_EQUITY_INDEX_FUTURES.get(root)
        if spec is None or canonicalize_symbol(spec.canonical_symbol) != canonical:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_ROOT_MAPPING,
                discovered=discovered,
                selected_root=root,
                reason="SELECTED_FUTURES_ROOT_DOES_NOT_MATCH_CANONICAL_SYMBOL",
            )

        try:
            contract = self.adapter.resolve_active_contract(root)
        except Exception:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_ADAPTER_ERROR,
                discovered=discovered,
                selected_root=root,
                reason="NINJATRADER_CONTRACT_RESOLUTION_ADAPTER_ERROR",
            )
        if not contract.ready or contract.active_contract is None:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_CONTRACT_RESOLUTION,
                discovered=discovered,
                selected_root=root,
                reason=contract.reason_codes[0],
            )

        supervision = self._supervisor.check(
            adapter=self.adapter,
            account_alias=binding.account_alias,
            canonical_symbol=canonical,
            broker_symbol=contract.active_contract,
            now_ms=now_ms,
            policy=supervision_policy,
        )
        supervision_state = supervision.to_dict()
        if not supervision.execution_data_ready:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_SUPERVISION,
                discovered=discovered,
                selected_root=root,
                active_contract=contract.active_contract,
                supervision_state=supervision_state,
                reason=f"NINJATRADER_SUPERVISION_{supervision.status.value}",
            )

        risk_profile = AccountRiskProfile(
            account_alias=binding.account_alias,
            configured_classification=binding.configured_classification,
            classification_source=binding.classification_source,
            detected_classification=discovered.detected_classification,
            prop_limits=binding.prop_limits,
        )
        managed = ManagedBrokerAccount(
            account_alias=binding.account_alias,
            adapter=self.adapter,
            symbol_map=BrokerSymbolMap(
                broker_type=self.adapter.broker_type,
                mapping={canonical: contract.active_contract},
            ),
            risk_fraction=binding.risk_fraction,
            enabled=True,
            max_risk_cash=binding.max_risk_cash,
        )
        phase23 = self._phase23.prepare(
            intent=intent,
            accounts=(
                ClassifiedManagedAccount(
                    managed_account=managed,
                    risk_profile=risk_profile,
                ),
            ),
            policy=OrchestrationPolicy.BEST_EFFORT,
        )
        phase23_account = phase23.accounts[0].to_dict()
        if not phase23.accounts[0].preparation_ready:
            return self._blocked(
                binding=binding,
                canonical=canonical,
                status=Phase24AccountStatus.BLOCKED_PHASE23,
                discovered=discovered,
                selected_root=root,
                active_contract=contract.active_contract,
                supervision_state=supervision_state,
                phase23_account_plan=phase23_account,
                reason="PHASE23_ACCOUNT_RISK_OR_MULTI_ACCOUNT_PREPARATION_BLOCKED",
            )

        return Phase24AccountPlan(
            account_alias=binding.account_alias,
            masked_account=discovered.masked_account,
            provider=discovered.provider,
            status=Phase24AccountStatus.READY,
            canonical_symbol=canonical,
            selected_root=root,
            active_contract=contract.active_contract,
            detected_classification=(
                discovered.detected_classification.value
                if discovered.detected_classification is not None
                else None
            ),
            classification_metadata_verified=discovered.classification_metadata_verified,
            supervision_state=supervision_state,
            phase23_account_plan=phase23_account,
            preparation_ready=True,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(
                *contract.reason_codes,
                "NINJATRADER_ACCOUNT_DATA_PASSED_PHASE22_SUPERVISION",
                "PHASE23_PERSONAL_OR_PROP_RISK_BASE_APPLIED_PER_ACCOUNT",
                "RAW_CONTRACT_COUNT_NOT_COPIED_BETWEEN_ACCOUNTS",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE24",
            ),
        )

    @staticmethod
    def _blocked(
        *,
        binding: NinjaTraderAccountBinding,
        canonical: str,
        status: Phase24AccountStatus,
        discovered: NinjaTraderDiscoveredAccount | None,
        reason: str,
        selected_root: str | None = None,
        active_contract: str | None = None,
        supervision_state: dict[str, Any] | None = None,
        phase23_account_plan: dict[str, Any] | None = None,
    ) -> Phase24AccountPlan:
        return Phase24AccountPlan(
            account_alias=binding.account_alias,
            masked_account=(discovered.masked_account if discovered is not None else None),
            provider=(discovered.provider if discovered is not None else None),
            status=status,
            canonical_symbol=canonical,
            selected_root=selected_root,
            active_contract=active_contract,
            detected_classification=(
                discovered.detected_classification.value
                if discovered is not None and discovered.detected_classification is not None
                else None
            ),
            classification_metadata_verified=(
                discovered.classification_metadata_verified if discovered is not None else False
            ),
            supervision_state=supervision_state,
            phase23_account_plan=phase23_account_plan,
            preparation_ready=False,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE24"),
        )
