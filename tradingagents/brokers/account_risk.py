"""Per-account classification and prop-firm risk-base contracts.

Phase 23 deliberately classifies each broker account independently. A platform
such as NinjaTrader may contain personal brokerage accounts and prop-firm
accounts at the same time, so platform identity is never used as a proxy for
account ownership or risk semantics.

For personal accounts the deterministic Londres risk base is actual account
equity. For prop-firm accounts the advertised/nominal account size is metadata
only: the tradable risk base is the smallest active remaining loss buffer,
starting with the remaining daily-loss allowance and, when applicable, the
remaining max/trailing-drawdown buffer.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from .contracts import (
    BrokerAccountSnapshot,
    BrokerAdapter,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerType,
)


class AccountClassification(str, Enum):
    PERSONAL = "PERSONAL"
    PROP_FIRM = "PROP_FIRM"
    UNKNOWN = "UNKNOWN"


class AccountClassificationSource(str, Enum):
    USER_CONFIRMED_CONFIG = "USER_CONFIRMED_CONFIG"
    VERIFIED_PROVIDER_METADATA = "VERIFIED_PROVIDER_METADATA"
    PRIVATE_PROVIDER_REGISTRY = "PRIVATE_PROVIDER_REGISTRY"
    UNKNOWN = "UNKNOWN"


class AccountClassificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class AccountRiskBaseStatus(str, Enum):
    READY = "READY"
    CLASSIFICATION_UNKNOWN = "CLASSIFICATION_UNKNOWN"
    CLASSIFICATION_CONFLICT = "CLASSIFICATION_CONFLICT"
    INVALID_ACCOUNT_EQUITY = "INVALID_ACCOUNT_EQUITY"
    PROP_DAILY_LOSS_LIMIT_REQUIRED = "PROP_DAILY_LOSS_LIMIT_REQUIRED"
    PROP_DAILY_LOSS_EXHAUSTED = "PROP_DAILY_LOSS_EXHAUSTED"
    PROP_DRAWDOWN_EXHAUSTED = "PROP_DRAWDOWN_EXHAUSTED"


@dataclass(frozen=True)
class PropFirmRiskLimits:
    """Dynamic prop-firm loss buffers used as tradable risk equity.

    ``daily_loss_limit`` and ``daily_loss_used`` are cash amounts in the account
    deposit currency. ``remaining_drawdown_buffer`` is optional because not all
    account programs expose an additional trailing/max-drawdown constraint in a
    form the adapter can verify. If it is supplied, the smaller remaining buffer
    controls the risk base.

    ``nominal_account_size`` is descriptive metadata only and is never used by
    the sizing engine.
    """

    daily_loss_limit: float
    daily_loss_used: float = 0.0
    remaining_drawdown_buffer: float | None = None
    nominal_account_size: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.daily_loss_limit) or self.daily_loss_limit <= 0:
            raise ValueError("daily_loss_limit must be finite and > 0")
        if not math.isfinite(self.daily_loss_used) or self.daily_loss_used < 0:
            raise ValueError("daily_loss_used must be finite and >= 0")
        if self.remaining_drawdown_buffer is not None:
            if not math.isfinite(self.remaining_drawdown_buffer):
                raise ValueError("remaining_drawdown_buffer must be finite when supplied")
        if self.nominal_account_size is not None:
            if not math.isfinite(self.nominal_account_size) or self.nominal_account_size <= 0:
                raise ValueError("nominal_account_size must be finite and > 0 when supplied")

    @property
    def remaining_daily_loss_buffer(self) -> float:
        return max(0.0, self.daily_loss_limit - self.daily_loss_used)

    @property
    def tradable_risk_equity(self) -> float:
        buffers = [self.remaining_daily_loss_buffer]
        if self.remaining_drawdown_buffer is not None:
            buffers.append(max(0.0, self.remaining_drawdown_buffer))
        return min(buffers)

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["remaining_daily_loss_buffer"] = self.remaining_daily_loss_buffer
        payload["tradable_risk_equity"] = self.tradable_risk_equity
        payload["nominal_account_size_used_for_sizing"] = False
        return payload


@dataclass(frozen=True)
class AccountRiskProfile:
    """Private per-account classification input.

    A configured classification may come from a one-time user-confirmed account
    profile or a private provider registry. ``detected_classification`` is for a
    future adapter/provider signal. When both exist they must agree; conflicts
    fail closed instead of allowing the strategy to guess.
    """

    account_alias: str
    configured_classification: AccountClassification | None = None
    classification_source: AccountClassificationSource = AccountClassificationSource.UNKNOWN
    detected_classification: AccountClassification | None = None
    prop_limits: PropFirmRiskLimits | None = None

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")


@dataclass(frozen=True)
class AccountRiskBaseResult:
    status: AccountRiskBaseStatus
    classification_status: AccountClassificationStatus
    classification: AccountClassification
    classification_source: AccountClassificationSource
    account_alias: str
    actual_account_equity: float
    risk_base: float | None
    risk_base_source: str | None
    nominal_account_size: float | None
    remaining_daily_loss_buffer: float | None
    remaining_drawdown_buffer: float | None
    reason_codes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status is AccountRiskBaseStatus.READY and self.risk_base is not None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["classification_status"] = self.classification_status.value
        payload["classification"] = self.classification.value
        payload["classification_source"] = self.classification_source.value
        payload["nominal_account_size_used_for_sizing"] = False
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


class AccountRiskBaseResolver:
    """Resolve the deterministic sizing equity for one account."""

    def resolve(
        self,
        *,
        profile: AccountRiskProfile,
        actual_account_equity: float,
    ) -> AccountRiskBaseResult:
        if not math.isfinite(actual_account_equity) or actual_account_equity <= 0:
            return self._blocked(
                profile=profile,
                actual_account_equity=actual_account_equity,
                status=AccountRiskBaseStatus.INVALID_ACCOUNT_EQUITY,
                classification_status=AccountClassificationStatus.UNKNOWN,
                classification=AccountClassification.UNKNOWN,
                reason="CONNECTED_ACCOUNT_REQUIRES_POSITIVE_ACTUAL_EQUITY",
            )

        classification_status, classification, source = self._classification(profile)
        if classification_status is AccountClassificationStatus.CONFLICT:
            return self._blocked(
                profile=profile,
                actual_account_equity=actual_account_equity,
                status=AccountRiskBaseStatus.CLASSIFICATION_CONFLICT,
                classification_status=classification_status,
                classification=classification,
                source=source,
                reason="CONFIGURED_AND_DETECTED_ACCOUNT_CLASSIFICATIONS_CONFLICT",
            )
        if classification_status is AccountClassificationStatus.UNKNOWN:
            return self._blocked(
                profile=profile,
                actual_account_equity=actual_account_equity,
                status=AccountRiskBaseStatus.CLASSIFICATION_UNKNOWN,
                classification_status=classification_status,
                classification=classification,
                source=source,
                reason="ACCOUNT_CLASSIFICATION_MUST_BE_VERIFIED_PER_ACCOUNT_NO_GUESSING",
            )

        if classification is AccountClassification.PERSONAL:
            return AccountRiskBaseResult(
                status=AccountRiskBaseStatus.READY,
                classification_status=classification_status,
                classification=classification,
                classification_source=source,
                account_alias=profile.account_alias,
                actual_account_equity=actual_account_equity,
                risk_base=actual_account_equity,
                risk_base_source="ACTUAL_ACCOUNT_EQUITY",
                nominal_account_size=None,
                remaining_daily_loss_buffer=None,
                remaining_drawdown_buffer=None,
                reason_codes=(
                    "PERSONAL_ACCOUNT_USES_ACTUAL_ACCOUNT_EQUITY_AS_RISK_BASE",
                    "ACCOUNT_CLASSIFICATION_VERIFIED_PER_ACCOUNT",
                ),
            )

        limits = profile.prop_limits
        if limits is None:
            return self._blocked(
                profile=profile,
                actual_account_equity=actual_account_equity,
                status=AccountRiskBaseStatus.PROP_DAILY_LOSS_LIMIT_REQUIRED,
                classification_status=classification_status,
                classification=classification,
                source=source,
                reason="PROP_ACCOUNT_REQUIRES_VERIFIED_DAILY_LOSS_LIMIT_DATA",
            )

        daily_remaining = limits.remaining_daily_loss_buffer
        if daily_remaining <= 0:
            return self._blocked(
                profile=profile,
                actual_account_equity=actual_account_equity,
                status=AccountRiskBaseStatus.PROP_DAILY_LOSS_EXHAUSTED,
                classification_status=classification_status,
                classification=classification,
                source=source,
                reason="PROP_DAILY_LOSS_BUFFER_EXHAUSTED",
            )
        if limits.remaining_drawdown_buffer is not None and limits.remaining_drawdown_buffer <= 0:
            return self._blocked(
                profile=profile,
                actual_account_equity=actual_account_equity,
                status=AccountRiskBaseStatus.PROP_DRAWDOWN_EXHAUSTED,
                classification_status=classification_status,
                classification=classification,
                source=source,
                reason="PROP_MAX_OR_TRAILING_DRAWDOWN_BUFFER_EXHAUSTED",
            )

        risk_base = limits.tradable_risk_equity
        return AccountRiskBaseResult(
            status=AccountRiskBaseStatus.READY,
            classification_status=classification_status,
            classification=classification,
            classification_source=source,
            account_alias=profile.account_alias,
            actual_account_equity=actual_account_equity,
            risk_base=risk_base,
            risk_base_source="MIN_ACTIVE_PROP_LOSS_BUFFER",
            nominal_account_size=limits.nominal_account_size,
            remaining_daily_loss_buffer=daily_remaining,
            remaining_drawdown_buffer=limits.remaining_drawdown_buffer,
            reason_codes=(
                "PROP_ACCOUNT_NOMINAL_SIZE_IS_METADATA_ONLY",
                "PROP_RISK_BASE_USES_REMAINING_DAILY_LOSS_BUFFER",
                "PROP_RISK_BASE_USES_STRICTER_ACTIVE_DRAWDOWN_BUFFER_WHEN_PRESENT",
                "ACCOUNT_CLASSIFICATION_VERIFIED_PER_ACCOUNT",
            ),
        )

    @staticmethod
    def _classification(
        profile: AccountRiskProfile,
    ) -> tuple[AccountClassificationStatus, AccountClassification, AccountClassificationSource]:
        configured = profile.configured_classification
        detected = profile.detected_classification

        configured_known = configured not in (None, AccountClassification.UNKNOWN)
        detected_known = detected not in (None, AccountClassification.UNKNOWN)

        if configured_known and detected_known and configured != detected:
            return (
                AccountClassificationStatus.CONFLICT,
                AccountClassification.UNKNOWN,
                profile.classification_source,
            )
        if detected_known:
            return (
                AccountClassificationStatus.VERIFIED,
                detected or AccountClassification.UNKNOWN,
                AccountClassificationSource.VERIFIED_PROVIDER_METADATA,
            )
        if configured_known and profile.classification_source is not AccountClassificationSource.UNKNOWN:
            return (
                AccountClassificationStatus.VERIFIED,
                configured or AccountClassification.UNKNOWN,
                profile.classification_source,
            )
        return (
            AccountClassificationStatus.UNKNOWN,
            AccountClassification.UNKNOWN,
            AccountClassificationSource.UNKNOWN,
        )

    @staticmethod
    def _blocked(
        *,
        profile: AccountRiskProfile,
        actual_account_equity: float,
        status: AccountRiskBaseStatus,
        classification_status: AccountClassificationStatus,
        classification: AccountClassification,
        reason: str,
        source: AccountClassificationSource = AccountClassificationSource.UNKNOWN,
    ) -> AccountRiskBaseResult:
        limits = profile.prop_limits
        return AccountRiskBaseResult(
            status=status,
            classification_status=classification_status,
            classification=classification,
            classification_source=source,
            account_alias=profile.account_alias,
            actual_account_equity=actual_account_equity,
            risk_base=None,
            risk_base_source=None,
            nominal_account_size=(limits.nominal_account_size if limits is not None else None),
            remaining_daily_loss_buffer=(
                limits.remaining_daily_loss_buffer if limits is not None else None
            ),
            remaining_drawdown_buffer=(
                limits.remaining_drawdown_buffer if limits is not None else None
            ),
            reason_codes=(reason, "POSITION_SIZING_FAILS_CLOSED_UNTIL_ACCOUNT_RISK_BASE_IS_VALID"),
        )


class RiskBaseAccountAdapter:
    """Read-only adapter view that substitutes only sizing equity.

    Phase 20 already has deterministic broker capability, symbol, tick-value,
    target and 60/40 validation. Phase 23 reuses that machinery by presenting a
    risk-base equity snapshot while keeping the original adapter untouched. The
    surrounding Phase 23 result preserves actual account equity separately.
    """

    def __init__(
        self,
        base: BrokerAdapter,
        *,
        account_alias: str,
        risk_base: float,
    ) -> None:
        if risk_base <= 0:
            raise ValueError("risk_base must be > 0")
        self._base = base
        self._account_alias = account_alias
        self._risk_base = float(risk_base)

    @property
    def adapter_id(self) -> str:
        return f"{self._base.adapter_id}:phase23-risk-base"

    @property
    def broker_type(self) -> BrokerType:
        return self._base.broker_type

    def public_status(self) -> dict[str, Any]:
        status = dict(self._base.public_status())
        status["account_environment"] = "HIDDEN_INTERNAL"
        status["order_submission_enabled"] = False
        return status

    def capabilities(self) -> BrokerCapabilities:
        return self._base.capabilities()

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        if account_alias != self._account_alias:
            raise ValueError("risk-base adapter account alias mismatch")
        snapshot = self._base.account_snapshot(account_alias)
        return replace(snapshot, equity=self._risk_base)

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec:
        return self._base.instrument_snapshot(
            account_alias=account_alias,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
        )

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote:
        return self._base.quote_snapshot(
            account_alias=account_alias,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
        )
