"""Phase 29: provider-specific prop compliance and rule-cache provenance.

Phase 29 wraps the Phase 28 dynamic-risk pipeline with one final deterministic
compliance firewall. It consumes additional current performance metrics for rules
such as consistency/scaling and refuses to invent provider formulas. Researched
rule snapshots may also be persisted through an explicitly configured safe cache.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tradingagents.brokers.account_risk import AccountClassification, PropFirmRiskLimits
from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent
from tradingagents.brokers.prop_compliance import (
    PropFirmComplianceEngine,
    PropFirmComplianceStatus,
    PropFirmLiveMetrics,
)
from tradingagents.brokers.prop_rule_cache import (
    InMemoryPropFirmRuleCache,
    PropFirmRuleCache,
    PropFirmRuleCacheEntry,
    PropFirmRuleCacheKey,
)
from tradingagents.brokers.prop_rule_research import PropFirmRuleSnapshot
from tradingagents.brokers.supervision import BrokerSupervisionPolicy

from .multi_account import MultiAccountBatchStatus
from .phase26 import NinjaTraderAccountRuleProfile, NinjaTraderTradeRuleContext
from .phase27 import PropFirmResearchHint
from .phase28 import LondresPhase28DynamicPropRiskEngine


class Phase29AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_PHASE28 = "BLOCKED_PHASE28"
    BLOCKED_RULE_SNAPSHOT = "BLOCKED_RULE_SNAPSHOT"
    BLOCKED_PROVIDER_PROGRAM_MISMATCH = "BLOCKED_PROVIDER_PROGRAM_MISMATCH"
    BLOCKED_MISSING_PERFORMANCE_METRICS = "BLOCKED_MISSING_PERFORMANCE_METRICS"
    BLOCKED_PROP_LIMIT_BREACH = "BLOCKED_PROP_LIMIT_BREACH"
    BLOCKED_UNSUPPORTED_REQUIRED_FORMULA = "BLOCKED_UNSUPPORTED_REQUIRED_FORMULA"
    BLOCKED_LIVE_COMPLIANCE = "BLOCKED_LIVE_COMPLIANCE"


@dataclass(frozen=True)
class Phase29AccountPlan:
    account_alias: str
    status: Phase29AccountStatus
    classification: str
    provider_id: str | None
    rule_cache_state: dict[str, Any] | None
    performance_metrics: dict[str, Any] | None
    compliance_state: dict[str, Any] | None
    phase28_account_plan: dict[str, Any] | None
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
class Phase29MultiAccountPlan:
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
            "phase": "LONDRES_PHASE29_PROVIDER_SPECIFIC_PROP_COMPLIANCE",
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
            "formula_policy": "EXACT_PROVIDER_FORMULA_REQUIRED_NO_GUESSING",
            "rule_cache_policy": "VALIDATED_SNAPSHOT_PROVENANCE_ONLY",
            "nominal_prop_account_size_used_for_sizing": False,
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase29PropComplianceEngine:
    def __init__(
        self,
        *,
        phase28: LondresPhase28DynamicPropRiskEngine,
        compliance: PropFirmComplianceEngine,
        performance_metrics_max_age_ms: int,
        rule_cache_ttl_ms: int,
        rule_cache: PropFirmRuleCache | None = None,
    ) -> None:
        if performance_metrics_max_age_ms <= 0:
            raise ValueError("performance_metrics_max_age_ms must be > 0")
        if rule_cache_ttl_ms <= 0:
            raise ValueError("rule_cache_ttl_ms must be > 0")
        self._phase28 = phase28
        self._compliance = compliance
        self._metrics_max_age_ms = performance_metrics_max_age_ms
        self._cache_ttl_ms = rule_cache_ttl_ms
        self._cache = rule_cache or InMemoryPropFirmRuleCache()

    def prepare(
        self,
        *,
        intent: TradeIntent,
        profiles: list[NinjaTraderAccountRuleProfile] | tuple[NinjaTraderAccountRuleProfile, ...],
        performance_metrics: list[PropFirmLiveMetrics] | tuple[PropFirmLiveMetrics, ...],
        rule_context: NinjaTraderTradeRuleContext,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        research_hints: tuple[PropFirmResearchHint, ...] = (),
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict[str, Any]:
        metrics_by_alias = {item.account_alias: item for item in performance_metrics}
        if len(metrics_by_alias) != len(performance_metrics):
            raise ValueError("performance_metrics require unique account_alias values")

        phase28 = self._phase28.prepare(
            intent=intent,
            profiles=profiles,
            rule_context=rule_context,
            now_ms=now_ms,
            supervision_policy=supervision_policy,
            research_hints=research_hints,
            policy=OrchestrationPolicy.BEST_EFFORT,
        )
        phase28_by_alias = {item["account_alias"]: item for item in phase28["accounts"]}

        plans: list[Phase29AccountPlan] = []
        for profile in profiles:
            p28 = phase28_by_alias.get(profile.account_alias)
            if not profile.enabled:
                plans.append(
                    self._plan(
                        profile=profile,
                        status=Phase29AccountStatus.SKIPPED_DISABLED,
                        classification="NOT_EVALUATED",
                        p28=p28,
                        reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
                    )
                )
                continue

            classification = str((p28 or {}).get("classification") or "UNKNOWN")
            if classification == AccountClassification.PERSONAL.value:
                if p28 and p28.get("preparation_ready"):
                    plans.append(
                        self._plan(
                            profile=profile,
                            status=Phase29AccountStatus.READY,
                            classification=classification,
                            p28=p28,
                            ready=True,
                            reason="PERSONAL_ACCOUNT_BYPASSES_PROP_PROVIDER_FORMULA_GATE",
                        )
                    )
                else:
                    plans.append(
                        self._plan(
                            profile=profile,
                            status=Phase29AccountStatus.BLOCKED_PHASE28,
                            classification=classification,
                            p28=p28,
                            reason="PERSONAL_ACCOUNT_DID_NOT_PASS_PHASE28",
                        )
                    )
                continue

            snapshot = _snapshot_from_phase28(p28)
            if snapshot is None:
                plans.append(
                    self._plan(
                        profile=profile,
                        status=Phase29AccountStatus.BLOCKED_RULE_SNAPSHOT,
                        classification=classification,
                        p28=p28,
                        reason="VERIFIED_PROP_RULE_SNAPSHOT_UNAVAILABLE_FROM_PHASE28_PHASE27_CONTEXT",
                    )
                )
                continue

            mismatch = _program_mismatch(p28, snapshot)
            if mismatch:
                plans.append(
                    self._plan(
                        profile=profile,
                        status=Phase29AccountStatus.BLOCKED_PROVIDER_PROGRAM_MISMATCH,
                        classification=classification,
                        provider_id=snapshot.provider_id,
                        p28=p28,
                        reason=mismatch,
                    )
                )
                continue

            cache_state = self._cache_snapshot(p28=p28, snapshot=snapshot, now_ms=now_ms)
            metrics = metrics_by_alias.get(profile.account_alias)
            if metrics is None:
                plans.append(
                    self._plan(
                        profile=profile,
                        status=Phase29AccountStatus.BLOCKED_MISSING_PERFORMANCE_METRICS,
                        classification=classification,
                        provider_id=snapshot.provider_id,
                        cache_state=cache_state,
                        p28=p28,
                        reason="PROP_ACCOUNT_REQUIRES_CURRENT_PERFORMANCE_METRICS_FOR_PHASE29",
                    )
                )
                continue

            limits = _limits_from_phase28(p28)
            compliance = self._compliance.evaluate(
                snapshot=snapshot,
                metrics=metrics,
                limits=limits,
                now_ms=now_ms,
                metrics_max_age_ms=self._metrics_max_age_ms,
            )
            if not compliance.ready:
                if compliance.status is PropFirmComplianceStatus.UNSUPPORTED_REQUIRED_FORMULA:
                    status = Phase29AccountStatus.BLOCKED_UNSUPPORTED_REQUIRED_FORMULA
                elif compliance.status in {
                    PropFirmComplianceStatus.DAILY_LOSS_BREACH,
                    PropFirmComplianceStatus.DRAWDOWN_BREACH,
                    PropFirmComplianceStatus.CONTRACT_CAP_REACHED,
                }:
                    status = Phase29AccountStatus.BLOCKED_PROP_LIMIT_BREACH
                elif compliance.status is PropFirmComplianceStatus.MISSING_REQUIRED_METRICS:
                    status = Phase29AccountStatus.BLOCKED_MISSING_PERFORMANCE_METRICS
                else:
                    status = Phase29AccountStatus.BLOCKED_LIVE_COMPLIANCE
                plans.append(
                    self._plan(
                        profile=profile,
                        status=status,
                        classification=classification,
                        provider_id=snapshot.provider_id,
                        cache_state=cache_state,
                        metrics=metrics,
                        compliance=compliance.public_dict(),
                        p28=p28,
                        reason=f"PHASE29_PROP_COMPLIANCE_{compliance.status.value}",
                    )
                )
                continue

            if not p28 or not p28.get("preparation_ready"):
                plans.append(
                    self._plan(
                        profile=profile,
                        status=Phase29AccountStatus.BLOCKED_PHASE28,
                        classification=classification,
                        provider_id=snapshot.provider_id,
                        cache_state=cache_state,
                        metrics=metrics,
                        compliance=compliance.public_dict(),
                        p28=p28,
                        reason="PROVIDER_COMPLIANCE_PASSED_BUT_PHASE28_REMAINS_BLOCKED",
                    )
                )
                continue

            plans.append(
                self._plan(
                    profile=profile,
                    status=Phase29AccountStatus.READY,
                    classification=classification,
                    provider_id=snapshot.provider_id,
                    cache_state=cache_state,
                    metrics=metrics,
                    compliance=compliance.public_dict(),
                    p28=p28,
                    ready=True,
                    reason="ACCOUNT_PASSED_PHASE29_PROVIDER_SPECIFIC_PROP_COMPLIANCE",
                )
            )

        return self._batch(intent=intent, plans=tuple(plans), policy=policy).to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"prop_firm_compliance_state": context}

    def _cache_snapshot(
        self,
        *,
        p28: dict[str, Any] | None,
        snapshot: PropFirmRuleSnapshot,
        now_ms: int,
    ) -> dict[str, Any]:
        provider_label = str((p28 or {}).get("provider_label") or snapshot.provider_name)
        key = PropFirmRuleCacheKey(
            provider_label=provider_label,
            program_hint=snapshot.program_name,
            account_size_hint=snapshot.account_size,
        )
        entry = PropFirmRuleCacheEntry(
            key=key,
            snapshot=snapshot,
            fetched_at_ms=snapshot.retrieved_at_ms,
            expires_at_ms=max(snapshot.retrieved_at_ms + self._cache_ttl_ms, now_ms + 1),
            source_digest=snapshot.source_digest,
        )
        self._cache.put(entry)
        return entry.public_dict()

    @staticmethod
    def _plan(
        *,
        profile: NinjaTraderAccountRuleProfile,
        status: Phase29AccountStatus,
        classification: str,
        p28: dict[str, Any] | None,
        reason: str,
        provider_id: str | None = None,
        cache_state: dict[str, Any] | None = None,
        metrics: PropFirmLiveMetrics | None = None,
        compliance: dict[str, Any] | None = None,
        ready: bool = False,
    ) -> Phase29AccountPlan:
        return Phase29AccountPlan(
            account_alias=profile.account_alias,
            status=status,
            classification=classification,
            provider_id=provider_id,
            rule_cache_state=cache_state,
            performance_metrics=metrics.public_dict() if metrics is not None else None,
            compliance_state=compliance,
            phase28_account_plan=p28,
            preparation_ready=ready,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29"),
        )

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        plans: tuple[Phase29AccountPlan, ...],
        policy: OrchestrationPolicy,
    ) -> Phase29MultiAccountPlan:
        skipped = sum(item.status is Phase29AccountStatus.SKIPPED_DISABLED for item in plans)
        enabled = len(plans) - skipped
        ready = sum(item.preparation_ready for item in plans)
        blocked = enabled - ready
        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_ACCOUNTS_AVAILABLE_FOR_PHASE29",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            status = MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_PASS_PHASE29",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29",
            )
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = ("ALL_ENABLED_ACCOUNTS_PASSED_PHASE29", "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29")
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_PROP_ACCOUNTS_THAT_FAIL_PROVIDER_COMPLIANCE",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29",
            )
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("NO_ENABLED_ACCOUNT_PASSED_PHASE29", "NO_BROKER_ORDER_SUBMISSION_IN_PHASE29")
        return Phase29MultiAccountPlan(
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


def _snapshot_from_phase28(plan: dict[str, Any] | None) -> PropFirmRuleSnapshot | None:
    if not plan:
        return None
    p27 = plan.get("phase27_account_plan")
    if not isinstance(p27, dict):
        return None
    research = p27.get("research_state")
    if not isinstance(research, dict):
        return None
    snapshot = research.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    payload = dict(snapshot)
    payload.pop("source_digest", None)
    payload.pop("research_source_policy", None)
    try:
        return PropFirmRuleSnapshot(**payload)
    except (TypeError, ValueError):
        return None


def _limits_from_phase28(plan: dict[str, Any] | None) -> PropFirmRiskLimits | None:
    if not plan:
        return None
    payload = plan.get("effective_prop_risk_limits")
    if not isinstance(payload, dict):
        return None
    try:
        return PropFirmRiskLimits(
            daily_loss_limit=float(payload["daily_loss_limit"]),
            daily_loss_used=float(payload.get("daily_loss_used", 0.0)),
            remaining_drawdown_buffer=(
                float(payload["remaining_drawdown_buffer"])
                if payload.get("remaining_drawdown_buffer") is not None
                else None
            ),
            nominal_account_size=(
                float(payload["nominal_account_size"])
                if payload.get("nominal_account_size") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _program_mismatch(plan: dict[str, Any] | None, snapshot: PropFirmRuleSnapshot) -> str | None:
    if not plan:
        return None
    telemetry = plan.get("risk_telemetry_state")
    if not isinstance(telemetry, dict):
        return None
    row = telemetry.get("snapshot")
    if not isinstance(row, dict):
        return None
    telemetry_program = str(row.get("program_name") or "").strip().lower()
    telemetry_size = str(row.get("account_size") or "").strip().lower()
    snapshot_program = str(snapshot.program_name or "").strip().lower()
    snapshot_size = str(snapshot.account_size or "").strip().lower()
    if telemetry_program and snapshot_program and telemetry_program != snapshot_program:
        return "PHASE27_RULE_PROGRAM_DOES_NOT_MATCH_VERIFIED_PHASE28_TELEMETRY_PROGRAM"
    if telemetry_size and snapshot_size and telemetry_size != snapshot_size:
        return "PHASE27_RULE_ACCOUNT_SIZE_DOES_NOT_MATCH_VERIFIED_PHASE28_TELEMETRY_ACCOUNT_SIZE"
    return None
