"""Deterministic live prop-firm compliance evaluation.

Phase 28 consumes validated Phase 27 rule snapshots plus current per-account
metrics. It never invents provider-specific formulas. Rules that require an exact
provider formula must be backed by an explicit adapter; otherwise the account
fails closed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from .account_risk import PropFirmRiskLimits
from .prop_rule_research import PropFirmRuleSnapshot


class PropFirmComplianceStatus(str, Enum):
    READY = "READY"
    STALE_METRICS = "STALE_METRICS"
    MISSING_REQUIRED_METRICS = "MISSING_REQUIRED_METRICS"
    DAILY_LOSS_BREACH = "DAILY_LOSS_BREACH"
    DRAWDOWN_BREACH = "DRAWDOWN_BREACH"
    CONTRACT_CAP_REACHED = "CONTRACT_CAP_REACHED"
    UNSUPPORTED_REQUIRED_FORMULA = "UNSUPPORTED_REQUIRED_FORMULA"
    FORMULA_FAILED = "FORMULA_FAILED"


class FormulaCheckStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    MISSING_METRICS = "MISSING_METRICS"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class PropFirmLiveMetrics:
    account_alias: str
    observed_at_ms: int
    daily_loss_used: float | None = None
    remaining_drawdown_buffer: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    current_contracts_by_root: dict[str, int] = field(default_factory=dict)
    trading_day_count: int | None = None
    cumulative_profit: float | None = None
    best_day_profit: float | None = None
    scaling_level: str | None = None
    payout_eligibility_inputs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if self.observed_at_ms < 0:
            raise ValueError("observed_at_ms must be >= 0")
        for value in (
            self.daily_loss_used,
            self.remaining_drawdown_buffer,
            self.realized_pnl,
            self.unrealized_pnl,
            self.cumulative_profit,
            self.best_day_profit,
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError("numeric live metrics must be finite when supplied")
        if self.daily_loss_used is not None and self.daily_loss_used < 0:
            raise ValueError("daily_loss_used must be >= 0")
        if self.trading_day_count is not None and self.trading_day_count < 0:
            raise ValueError("trading_day_count must be >= 0")
        normalized: dict[str, int] = {}
        for root, count in self.current_contracts_by_root.items():
            root_name = str(root).strip().upper()
            if not root_name or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("current contract usage requires nonnegative integer counts")
            normalized[root_name] = count
        object.__setattr__(self, "current_contracts_by_root", normalized)

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


@dataclass(frozen=True)
class FormulaCheck:
    rule_name: str
    status: FormulaCheckStatus
    reason_codes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status is FormulaCheckStatus.PASSED


class PropFirmFormulaAdapter(Protocol):
    provider_id: str

    def evaluate(
        self,
        *,
        rule_name: str,
        snapshot: PropFirmRuleSnapshot,
        metrics: PropFirmLiveMetrics,
    ) -> FormulaCheck: ...


class PropFirmFormulaRegistry:
    """Explicit registry of verified provider-specific formula adapters."""

    def __init__(self, adapters: tuple[PropFirmFormulaAdapter, ...] = ()) -> None:
        self._adapters = {adapter.provider_id.strip().upper(): adapter for adapter in adapters}

    def evaluate(
        self,
        *,
        provider_id: str,
        rule_name: str,
        snapshot: PropFirmRuleSnapshot,
        metrics: PropFirmLiveMetrics,
    ) -> FormulaCheck:
        adapter = self._adapters.get(provider_id.strip().upper())
        if adapter is None:
            return FormulaCheck(
                rule_name=rule_name,
                status=FormulaCheckStatus.UNSUPPORTED,
                reason_codes=(f"NO_VERIFIED_{rule_name.upper()}_FORMULA_ADAPTER",),
            )
        return adapter.evaluate(rule_name=rule_name, snapshot=snapshot, metrics=metrics)


@dataclass(frozen=True)
class PropFirmComplianceResult:
    status: PropFirmComplianceStatus
    account_alias: str
    provider_id: str
    metrics_age_ms: int
    remaining_daily_loss_buffer: float | None
    remaining_drawdown_buffer: float | None
    formula_checks: tuple[FormulaCheck, ...]
    reason_codes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status is PropFirmComplianceStatus.READY

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "account_alias": self.account_alias,
            "provider_id": self.provider_id,
            "metrics_age_ms": self.metrics_age_ms,
            "remaining_daily_loss_buffer": self.remaining_daily_loss_buffer,
            "remaining_drawdown_buffer": self.remaining_drawdown_buffer,
            "formula_checks": [
                {
                    "rule_name": item.rule_name,
                    "status": item.status.value,
                    "reason_codes": list(item.reason_codes),
                }
                for item in self.formula_checks
            ],
            "reason_codes": list(self.reason_codes),
            "nominal_account_size_used_for_sizing": False,
            "account_environment": "HIDDEN_INTERNAL",
            "order_submission_enabled": False,
        }


class PropFirmComplianceEngine:
    def __init__(self, *, formulas: PropFirmFormulaRegistry | None = None) -> None:
        self._formulas = formulas or PropFirmFormulaRegistry()

    def evaluate(
        self,
        *,
        snapshot: PropFirmRuleSnapshot,
        metrics: PropFirmLiveMetrics,
        limits: PropFirmRiskLimits | None,
        now_ms: int,
        metrics_max_age_ms: int,
    ) -> PropFirmComplianceResult:
        if metrics_max_age_ms <= 0:
            raise ValueError("metrics_max_age_ms must be > 0")
        age = now_ms - metrics.observed_at_ms
        if age < 0 or age > metrics_max_age_ms:
            return self._result(
                status=PropFirmComplianceStatus.STALE_METRICS,
                snapshot=snapshot,
                metrics=metrics,
                age=age,
                reason="LIVE_PROP_ACCOUNT_METRICS_ARE_STALE_OR_FROM_THE_FUTURE",
            )

        daily_remaining: float | None = None
        if snapshot.daily_loss_rule_present is True:
            if limits is None or metrics.daily_loss_used is None:
                return self._result(
                    status=PropFirmComplianceStatus.MISSING_REQUIRED_METRICS,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    reason="DAILY_LOSS_RULE_REQUIRES_CURRENT_DAILY_LOSS_USAGE_AND_VERIFIED_LIMIT",
                )
            daily_remaining = max(0.0, limits.daily_loss_limit - metrics.daily_loss_used)
            if daily_remaining <= 0:
                return self._result(
                    status=PropFirmComplianceStatus.DAILY_LOSS_BREACH,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    reason="PROP_DAILY_LOSS_LIMIT_REACHED_OR_EXCEEDED",
                )

        drawdown_remaining = metrics.remaining_drawdown_buffer
        if snapshot.drawdown_rule_present is True:
            if drawdown_remaining is None:
                return self._result(
                    status=PropFirmComplianceStatus.MISSING_REQUIRED_METRICS,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    reason="DRAWDOWN_RULE_REQUIRES_CURRENT_REMAINING_DRAWDOWN_BUFFER",
                )
            if drawdown_remaining <= 0:
                return self._result(
                    status=PropFirmComplianceStatus.DRAWDOWN_BREACH,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    drawdown_remaining=drawdown_remaining,
                    reason="PROP_MAX_OR_TRAILING_DRAWDOWN_LIMIT_REACHED_OR_EXCEEDED",
                )

        for root, cap in (snapshot.max_contracts_by_root or {}).items():
            current = metrics.current_contracts_by_root.get(root, 0)
            if current >= cap:
                return self._result(
                    status=PropFirmComplianceStatus.CONTRACT_CAP_REACHED,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    drawdown_remaining=drawdown_remaining,
                    reason=f"CURRENT_{root}_CONTRACT_USAGE_LEAVES_NO_CAPACITY_FOR_A_NEW_CONTRACT",
                )

        checks: list[FormulaCheck] = []
        required = []
        if snapshot.consistency_rule_required:
            required.append("consistency")
        if snapshot.scaling_rule_required:
            required.append("scaling")
        if getattr(snapshot, "payout_rule_required", False):
            required.append("payout")

        for rule_name in required:
            check = self._formulas.evaluate(
                provider_id=snapshot.provider_id,
                rule_name=rule_name,
                snapshot=snapshot,
                metrics=metrics,
            )
            checks.append(check)
            if check.status is FormulaCheckStatus.UNSUPPORTED:
                return self._result(
                    status=PropFirmComplianceStatus.UNSUPPORTED_REQUIRED_FORMULA,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    drawdown_remaining=drawdown_remaining,
                    checks=tuple(checks),
                    reason=f"{rule_name.upper()}_RULE_REQUIRES_VERIFIED_PROVIDER_FORMULA",
                )
            if check.status is FormulaCheckStatus.MISSING_METRICS:
                return self._result(
                    status=PropFirmComplianceStatus.MISSING_REQUIRED_METRICS,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    drawdown_remaining=drawdown_remaining,
                    checks=tuple(checks),
                    reason=f"{rule_name.upper()}_RULE_REQUIRES_ADDITIONAL_LIVE_ACCOUNT_METRICS",
                )
            if check.status is FormulaCheckStatus.FAILED:
                return self._result(
                    status=PropFirmComplianceStatus.FORMULA_FAILED,
                    snapshot=snapshot,
                    metrics=metrics,
                    age=age,
                    daily_remaining=daily_remaining,
                    drawdown_remaining=drawdown_remaining,
                    checks=tuple(checks),
                    reason=f"{rule_name.upper()}_PROVIDER_RULE_FAILED",
                )

        return self._result(
            status=PropFirmComplianceStatus.READY,
            snapshot=snapshot,
            metrics=metrics,
            age=age,
            daily_remaining=daily_remaining,
            drawdown_remaining=drawdown_remaining,
            checks=tuple(checks),
            reason="CURRENT_PROP_ACCOUNT_METRICS_PASS_VERIFIED_RULE_CONTRACT",
        )

    @staticmethod
    def _result(
        *,
        status: PropFirmComplianceStatus,
        snapshot: PropFirmRuleSnapshot,
        metrics: PropFirmLiveMetrics,
        age: int,
        reason: str,
        daily_remaining: float | None = None,
        drawdown_remaining: float | None = None,
        checks: tuple[FormulaCheck, ...] = (),
    ) -> PropFirmComplianceResult:
        return PropFirmComplianceResult(
            status=status,
            account_alias=metrics.account_alias,
            provider_id=snapshot.provider_id,
            metrics_age_ms=age,
            remaining_daily_loss_buffer=daily_remaining,
            remaining_drawdown_buffer=drawdown_remaining,
            formula_checks=checks,
            reason_codes=(reason, "LIVE_PROP_COMPLIANCE_FAILS_CLOSED"),
        )
