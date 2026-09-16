"""Phase 28: fresh verified prop-risk telemetry before official-rule preparation.

Phase 27 can discover current official prop-firm rules, but those rules still
need current account-specific daily-loss and drawdown state. Phase 28 supplies
that missing dynamic layer. Personal accounts bypass prop telemetry. Verified
prop accounts must receive a fresh provider/account telemetry snapshot before
Phase 27/26/24/23 may size them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.account_risk import AccountClassification
from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent
from tradingagents.brokers.prop_risk_telemetry import (
    PropRiskTelemetryPolicy,
    PropRiskTelemetrySource,
    PropRiskTelemetryValidationResult,
    PropRiskTelemetryValidator,
)
from tradingagents.brokers.prop_rule_research import (
    PropFirmIdentityStatus,
    PropFirmProviderRegistry,
)
from tradingagents.brokers.supervision import BrokerSupervisionPolicy

from .multi_account import MultiAccountBatchStatus
from .phase24 import LondresPhase24NinjaTraderReadOnlyEngine
from .phase26 import NinjaTraderAccountRuleProfile, NinjaTraderTradeRuleContext
from .phase27 import (
    LondresPhase27PropFirmRuleResearchEngine,
    PropFirmResearchHint,
)


class Phase28AccountStatus(str, Enum):
    READY = "READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_ACCOUNT_NOT_DISCOVERED = "BLOCKED_ACCOUNT_NOT_DISCOVERED"
    BLOCKED_ACCOUNT_CLASSIFICATION = "BLOCKED_ACCOUNT_CLASSIFICATION"
    BLOCKED_PROVIDER_METADATA = "BLOCKED_PROVIDER_METADATA"
    BLOCKED_PROVIDER_IDENTITY = "BLOCKED_PROVIDER_IDENTITY"
    BLOCKED_RISK_TELEMETRY = "BLOCKED_RISK_TELEMETRY"
    BLOCKED_PHASE27 = "BLOCKED_PHASE27"


@dataclass(frozen=True)
class Phase28AccountPlan:
    account_alias: str
    status: Phase28AccountStatus
    classification: str
    provider_label: str | None
    provider_id: str | None
    risk_telemetry_state: dict[str, Any] | None
    effective_prop_risk_limits: dict[str, Any] | None
    phase27_account_plan: dict[str, Any] | None
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
class Phase28MultiAccountPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase28AccountPlan, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    batch_ready_for_future_execution: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE28_DYNAMIC_PROP_RISK_TELEMETRY",
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
            "prop_risk_mode": "FRESH_VERIFIED_PROVIDER_TELEMETRY_PER_ACCOUNT",
            "generic_pnl_inference_allowed": False,
            "nominal_prop_account_size_used_for_sizing": False,
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase28DynamicPropRiskEngine:
    """Inject fresh account-local prop risk state, then run Phase 27."""

    def __init__(
        self,
        *,
        phase24: LondresPhase24NinjaTraderReadOnlyEngine,
        phase27: LondresPhase27PropFirmRuleResearchEngine,
        provider_registry: PropFirmProviderRegistry,
        telemetry_source: PropRiskTelemetrySource,
        telemetry_policy: PropRiskTelemetryPolicy,
    ) -> None:
        self._phase24 = phase24
        self._phase27 = phase27
        self._registry = provider_registry
        self._telemetry_source = telemetry_source
        self._telemetry_policy = telemetry_policy
        self._validator = PropRiskTelemetryValidator()

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
        aliases = [profile.account_alias for profile in profiles]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Phase 28 requires unique account_alias values")
        explicit_hints = {item.account_alias: item for item in research_hints}
        if len(explicit_hints) != len(research_hints):
            raise ValueError("research_hints require unique account_alias values")

        discovery = self._phase24.discover()
        discovered = {item["account_alias"]: item for item in discovery["accounts"]}

        eligible: list[NinjaTraderAccountRuleProfile] = []
        generated_hints: list[PropFirmResearchHint] = []
        preblocked: dict[str, Phase28AccountPlan] = {}
        telemetry_by_alias: dict[str, PropRiskTelemetryValidationResult | None] = {}
        provider_by_alias: dict[str, str | None] = {}

        for profile in profiles:
            if not profile.enabled:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.SKIPPED_DISABLED,
                    classification="NOT_EVALUATED",
                    reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
                )
                continue

            account = discovered.get(profile.account_alias)
            if account is None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.BLOCKED_ACCOUNT_NOT_DISCOVERED,
                    classification="UNKNOWN",
                    reason="NINJATRADER_ACCOUNT_NOT_DISCOVERED_FOR_DYNAMIC_PROP_RISK",
                )
                continue

            classification, conflict = self._classification(profile=profile, discovered=account)
            provider_label = str(account.get("provider") or "").strip() or None
            if conflict or classification is AccountClassification.UNKNOWN:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.BLOCKED_ACCOUNT_CLASSIFICATION,
                    classification=("CONFLICT" if conflict else "UNKNOWN"),
                    provider_label=provider_label,
                    reason=(
                        "ACCOUNT_CLASSIFICATION_CONFLICT_REQUIRES_RESOLUTION"
                        if conflict
                        else "ACCOUNT_MUST_BE_VERIFIED_PERSONAL_OR_PROP_BEFORE_DYNAMIC_RISK_RESOLUTION"
                    ),
                )
                continue

            if classification is AccountClassification.PERSONAL:
                eligible.append(profile)
                telemetry_by_alias[profile.account_alias] = None
                provider_by_alias[profile.account_alias] = None
                hint = explicit_hints.get(profile.account_alias)
                if hint is not None:
                    generated_hints.append(hint)
                continue

            if not provider_label:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.BLOCKED_PROVIDER_METADATA,
                    classification=classification.value,
                    reason="PROP_ACCOUNT_REQUIRES_PROVIDER_METADATA_FOR_DYNAMIC_RISK_STATE",
                )
                continue

            identity_status, provider = self._registry.resolve(provider_label)
            if identity_status is not PropFirmIdentityStatus.VERIFIED or provider is None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.BLOCKED_PROVIDER_IDENTITY,
                    classification=classification.value,
                    provider_label=provider_label,
                    reason=f"PROP_PROVIDER_IDENTITY_{identity_status.value}",
                )
                continue

            try:
                telemetry = self._telemetry_source.snapshot(
                    account_alias=profile.account_alias,
                    provider_id=provider.provider_id,
                )
            except Exception:
                telemetry = None
                validation = self._validator.validate(
                    snapshot=None,
                    account_alias=profile.account_alias,
                    provider_id=provider.provider_id,
                    account_currency=str(account.get("currency") or ""),
                    now_ms=now_ms,
                    policy=self._telemetry_policy,
                )
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.BLOCKED_RISK_TELEMETRY,
                    classification=classification.value,
                    provider_label=provider_label,
                    provider_id=provider.provider_id,
                    telemetry=validation,
                    reason="PROP_RISK_TELEMETRY_SOURCE_ERROR",
                )
                continue

            validation = self._validator.validate(
                snapshot=telemetry,
                account_alias=profile.account_alias,
                provider_id=provider.provider_id,
                account_currency=str(account.get("currency") or ""),
                now_ms=now_ms,
                policy=self._telemetry_policy,
            )
            if not validation.ready or validation.snapshot is None:
                preblocked[profile.account_alias] = self._blocked(
                    profile=profile,
                    status=Phase28AccountStatus.BLOCKED_RISK_TELEMETRY,
                    classification=classification.value,
                    provider_label=provider_label,
                    provider_id=provider.provider_id,
                    telemetry=validation,
                    reason=f"PROP_RISK_TELEMETRY_{validation.status.value}",
                )
                continue

            updated = replace(profile, prop_limits=validation.snapshot.to_prop_limits())
            eligible.append(updated)
            telemetry_by_alias[profile.account_alias] = validation
            provider_by_alias[profile.account_alias] = provider.provider_id

            explicit = explicit_hints.get(profile.account_alias)
            if explicit is not None:
                generated_hints.append(explicit)
            elif validation.snapshot.program_name or validation.snapshot.account_size:
                generated_hints.append(
                    PropFirmResearchHint(
                        account_alias=profile.account_alias,
                        program_hint=validation.snapshot.program_name,
                        account_size_hint=validation.snapshot.account_size,
                    )
                )

        phase27_accounts: dict[str, dict[str, Any]] = {}
        if eligible:
            phase27 = self._phase27.prepare(
                intent=intent,
                profiles=tuple(eligible),
                rule_context=rule_context,
                now_ms=now_ms,
                supervision_policy=supervision_policy,
                research_hints=tuple(generated_hints),
                policy=OrchestrationPolicy.BEST_EFFORT,
            )
            phase27_accounts = {item["account_alias"]: item for item in phase27["accounts"]}

        original_by_alias = {profile.account_alias: profile for profile in profiles}
        eligible_by_alias = {profile.account_alias: profile for profile in eligible}
        plans: list[Phase28AccountPlan] = []
        for profile in profiles:
            if profile.account_alias in preblocked:
                plans.append(preblocked[profile.account_alias])
                continue

            account = discovered[profile.account_alias]
            classification, _ = self._classification(profile=profile, discovered=account)
            phase27_account = phase27_accounts.get(profile.account_alias)
            validation = telemetry_by_alias.get(profile.account_alias)
            effective = eligible_by_alias[profile.account_alias].prop_limits
            if not phase27_account or not phase27_account.get("preparation_ready"):
                plans.append(
                    Phase28AccountPlan(
                        account_alias=profile.account_alias,
                        status=Phase28AccountStatus.BLOCKED_PHASE27,
                        classification=classification.value,
                        provider_label=str(account.get("provider") or "") or None,
                        provider_id=provider_by_alias.get(profile.account_alias),
                        risk_telemetry_state=(validation.public_dict() if validation else None),
                        effective_prop_risk_limits=(
                            effective.public_dict() if effective is not None else None
                        ),
                        phase27_account_plan=phase27_account,
                        preparation_ready=False,
                        reason_codes=(
                            "PHASE27_BLOCKED_AFTER_DYNAMIC_PROP_RISK_INJECTION",
                            "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28",
                        ),
                    )
                )
                continue

            plans.append(
                Phase28AccountPlan(
                    account_alias=profile.account_alias,
                    status=Phase28AccountStatus.READY,
                    classification=classification.value,
                    provider_label=str(account.get("provider") or "") or None,
                    provider_id=provider_by_alias.get(profile.account_alias),
                    risk_telemetry_state=(validation.public_dict() if validation else None),
                    effective_prop_risk_limits=(
                        effective.public_dict() if effective is not None else None
                    ),
                    phase27_account_plan=phase27_account,
                    preparation_ready=True,
                    reason_codes=(
                        "PERSONAL_ACCOUNT_BYPASSES_PROP_RISK_TELEMETRY"
                        if classification is AccountClassification.PERSONAL
                        else "FRESH_VERIFIED_PROP_RISK_TELEMETRY_INJECTED_BEFORE_PHASE27",
                        "PHASE27_OFFICIAL_RULE_RESEARCH_AND_ADAPTATION_PASSED",
                        "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28",
                    ),
                )
            )

        return self._batch(intent=intent, plans=tuple(plans), policy=policy).to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"dynamic_prop_risk_state": context}

    @staticmethod
    def _classification(
        *,
        profile: NinjaTraderAccountRuleProfile,
        discovered: dict[str, Any],
    ) -> tuple[AccountClassification, bool]:
        configured = profile.configured_classification
        detected_raw = discovered.get("detected_classification")
        known_values = {item.value for item in AccountClassification}
        detected = AccountClassification(detected_raw) if detected_raw in known_values else None
        if not bool(discovered.get("classification_metadata_verified")):
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
    def _blocked(
        *,
        profile: NinjaTraderAccountRuleProfile,
        status: Phase28AccountStatus,
        classification: str,
        reason: str,
        provider_label: str | None = None,
        provider_id: str | None = None,
        telemetry: PropRiskTelemetryValidationResult | None = None,
    ) -> Phase28AccountPlan:
        return Phase28AccountPlan(
            account_alias=profile.account_alias,
            status=status,
            classification=classification,
            provider_label=provider_label,
            provider_id=provider_id,
            risk_telemetry_state=(telemetry.public_dict() if telemetry else None),
            effective_prop_risk_limits=None,
            phase27_account_plan=None,
            preparation_ready=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28"),
        )

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        plans: tuple[Phase28AccountPlan, ...],
        policy: OrchestrationPolicy,
    ) -> Phase28MultiAccountPlan:
        skipped = sum(item.status is Phase28AccountStatus.SKIPPED_DISABLED for item in plans)
        enabled = len(plans) - skipped
        ready = sum(item.preparation_ready for item in plans)
        blocked = enabled - ready

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_ACCOUNTS_FOR_PHASE28",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            status = MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_ACCOUNT_TO_PASS_PHASE28",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28",
            )
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = (
                "ALL_ENABLED_ACCOUNTS_PASSED_PHASE28",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28",
            )
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_ACCOUNTS_WITH_INVALID_DYNAMIC_PROP_RISK_STATE",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28",
            )
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = (
                "NO_ACCOUNT_PASSED_PHASE28",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28",
            )

        return Phase28MultiAccountPlan(
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
