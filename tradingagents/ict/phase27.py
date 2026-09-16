"""Phase 27: identify a prop-firm provider, research current official rules, adapt safely.

Phase 27 wraps Phase 26. Account classification remains per-account and is not
guessed from a provider name. Once an account is verified as PROP_FIRM, Phase 27
identifies the firm from provider metadata, searches current official domains,
extracts a structured rule snapshot, and automatically *tightens* the account
policy. It never silently relaxes a user-configured restriction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.account_risk import AccountClassification
from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent
from tradingagents.brokers.prop_rule_research import (
    PropFirmRuleResearchEngine,
    PropFirmRuleResearchRequest,
    PropFirmRuleResearchResult,
    PropFirmRuleSnapshot,
)
from tradingagents.brokers.supervision import BrokerSupervisionPolicy

from .multi_account import MultiAccountBatchStatus
from .phase24 import LondresPhase24NinjaTraderReadOnlyEngine
from .phase26 import (
    LondresPhase26NinjaTraderAccountPolicyEngine,
    NinjaTraderAccountRuleProfile,
    NinjaTraderTradeRuleContext,
)


class Phase27AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_ACCOUNT_NOT_DISCOVERED = "BLOCKED_ACCOUNT_NOT_DISCOVERED"
    BLOCKED_ACCOUNT_CLASSIFICATION = "BLOCKED_ACCOUNT_CLASSIFICATION"
    BLOCKED_PROVIDER_METADATA = "BLOCKED_PROVIDER_METADATA"
    BLOCKED_RULE_RESEARCH = "BLOCKED_RULE_RESEARCH"
    BLOCKED_RULE_CONFLICT = "BLOCKED_RULE_CONFLICT"
    BLOCKED_REQUIRED_PROP_RISK_DATA = "BLOCKED_REQUIRED_PROP_RISK_DATA"
    BLOCKED_PHASE26 = "BLOCKED_PHASE26"


@dataclass(frozen=True)
class PropFirmResearchHint:
    account_alias: str
    program_hint: str | None = None
    account_size_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")


@dataclass(frozen=True)
class PropFirmRuleRefreshPolicy:
    rules_max_age_ms: int
    refresh_on_each_prepare: bool = False

    def __post_init__(self) -> None:
        if self.rules_max_age_ms <= 0:
            raise ValueError("rules_max_age_ms must be > 0")


@dataclass(frozen=True)
class Phase27AccountPlan:
    account_alias: str
    status: Phase27AccountStatus
    classification: str
    provider_label: str | None
    provider_id: str | None
    research_state: dict[str, Any] | None
    original_policy: dict[str, Any]
    adapted_policy: dict[str, Any] | None
    policy_adjustments: tuple[str, ...]
    phase26_account_plan: dict[str, Any] | None
    preparation_ready: bool
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
class Phase27MultiAccountPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase27AccountPlan, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    batch_ready_for_future_execution: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE27_PROP_FIRM_OFFICIAL_RULE_RESEARCH_AND_ADAPTATION",
            "trade_id": self.trade_id,
            "canonical_symbol": self.canonical_symbol,
            "policy": self.policy.value,
            "status": self.status.value,
            "accounts": [item.to_dict() for item in self.accounts],
            "enabled_accounts": self.enabled_accounts,
            "ready_accounts": self.ready_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "batch_ready_for_future_execution": self.batch_ready_for_future_execution,
            "rule_adaptation_mode": "OFFICIAL_SOURCES_AUTO_TIGHTEN_NEVER_AUTO_RELAX",
            "account_classification_mode": "PER_ACCOUNT_NO_PROVIDER_NAME_GUESSING",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase27PropFirmRuleResearchEngine:
    """Research current official prop rules and tighten Phase 26 profiles."""

    def __init__(
        self,
        *,
        phase24: LondresPhase24NinjaTraderReadOnlyEngine,
        phase26: LondresPhase26NinjaTraderAccountPolicyEngine,
        research: PropFirmRuleResearchEngine,
        refresh_policy: PropFirmRuleRefreshPolicy,
    ) -> None:
        self._phase24 = phase24
        self._phase26 = phase26
        self._research = research
        self._refresh_policy = refresh_policy
        self._cache: dict[tuple[str, str | None, str | None], PropFirmRuleResearchResult] = {}

    def prepare(
        self,
        *,
        intent: TradeIntent,
        profiles: list[NinjaTraderAccountRuleProfile] | tuple[NinjaTraderAccountRuleProfile, ...],
        rule_context: NinjaTraderTradeRuleContext,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        research_hints: tuple[PropFirmResearchHint, ...] = (),
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict[str, Any]:
        hints = {item.account_alias: item for item in research_hints}
        if len(hints) != len(research_hints):
            raise ValueError("research_hints require unique account_alias values")

        discovery = self._phase24.discover()
        discovered = {item["account_alias"]: item for item in discovery["accounts"]}

        preblocked: dict[str, Phase27AccountPlan] = {}
        adapted: list[NinjaTraderAccountRuleProfile] = []
        research_by_alias: dict[str, PropFirmRuleResearchResult | None] = {}
        adjustments_by_alias: dict[str, tuple[str, ...]] = {}

        for profile in profiles:
            if not profile.enabled:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.SKIPPED_DISABLED,
                    classification="NOT_EVALUATED",
                    reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
                )
                continue

            account = discovered.get(profile.account_alias)
            if account is None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_ACCOUNT_NOT_DISCOVERED,
                    classification="UNKNOWN",
                    reason="NINJATRADER_ACCOUNT_NOT_DISCOVERED_FOR_PROP_RULE_RESEARCH",
                )
                continue

            classification, conflict = self._classification(profile=profile, discovered=account)
            if conflict or classification is AccountClassification.UNKNOWN:
                provider_label = str(account.get("provider") or "").strip() or None
                research_result = None
                if provider_label:
                    hint = hints.get(profile.account_alias)
                    research_result = self._research_for(
                        provider_label=provider_label,
                        program_hint=hint.program_hint if hint else None,
                        account_size_hint=hint.account_size_hint if hint else None,
                        now_ms=now_ms,
                    )
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_ACCOUNT_CLASSIFICATION,
                    classification=("CONFLICT" if conflict else "UNKNOWN"),
                    provider_label=provider_label,
                    research=research_result,
                    reason=(
                        "ACCOUNT_CLASSIFICATION_CONFLICT_REQUIRES_RESOLUTION"
                        if conflict
                        else "ACCOUNT_MUST_BE_VERIFIED_PERSONAL_OR_PROP_BEFORE_PROVIDER_RULES_CAN_CONTROL_SIZING"
                    ),
                )
                continue

            if classification is AccountClassification.PERSONAL:
                adapted.append(profile)
                research_by_alias[profile.account_alias] = None
                adjustments_by_alias[profile.account_alias] = (
                    "PERSONAL_ACCOUNT_DOES_NOT_USE_PROP_FIRM_RULE_RESEARCH",
                )
                continue

            provider_label = str(account.get("provider") or "").strip()
            if not provider_label:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_PROVIDER_METADATA,
                    classification=classification.value,
                    reason="PROP_ACCOUNT_REQUIRES_PROVIDER_METADATA_FOR_RULE_RESEARCH",
                )
                continue

            hint = hints.get(profile.account_alias)
            research_result = self._research_for(
                provider_label=provider_label,
                program_hint=hint.program_hint if hint else None,
                account_size_hint=hint.account_size_hint if hint else None,
                now_ms=now_ms,
            )
            if not research_result.ready or research_result.snapshot is None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_RULE_RESEARCH,
                    classification=classification.value,
                    provider_label=provider_label,
                    research=research_result,
                    reason=f"PROP_RULE_RESEARCH_{research_result.status.value}",
                )
                continue

            snapshot = research_result.snapshot
            if snapshot.drawdown_rule_present is True and (
                profile.prop_limits is None
                or profile.prop_limits.remaining_drawdown_buffer is None
            ):
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_REQUIRED_PROP_RISK_DATA,
                    classification=classification.value,
                    provider_label=provider_label,
                    provider_id=research_result.provider_id,
                    research=research_result,
                    reason="OFFICIAL_RULES_REQUIRE_DRAWDOWN_CONTROL_BUT_CURRENT_REMAINING_DRAWDOWN_BUFFER_IS_UNAVAILABLE",
                )
                continue
            if snapshot.daily_loss_rule_present is True and profile.prop_limits is None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_REQUIRED_PROP_RISK_DATA,
                    classification=classification.value,
                    provider_label=provider_label,
                    provider_id=research_result.provider_id,
                    research=research_result,
                    reason="OFFICIAL_RULES_REQUIRE_DAILY_LOSS_DATA_BUT_PROP_RISK_LIMITS_ARE_UNAVAILABLE",
                )
                continue

            adapted_profile, adjustment_codes, conflict_reason = self._tighten(profile, snapshot)
            if conflict_reason is not None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase27AccountStatus.BLOCKED_RULE_CONFLICT,
                    classification=classification.value,
                    provider_label=provider_label,
                    provider_id=research_result.provider_id,
                    research=research_result,
                    reason=conflict_reason,
                )
                continue

            adapted.append(adapted_profile)
            research_by_alias[profile.account_alias] = research_result
            adjustments_by_alias[profile.account_alias] = adjustment_codes

        phase26_accounts: dict[str, dict[str, Any]] = {}
        if adapted:
            phase26_result = self._phase26.prepare(
                intent=intent,
                profiles=tuple(adapted),
                rule_context=rule_context,
                now_ms=now_ms,
                supervision_policy=supervision_policy,
                policy=OrchestrationPolicy.BEST_EFFORT,
            )
            phase26_accounts = {
                item["account_alias"]: item for item in phase26_result["accounts"]
            }

        profile_by_alias = {profile.account_alias: profile for profile in profiles}
        adapted_by_alias = {profile.account_alias: profile for profile in adapted}
        plans: list[Phase27AccountPlan] = []
        for profile in profiles:
            if profile.account_alias in preblocked:
                plans.append(preblocked[profile.account_alias])
                continue
            phase26_account = phase26_accounts.get(profile.account_alias)
            research_result = research_by_alias.get(profile.account_alias)
            adapted_profile = adapted_by_alias[profile.account_alias]
            if not phase26_account or not phase26_account.get("preparation_ready"):
                plans.append(
                    Phase27AccountPlan(
                        account_alias=profile.account_alias,
                        status=Phase27AccountStatus.BLOCKED_PHASE26,
                        classification=self._classification(
                            profile=profile,
                            discovered=discovered[profile.account_alias],
                        )[0].value,
                        provider_label=str(discovered[profile.account_alias].get("provider") or "") or None,
                        provider_id=(research_result.provider_id if research_result else None),
                        research_state=(research_result.public_dict() if research_result else None),
                        original_policy=profile.public_dict(),
                        adapted_policy=adapted_profile.public_dict(),
                        policy_adjustments=adjustments_by_alias.get(profile.account_alias, ()),
                        phase26_account_plan=phase26_account,
                        preparation_ready=False,
                        reason_codes=(
                            "PHASE26_BLOCKED_AFTER_PROP_RULE_ADAPTATION",
                            "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27",
                        ),
                    )
                )
                continue

            plans.append(
                Phase27AccountPlan(
                    account_alias=profile.account_alias,
                    status=Phase27AccountStatus.READY,
                    classification=self._classification(
                        profile=profile,
                        discovered=discovered[profile.account_alias],
                    )[0].value,
                    provider_label=str(discovered[profile.account_alias].get("provider") or "") or None,
                    provider_id=(research_result.provider_id if research_result else None),
                    research_state=(research_result.public_dict() if research_result else None),
                    original_policy=profile_by_alias[profile.account_alias].public_dict(),
                    adapted_policy=adapted_profile.public_dict(),
                    policy_adjustments=adjustments_by_alias.get(profile.account_alias, ()),
                    phase26_account_plan=phase26_account,
                    preparation_ready=True,
                    reason_codes=(
                        "ACCOUNT_POLICY_PASSED_PHASE27_OFFICIAL_RULE_GATE",
                        "PROP_RULES_CAN_ONLY_TIGHTEN_EXISTING_POLICY_AUTOMATICALLY",
                        "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27",
                    ),
                )
            )

        return self._batch(intent=intent, plans=tuple(plans), policy=policy).to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"prop_firm_rule_research_state": context}

    def _research_for(
        self,
        *,
        provider_label: str,
        program_hint: str | None,
        account_size_hint: str | None,
        now_ms: int,
    ) -> PropFirmRuleResearchResult:
        key = (provider_label.strip().lower(), program_hint, account_size_hint)
        cached = self._cache.get(key)
        if (
            cached is not None
            and cached.snapshot is not None
            and not self._refresh_policy.refresh_on_each_prepare
            and now_ms - cached.snapshot.retrieved_at_ms <= self._refresh_policy.rules_max_age_ms
        ):
            return cached

        result = self._research.research(
            request=PropFirmRuleResearchRequest(
                provider_label=provider_label,
                program_hint=program_hint,
                account_size_hint=account_size_hint,
            ),
            now_ms=now_ms,
        )
        if result.ready:
            self._cache[key] = result
        return result

    @staticmethod
    def _classification(
        *,
        profile: NinjaTraderAccountRuleProfile,
        discovered: dict[str, Any],
    ) -> tuple[AccountClassification, bool]:
        configured = profile.configured_classification
        detected_raw = discovered.get("detected_classification")
        detected = AccountClassification(detected_raw) if detected_raw in {item.value for item in AccountClassification} else None
        detected_verified = bool(discovered.get("classification_metadata_verified"))
        if not detected_verified:
            detected = None

        configured_known = configured not in (None, AccountClassification.UNKNOWN)
        detected_known = detected not in (None, AccountClassification.UNKNOWN)
        if configured_known and detected_known and configured != detected:
            return AccountClassification.UNKNOWN, True
        if detected_known:
            return detected or AccountClassification.UNKNOWN, False
        if configured_known:
            return configured or AccountClassification.UNKNOWN, False
        return AccountClassification.UNKNOWN, False

    @staticmethod
    def _tighten(
        profile: NinjaTraderAccountRuleProfile,
        snapshot: PropFirmRuleSnapshot,
    ) -> tuple[NinjaTraderAccountRuleProfile, tuple[str, ...], str | None]:
        selected_roots = tuple(profile.allowed_roots)
        adjustments: list[str] = []

        if snapshot.allowed_roots is not None:
            researched = set(snapshot.allowed_roots)
            selected_roots = tuple(root for root in selected_roots if root in researched)
            if not selected_roots:
                return profile, (), "OFFICIAL_PROVIDER_RULES_ALLOW_NONE_OF_THE_ACCOUNT_CONFIGURED_ROOTS"
            if selected_roots != profile.allowed_roots:
                adjustments.append("ALLOWED_ROOTS_TIGHTENED_FROM_OFFICIAL_RULES")

        selected_for_trade = profile.root_map.get(next(iter(profile.root_map))) if len(profile.root_map) == 1 else None
        if selected_for_trade is not None and selected_for_trade not in selected_roots:
            return profile, (), "SELECTED_FUTURES_ROOT_IS_NOT_ALLOWED_BY_CURRENT_OFFICIAL_PROVIDER_RULES"

        caps = dict(profile.max_contracts_by_root)
        for root, researched_cap in (snapshot.max_contracts_by_root or {}).items():
            current = caps.get(root)
            if current is None:
                caps[root] = researched_cap
                adjustments.append(f"MAX_CONTRACTS_{root}_SET_FROM_OFFICIAL_RULES")
            elif researched_cap < current:
                caps[root] = researched_cap
                adjustments.append(f"MAX_CONTRACTS_{root}_TIGHTENED_FROM_OFFICIAL_RULES")

        sessions = profile.allowed_sessions
        if snapshot.allowed_sessions is not None:
            if sessions is None:
                sessions = snapshot.allowed_sessions
                adjustments.append("ALLOWED_SESSIONS_SET_FROM_OFFICIAL_RULES")
            else:
                intersection = tuple(item for item in sessions if item in set(snapshot.allowed_sessions))
                if not intersection:
                    return profile, (), "CURRENT_ACCOUNT_SESSION_POLICY_CONFLICTS_WITH_OFFICIAL_PROVIDER_RULES"
                if intersection != sessions:
                    sessions = intersection
                    adjustments.append("ALLOWED_SESSIONS_TIGHTENED_FROM_OFFICIAL_RULES")

        def conservative_bool(current: bool | None, researched: bool | None, code: str) -> bool | None:
            if researched is None:
                return current
            if current is False or researched is False:
                if current is not False:
                    adjustments.append(code)
                return False
            if current is None:
                adjustments.append(code)
                return researched
            return current

        news = conservative_bool(
            profile.news_trading_allowed,
            snapshot.news_trading_allowed,
            "NEWS_RULE_ADAPTED_FROM_OFFICIAL_RULES",
        )
        overnight = conservative_bool(
            profile.overnight_holding_allowed,
            snapshot.overnight_holding_allowed,
            "OVERNIGHT_RULE_ADAPTED_FROM_OFFICIAL_RULES",
        )
        weekend = conservative_bool(
            profile.weekend_holding_allowed,
            snapshot.weekend_holding_allowed,
            "WEEKEND_RULE_ADAPTED_FROM_OFFICIAL_RULES",
        )

        consistency_required = profile.consistency_rule_required or snapshot.consistency_rule_required
        scaling_required = profile.scaling_rule_required or snapshot.scaling_rule_required
        if snapshot.consistency_rule_required and not profile.consistency_rule_required:
            adjustments.append("CONSISTENCY_RULE_REQUIREMENT_DISCOVERED_FROM_OFFICIAL_RULES")
        if snapshot.scaling_rule_required and not profile.scaling_rule_required:
            adjustments.append("SCALING_RULE_REQUIREMENT_DISCOVERED_FROM_OFFICIAL_RULES")

        adapted = replace(
            profile,
            allowed_roots=selected_roots,
            max_contracts_by_root=caps,
            allowed_sessions=sessions,
            news_trading_allowed=news,
            overnight_holding_allowed=overnight,
            weekend_holding_allowed=weekend,
            consistency_rule_required=consistency_required,
            scaling_rule_required=scaling_required,
        )
        if not adjustments:
            adjustments.append("CURRENT_ACCOUNT_POLICY_ALREADY_AT_LEAST_AS_STRICT_AS_OFFICIAL_RULES")
        return adapted, tuple(adjustments), None

    @staticmethod
    def _blocked(
        *,
        profile: NinjaTraderAccountRuleProfile,
        status: Phase27AccountStatus,
        classification: str,
        reason: str,
        provider_label: str | None = None,
        provider_id: str | None = None,
        research: PropFirmRuleResearchResult | None = None,
    ) -> Phase27AccountPlan:
        return Phase27AccountPlan(
            account_alias=profile.account_alias,
            status=status,
            classification=classification,
            provider_label=provider_label,
            provider_id=provider_id or (research.provider_id if research else None),
            research_state=research.public_dict() if research is not None else None,
            original_policy=profile.public_dict(),
            adapted_policy=None,
            policy_adjustments=(),
            phase26_account_plan=None,
            preparation_ready=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27"),
        )

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        plans: tuple[Phase27AccountPlan, ...],
        policy: OrchestrationPolicy,
    ) -> Phase27MultiAccountPlan:
        skipped = sum(item.status is Phase27AccountStatus.SKIPPED_DISABLED for item in plans)
        enabled = len(plans) - skipped
        ready = sum(item.preparation_ready for item in plans)
        blocked = enabled - ready

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_ACCOUNTS_AVAILABLE_FOR_PHASE27",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            status = MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_PASS_CURRENT_OFFICIAL_RULE_RESEARCH",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27",
            )
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = (
                "ALL_ENABLED_ACCOUNTS_PASSED_PHASE27",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27",
            )
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_ACCOUNTS_WITH_UNVERIFIED_OR_CONFLICTING_PROP_RULES",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27",
            )
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = (
                "NO_ENABLED_ACCOUNT_PASSED_PHASE27",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27",
            )

        return Phase27MultiAccountPlan(
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
