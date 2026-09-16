"""Phase 26: per-account NinjaTrader futures and prop-firm rule profiles.

Phase 26 sits above Phase 24. It keeps the same Londres trade intent while
applying explicit account-local policy before an account may remain preparation
ready. It never copies raw contract quantity between accounts and never silently
resizes a position that exceeds an account policy cap.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tradingagents.brokers.account_risk import (
    AccountClassification,
    AccountClassificationSource,
    PropFirmRiskLimits,
)
from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent, canonicalize_symbol
from tradingagents.brokers.ninjatrader_futures import NINJATRADER_EQUITY_INDEX_FUTURES
from tradingagents.brokers.supervision import BrokerSupervisionPolicy

from .multi_account import MultiAccountBatchStatus
from .phase24 import LondresPhase24NinjaTraderReadOnlyEngine, NinjaTraderAccountBinding
from .risk_sizing import ALLOWED_RISK_FRACTIONS


class Phase26AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_POLICY_CONFIGURATION = "BLOCKED_POLICY_CONFIGURATION"
    BLOCKED_ROOT_NOT_ALLOWED = "BLOCKED_ROOT_NOT_ALLOWED"
    BLOCKED_RULE_CONTEXT = "BLOCKED_RULE_CONTEXT"
    BLOCKED_RULE_VIOLATION = "BLOCKED_RULE_VIOLATION"
    BLOCKED_UNSUPPORTED_REQUIRED_RULE = "BLOCKED_UNSUPPORTED_REQUIRED_RULE"
    BLOCKED_PHASE24 = "BLOCKED_PHASE24"
    BLOCKED_MAX_CONTRACTS = "BLOCKED_MAX_CONTRACTS"


@dataclass(frozen=True)
class NinjaTraderTradeRuleContext:
    """Facts required to evaluate optional account-specific trading rules.

    A value of ``None`` means the fact is unavailable. If an account policy
    requires that fact, Phase 26 fails closed rather than assuming permission.
    """

    session: str | None = None
    high_impact_news_window: bool | None = None
    will_hold_overnight: bool | None = None
    will_hold_weekend: bool | None = None

    def __post_init__(self) -> None:
        if self.session is not None:
            value = self.session.strip().upper()
            if not value:
                raise ValueError("session cannot be blank when supplied")
            object.__setattr__(self, "session", value)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NinjaTraderAccountRuleProfile:
    """Explicit per-account policy for one NinjaTrader account.

    The profile intentionally duplicates the Phase 24 binding inputs so the
    account's classification, root mapping, risk tier and prop-loss buffers are
    bound to the same policy object as its allowed futures roots and contract
    caps. Nominal prop account size remains metadata only through Phase 23.
    """

    account_alias: str
    root_map: dict[str, str]
    risk_fraction: float
    allowed_roots: tuple[str, ...]
    max_contracts_by_root: dict[str, int]
    configured_classification: AccountClassification | None = None
    classification_source: AccountClassificationSource = AccountClassificationSource.UNKNOWN
    prop_limits: PropFirmRiskLimits | None = None
    enabled: bool = True
    max_risk_cash: float | None = None
    allowed_sessions: tuple[str, ...] | None = None
    news_trading_allowed: bool | None = None
    overnight_holding_allowed: bool | None = None
    weekend_holding_allowed: bool | None = None
    consistency_rule_required: bool = False
    scaling_rule_required: bool = False

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

        normalized_map: dict[str, str] = {}
        for canonical, root in self.root_map.items():
            key = canonicalize_symbol(canonical)
            value = str(root).strip().upper()
            if not value:
                raise ValueError("NinjaTrader futures root cannot be blank")
            normalized_map[key] = value
        object.__setattr__(self, "root_map", normalized_map)

        roots = tuple(dict.fromkeys(str(root).strip().upper() for root in self.allowed_roots))
        if self.enabled and not roots:
            raise ValueError("enabled account requires at least one explicitly allowed futures root")
        if any(not root for root in roots):
            raise ValueError("allowed futures root cannot be blank")
        object.__setattr__(self, "allowed_roots", roots)

        caps: dict[str, int] = {}
        for root, value in self.max_contracts_by_root.items():
            normalized_root = str(root).strip().upper()
            if not normalized_root:
                raise ValueError("max-contract root cannot be blank")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("max contracts must be an explicit positive integer")
            caps[normalized_root] = value
        object.__setattr__(self, "max_contracts_by_root", caps)

        if self.allowed_sessions is not None:
            sessions = tuple(
                dict.fromkeys(str(session).strip().upper() for session in self.allowed_sessions)
            )
            if not sessions or any(not session for session in sessions):
                raise ValueError("allowed_sessions must contain nonblank values")
            object.__setattr__(self, "allowed_sessions", sessions)

    def phase24_binding(self) -> NinjaTraderAccountBinding:
        return NinjaTraderAccountBinding(
            account_alias=self.account_alias,
            root_map=self.root_map,
            risk_fraction=self.risk_fraction,
            configured_classification=self.configured_classification,
            classification_source=self.classification_source,
            prop_limits=self.prop_limits,
            enabled=self.enabled,
            max_risk_cash=self.max_risk_cash,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "account_alias": self.account_alias,
            "enabled": self.enabled,
            "root_map": dict(self.root_map),
            "risk_fraction": self.risk_fraction,
            "configured_classification": (
                self.configured_classification.value
                if self.configured_classification is not None
                else None
            ),
            "classification_source": self.classification_source.value,
            "allowed_roots": list(self.allowed_roots),
            "max_contracts_by_root": dict(self.max_contracts_by_root),
            "max_risk_cash": self.max_risk_cash,
            "allowed_sessions": (
                list(self.allowed_sessions) if self.allowed_sessions is not None else None
            ),
            "news_trading_allowed": self.news_trading_allowed,
            "overnight_holding_allowed": self.overnight_holding_allowed,
            "weekend_holding_allowed": self.weekend_holding_allowed,
            "consistency_rule_required": self.consistency_rule_required,
            "scaling_rule_required": self.scaling_rule_required,
            "nominal_prop_account_size_used_for_sizing": False,
            "account_environment": "HIDDEN_INTERNAL",
        }


@dataclass(frozen=True)
class Phase26AccountPlan:
    account_alias: str
    status: Phase26AccountStatus
    canonical_symbol: str
    selected_root: str | None
    max_contracts: int | None
    prepared_contracts: float | None
    policy_state: dict[str, Any]
    rule_context: dict[str, Any]
    phase24_account_plan: dict[str, Any] | None
    preparation_ready: bool
    order_authorized: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["read_only"] = True
        payload["execution_enabled"] = False
        payload["order_submission_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload


@dataclass(frozen=True)
class Phase26MultiAccountPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase26AccountPlan, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    batch_ready_for_future_execution: bool
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
            "position_copy_mode": "REPLICATE_TRADE_INTENT_NEVER_RAW_CONTRACT_COUNT",
            "account_policy_mode": "EXPLICIT_PER_ACCOUNT_FAIL_CLOSED",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase26NinjaTraderAccountPolicyEngine:
    """Apply per-account futures and prop-firm policy above Phase 24."""

    def __init__(self, phase24: LondresPhase24NinjaTraderReadOnlyEngine) -> None:
        self._phase24 = phase24

    def prepare(
        self,
        *,
        intent: TradeIntent,
        profiles: list[NinjaTraderAccountRuleProfile] | tuple[NinjaTraderAccountRuleProfile, ...],
        rule_context: NinjaTraderTradeRuleContext,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict[str, Any]:
        aliases = [profile.account_alias for profile in profiles]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Phase 26 requires unique account_alias values")

        prechecked: dict[str, Phase26AccountPlan] = {}
        eligible: list[NinjaTraderAccountRuleProfile] = []
        for profile in profiles:
            blocked = self._precheck(intent=intent, profile=profile, context=rule_context)
            if blocked is None:
                eligible.append(profile)
            else:
                prechecked[profile.account_alias] = blocked

        phase24_accounts: dict[str, dict[str, Any]] = {}
        if eligible:
            phase24 = self._phase24.prepare(
                intent=intent,
                bindings=tuple(profile.phase24_binding() for profile in eligible),
                now_ms=now_ms,
                supervision_policy=supervision_policy,
                policy=OrchestrationPolicy.BEST_EFFORT,
            )
            phase24_accounts = {item["account_alias"]: item for item in phase24["accounts"]}

        plans: list[Phase26AccountPlan] = []
        for profile in profiles:
            if profile.account_alias in prechecked:
                plans.append(prechecked[profile.account_alias])
                continue
            phase24_account = phase24_accounts.get(profile.account_alias)
            plans.append(
                self._from_phase24(
                    intent=intent,
                    profile=profile,
                    context=rule_context,
                    phase24_account=phase24_account,
                )
            )

        result = self._batch(intent=intent, plans=tuple(plans), policy=policy)
        payload = result.to_dict()
        payload["phase"] = "LONDRES_PHASE26_NINJATRADER_PER_ACCOUNT_RULE_PROFILES"
        return payload

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"ninjatrader_account_policy_state": context}

    def _precheck(
        self,
        *,
        intent: TradeIntent,
        profile: NinjaTraderAccountRuleProfile,
        context: NinjaTraderTradeRuleContext,
    ) -> Phase26AccountPlan | None:
        canonical = intent.canonical
        policy_state = profile.public_dict()
        rule_state = context.public_dict()
        if not profile.enabled:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.SKIPPED_DISABLED,
                context=context,
                reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
            )

        root = profile.root_map.get(canonical)
        if root is None:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_POLICY_CONFIGURATION,
                context=context,
                reason="EXPLICIT_ACCOUNT_ROOT_MAPPING_REQUIRED",
            )
        if root not in profile.allowed_roots:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_ROOT_NOT_ALLOWED,
                context=context,
                selected_root=root,
                reason="SELECTED_ROOT_NOT_ALLOWED_BY_THIS_ACCOUNT_POLICY",
            )
        spec = NINJATRADER_EQUITY_INDEX_FUTURES.get(root)
        if spec is None or canonicalize_symbol(spec.canonical_symbol) != canonical:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_POLICY_CONFIGURATION,
                context=context,
                selected_root=root,
                reason="ACCOUNT_POLICY_ROOT_DOES_NOT_MATCH_CANONICAL_SYMBOL",
            )
        if profile.max_contracts_by_root.get(root) is None:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_POLICY_CONFIGURATION,
                context=context,
                selected_root=root,
                reason="EXPLICIT_PER_ACCOUNT_MAX_CONTRACTS_REQUIRED_FOR_SELECTED_ROOT",
            )

        if profile.consistency_rule_required:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_UNSUPPORTED_REQUIRED_RULE,
                context=context,
                selected_root=root,
                reason="PROP_CONSISTENCY_RULE_REQUIRED_BUT_NOT_YET_ENCODED",
            )
        if profile.scaling_rule_required:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_UNSUPPORTED_REQUIRED_RULE,
                context=context,
                selected_root=root,
                reason="PROP_SCALING_RULE_REQUIRED_BUT_NOT_YET_ENCODED",
            )

        if profile.allowed_sessions is not None:
            if context.session is None:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_CONTEXT,
                    context=context,
                    selected_root=root,
                    reason="SESSION_RULE_REQUIRES_VERIFIED_CURRENT_SESSION",
                )
            if context.session not in profile.allowed_sessions:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_VIOLATION,
                    context=context,
                    selected_root=root,
                    reason="CURRENT_SESSION_NOT_ALLOWED_BY_ACCOUNT_POLICY",
                )

        if profile.news_trading_allowed is not None:
            if context.high_impact_news_window is None:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_CONTEXT,
                    context=context,
                    selected_root=root,
                    reason="NEWS_RULE_REQUIRES_VERIFIED_NEWS_WINDOW_STATE",
                )
            if context.high_impact_news_window and not profile.news_trading_allowed:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_VIOLATION,
                    context=context,
                    selected_root=root,
                    reason="HIGH_IMPACT_NEWS_TRADING_PROHIBITED_BY_ACCOUNT_POLICY",
                )

        if profile.overnight_holding_allowed is not None:
            if context.will_hold_overnight is None:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_CONTEXT,
                    context=context,
                    selected_root=root,
                    reason="OVERNIGHT_RULE_REQUIRES_VERIFIED_HOLDING_INTENT",
                )
            if context.will_hold_overnight and not profile.overnight_holding_allowed:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_VIOLATION,
                    context=context,
                    selected_root=root,
                    reason="OVERNIGHT_HOLDING_PROHIBITED_BY_ACCOUNT_POLICY",
                )

        if profile.weekend_holding_allowed is not None:
            if context.will_hold_weekend is None:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_CONTEXT,
                    context=context,
                    selected_root=root,
                    reason="WEEKEND_RULE_REQUIRES_VERIFIED_HOLDING_INTENT",
                )
            if context.will_hold_weekend and not profile.weekend_holding_allowed:
                return self._blocked(
                    profile=profile,
                    canonical=canonical,
                    status=Phase26AccountStatus.BLOCKED_RULE_VIOLATION,
                    context=context,
                    selected_root=root,
                    reason="WEEKEND_HOLDING_PROHIBITED_BY_ACCOUNT_POLICY",
                )

        _ = policy_state, rule_state
        return None

    def _from_phase24(
        self,
        *,
        intent: TradeIntent,
        profile: NinjaTraderAccountRuleProfile,
        context: NinjaTraderTradeRuleContext,
        phase24_account: dict[str, Any] | None,
    ) -> Phase26AccountPlan:
        canonical = intent.canonical
        root = profile.root_map.get(canonical)
        cap = profile.max_contracts_by_root.get(root or "")
        if phase24_account is None:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_PHASE24,
                context=context,
                selected_root=root,
                reason="PHASE24_ACCOUNT_RESULT_UNAVAILABLE",
            )
        if not phase24_account.get("preparation_ready"):
            reason = "PHASE24_ACCOUNT_PREPARATION_BLOCKED"
            phase24_reasons = phase24_account.get("reason_codes") or []
            if phase24_reasons:
                reason = str(phase24_reasons[0])
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_PHASE24,
                context=context,
                selected_root=root,
                phase24_account_plan=phase24_account,
                reason=reason,
            )

        prepared = self._prepared_contracts(phase24_account)
        if prepared is None or not math.isfinite(prepared) or prepared <= 0:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_PHASE24,
                context=context,
                selected_root=root,
                phase24_account_plan=phase24_account,
                reason="PHASE24_READY_ACCOUNT_MISSING_VALID_PREPARED_CONTRACT_QUANTITY",
            )
        if cap is None:
            return self._blocked(
                profile=profile,
                canonical=canonical,
                status=Phase26AccountStatus.BLOCKED_POLICY_CONFIGURATION,
                context=context,
                selected_root=root,
                phase24_account_plan=phase24_account,
                reason="PER_ACCOUNT_CONTRACT_CAP_UNAVAILABLE",
            )
        if prepared > float(cap) + 1e-12:
            return Phase26AccountPlan(
                account_alias=profile.account_alias,
                status=Phase26AccountStatus.BLOCKED_MAX_CONTRACTS,
                canonical_symbol=canonical,
                selected_root=root,
                max_contracts=cap,
                prepared_contracts=prepared,
                policy_state=profile.public_dict(),
                rule_context=context.public_dict(),
                phase24_account_plan=phase24_account,
                preparation_ready=False,
                order_authorized=False,
                broker_order_placed=False,
                reason_codes=(
                    "RISK_SIZED_CONTRACT_QUANTITY_EXCEEDS_EXPLICIT_ACCOUNT_CAP",
                    "PHASE26_DOES_NOT_SILENTLY_RESIZE_TO_ACCOUNT_MAXIMUM",
                    "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26",
                ),
            )

        return Phase26AccountPlan(
            account_alias=profile.account_alias,
            status=Phase26AccountStatus.READY,
            canonical_symbol=canonical,
            selected_root=root,
            max_contracts=cap,
            prepared_contracts=prepared,
            policy_state=profile.public_dict(),
            rule_context=context.public_dict(),
            phase24_account_plan=phase24_account,
            preparation_ready=True,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(
                "PHASE24_READ_ONLY_ACCOUNT_PREPARATION_READY",
                "EXPLICIT_PER_ACCOUNT_ROOT_AND_CONTRACT_CAP_VALIDATED",
                "OPTIONAL_ACCOUNT_RULES_VALIDATED_WITHOUT_GUESSING",
                "RAW_CONTRACT_COUNT_NOT_COPIED_BETWEEN_ACCOUNTS",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26",
            ),
        )

    @staticmethod
    def _prepared_contracts(phase24_account: dict[str, Any]) -> float | None:
        phase23 = phase24_account.get("phase23_account_plan")
        if not isinstance(phase23, dict):
            return None
        phase20 = phase23.get("phase20_account_plan")
        if not isinstance(phase20, dict):
            return None
        value = phase20.get("prepared_volume")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _blocked(
        *,
        profile: NinjaTraderAccountRuleProfile,
        canonical: str,
        status: Phase26AccountStatus,
        context: NinjaTraderTradeRuleContext,
        reason: str,
        selected_root: str | None = None,
        phase24_account_plan: dict[str, Any] | None = None,
    ) -> Phase26AccountPlan:
        cap = profile.max_contracts_by_root.get(selected_root or "")
        return Phase26AccountPlan(
            account_alias=profile.account_alias,
            status=status,
            canonical_symbol=canonical,
            selected_root=selected_root,
            max_contracts=cap,
            prepared_contracts=None,
            policy_state=profile.public_dict(),
            rule_context=context.public_dict(),
            phase24_account_plan=phase24_account_plan,
            preparation_ready=False,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26"),
        )

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        plans: tuple[Phase26AccountPlan, ...],
        policy: OrchestrationPolicy,
    ) -> Phase26MultiAccountPlan:
        skipped = sum(plan.status is Phase26AccountStatus.SKIPPED_DISABLED for plan in plans)
        enabled = len(plans) - skipped
        ready = sum(plan.preparation_ready for plan in plans)
        blocked = enabled - ready

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_NINJATRADER_ACCOUNTS_FOR_PHASE26_POLICY",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            status = MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_PASS_PHASE26",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26",
            )
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = (
                "ALL_ENABLED_ACCOUNTS_PASSED_PER_ACCOUNT_RULE_POLICY",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26",
            )
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_ACCOUNTS_BLOCKED_BY_PHASE26_POLICY",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26",
            )
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = (
                "NO_ENABLED_ACCOUNT_PASSED_PHASE26_POLICY",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE26",
            )

        return Phase26MultiAccountPlan(
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
            reason_codes=reasons,
        )
