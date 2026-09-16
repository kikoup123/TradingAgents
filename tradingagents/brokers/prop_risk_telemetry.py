"""Verified dynamic prop-firm risk telemetry used by Londres Phase 28.

The rule-research layer (Phase 27) determines *what* a provider requires. This
module supplies the current account-specific loss buffers needed to enforce those
rules. It deliberately does not derive a prop firm's daily-loss usage or trailing
drawdown from generic account P/L because provider definitions and reset rules
vary by program.

A telemetry source must therefore provide explicit, account-local values from a
trusted provider/dashboard/companion integration. Advertised nominal account
size remains metadata only and is never tradable risk equity.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .account_risk import PropFirmRiskLimits


class PropRiskTelemetryStatus(str, Enum):
    READY = "READY"
    NOT_FOUND = "NOT_FOUND"
    UNVERIFIED_SOURCE = "UNVERIFIED_SOURCE"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    STALE = "STALE"
    INVALID_DATA = "INVALID_DATA"


@dataclass(frozen=True)
class PropRiskTelemetryPolicy:
    """Explicit freshness requirements for dynamic prop risk state."""

    max_age_ms: int
    future_timestamp_tolerance_ms: int = 0

    def __post_init__(self) -> None:
        if self.max_age_ms <= 0:
            raise ValueError("max_age_ms must be > 0")
        if self.future_timestamp_tolerance_ms < 0:
            raise ValueError("future_timestamp_tolerance_ms must be >= 0")


@dataclass(frozen=True)
class PropRiskTelemetrySnapshot:
    """One verified provider/account risk-state observation.

    ``daily_loss_limit`` and ``daily_loss_used`` are provider-defined current
    cash values in ``currency``. ``remaining_drawdown_buffer`` is supplied by
    the provider-specific telemetry producer when that rule applies.

    Realized/unrealized P/L fields are optional audit observations only. Londres
    never reverse-engineers provider loss rules from them.
    """

    account_alias: str
    provider_id: str
    currency: str
    as_of_ms: int
    source: str
    source_verified: bool
    daily_loss_limit: float
    daily_loss_used: float
    remaining_drawdown_buffer: float | None = None
    nominal_account_size: float | None = None
    program_name: str | None = None
    account_size: str | None = None
    realized_pnl_observed: float | None = None
    unrealized_pnl_observed: float | None = None
    daily_loss_window_id: str | None = None
    drawdown_model: str | None = None

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if not self.currency.strip():
            raise ValueError("currency is required")
        if self.as_of_ms < 0:
            raise ValueError("as_of_ms must be >= 0")
        if not self.source.strip():
            raise ValueError("source is required")
        if not math.isfinite(self.daily_loss_limit) or self.daily_loss_limit <= 0:
            raise ValueError("daily_loss_limit must be finite and > 0")
        if not math.isfinite(self.daily_loss_used) or self.daily_loss_used < 0:
            raise ValueError("daily_loss_used must be finite and >= 0")
        if self.remaining_drawdown_buffer is not None and not math.isfinite(
            self.remaining_drawdown_buffer
        ):
            raise ValueError("remaining_drawdown_buffer must be finite when supplied")
        if self.nominal_account_size is not None and (
            not math.isfinite(self.nominal_account_size) or self.nominal_account_size <= 0
        ):
            raise ValueError("nominal_account_size must be finite and > 0 when supplied")
        for name, value in (
            ("realized_pnl_observed", self.realized_pnl_observed),
            ("unrealized_pnl_observed", self.unrealized_pnl_observed),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when supplied")

        object.__setattr__(self, "provider_id", self.provider_id.strip().upper())
        object.__setattr__(self, "currency", self.currency.strip().upper())
        object.__setattr__(self, "source", self.source.strip())

    @property
    def remaining_daily_loss_buffer(self) -> float:
        return max(0.0, self.daily_loss_limit - self.daily_loss_used)

    @property
    def tradable_risk_equity(self) -> float:
        values = [self.remaining_daily_loss_buffer]
        if self.remaining_drawdown_buffer is not None:
            values.append(max(0.0, self.remaining_drawdown_buffer))
        return min(values)

    def to_prop_limits(self) -> PropFirmRiskLimits:
        return PropFirmRiskLimits(
            daily_loss_limit=self.daily_loss_limit,
            daily_loss_used=self.daily_loss_used,
            remaining_drawdown_buffer=self.remaining_drawdown_buffer,
            nominal_account_size=self.nominal_account_size,
        )

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["remaining_daily_loss_buffer"] = self.remaining_daily_loss_buffer
        payload["tradable_risk_equity"] = self.tradable_risk_equity
        payload["nominal_account_size_used_for_sizing"] = False
        payload["generic_pnl_used_to_infer_prop_limits"] = False
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


class PropRiskTelemetrySource(Protocol):
    def snapshot(
        self,
        *,
        account_alias: str,
        provider_id: str,
    ) -> PropRiskTelemetrySnapshot | None: ...


class JsonPropRiskTelemetrySource:
    """Read verified prop risk state from a private local JSON handoff file.

    The file is intentionally separate from the public/LLM-facing state. A
    provider-specific companion can atomically replace it whenever dashboard/API
    values change. This reader does not contain provider credentials.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    def snapshot(
        self,
        *,
        account_alias: str,
        provider_id: str,
    ) -> PropRiskTelemetrySnapshot | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("prop risk telemetry file is unavailable or invalid") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise RuntimeError("unsupported prop risk telemetry schema")
        rows = data.get("accounts")
        if not isinstance(rows, list):
            raise RuntimeError("prop risk telemetry accounts must be a list")

        provider = provider_id.strip().upper()
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("account_alias") or "").strip() == account_alias
            and str(row.get("provider_id") or "").strip().upper() == provider
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise RuntimeError("prop risk telemetry must contain one row per account/provider")
        row = matches[0]
        return PropRiskTelemetrySnapshot(
            account_alias=str(row.get("account_alias") or ""),
            provider_id=str(row.get("provider_id") or ""),
            currency=str(row.get("currency") or ""),
            as_of_ms=int(row.get("as_of_ms")),
            source=str(row.get("source") or ""),
            source_verified=bool(row.get("source_verified")),
            daily_loss_limit=float(row.get("daily_loss_limit")),
            daily_loss_used=float(row.get("daily_loss_used", 0.0)),
            remaining_drawdown_buffer=(
                float(row["remaining_drawdown_buffer"])
                if row.get("remaining_drawdown_buffer") is not None
                else None
            ),
            nominal_account_size=(
                float(row["nominal_account_size"])
                if row.get("nominal_account_size") is not None
                else None
            ),
            program_name=(str(row["program_name"]) if row.get("program_name") else None),
            account_size=(str(row["account_size"]) if row.get("account_size") else None),
            realized_pnl_observed=(
                float(row["realized_pnl_observed"])
                if row.get("realized_pnl_observed") is not None
                else None
            ),
            unrealized_pnl_observed=(
                float(row["unrealized_pnl_observed"])
                if row.get("unrealized_pnl_observed") is not None
                else None
            ),
            daily_loss_window_id=(
                str(row["daily_loss_window_id"]) if row.get("daily_loss_window_id") else None
            ),
            drawdown_model=(str(row["drawdown_model"]) if row.get("drawdown_model") else None),
        )


@dataclass(frozen=True)
class PropRiskTelemetryValidationResult:
    status: PropRiskTelemetryStatus
    account_alias: str
    provider_id: str
    currency: str
    age_ms: int | None
    snapshot: PropRiskTelemetrySnapshot | None
    reason_codes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status is PropRiskTelemetryStatus.READY and self.snapshot is not None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "account_alias": self.account_alias,
            "provider_id": self.provider_id,
            "currency": self.currency,
            "age_ms": self.age_ms,
            "snapshot": self.snapshot.public_dict() if self.snapshot is not None else None,
            "reason_codes": list(self.reason_codes),
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }


class PropRiskTelemetryValidator:
    """Fail-closed validation of account/provider risk-state telemetry."""

    def validate(
        self,
        *,
        snapshot: PropRiskTelemetrySnapshot | None,
        account_alias: str,
        provider_id: str,
        account_currency: str,
        now_ms: int,
        policy: PropRiskTelemetryPolicy,
    ) -> PropRiskTelemetryValidationResult:
        provider = provider_id.strip().upper()
        currency = account_currency.strip().upper()
        if snapshot is None:
            return self._blocked(
                status=PropRiskTelemetryStatus.NOT_FOUND,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=None,
                reason="CURRENT_PROP_RISK_TELEMETRY_NOT_FOUND",
            )
        if snapshot.account_alias != account_alias:
            return self._blocked(
                status=PropRiskTelemetryStatus.ACCOUNT_MISMATCH,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=snapshot,
                reason="PROP_RISK_TELEMETRY_ACCOUNT_ALIAS_MISMATCH",
            )
        if snapshot.provider_id != provider:
            return self._blocked(
                status=PropRiskTelemetryStatus.PROVIDER_MISMATCH,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=snapshot,
                reason="PROP_RISK_TELEMETRY_PROVIDER_ID_MISMATCH",
            )
        if snapshot.currency != currency:
            return self._blocked(
                status=PropRiskTelemetryStatus.CURRENCY_MISMATCH,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=snapshot,
                reason="PROP_RISK_TELEMETRY_CURRENCY_MISMATCH",
            )
        if not snapshot.source_verified:
            return self._blocked(
                status=PropRiskTelemetryStatus.UNVERIFIED_SOURCE,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=snapshot,
                reason="PROP_RISK_TELEMETRY_SOURCE_NOT_VERIFIED",
            )
        if snapshot.as_of_ms > now_ms + policy.future_timestamp_tolerance_ms:
            return self._blocked(
                status=PropRiskTelemetryStatus.INVALID_TIMESTAMP,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=snapshot,
                reason="PROP_RISK_TELEMETRY_TIMESTAMP_IS_IN_THE_FUTURE",
            )
        age = max(0, now_ms - snapshot.as_of_ms)
        if age > policy.max_age_ms:
            return self._blocked(
                status=PropRiskTelemetryStatus.STALE,
                account_alias=account_alias,
                provider_id=provider,
                currency=currency,
                snapshot=snapshot,
                age_ms=age,
                reason="PROP_RISK_TELEMETRY_EXCEEDS_EXPLICIT_MAX_AGE",
            )

        return PropRiskTelemetryValidationResult(
            status=PropRiskTelemetryStatus.READY,
            account_alias=account_alias,
            provider_id=provider,
            currency=currency,
            age_ms=age,
            snapshot=snapshot,
            reason_codes=(
                "PROP_RISK_TELEMETRY_SOURCE_VERIFIED",
                "PROP_RISK_TELEMETRY_FRESH",
                "PROP_RISK_LIMITS_ARE_EXPLICIT_PROVIDER_VALUES_NOT_GENERIC_PNL_INFERENCE",
                "NOMINAL_PROP_ACCOUNT_SIZE_IS_METADATA_ONLY",
            ),
        )

    @staticmethod
    def _blocked(
        *,
        status: PropRiskTelemetryStatus,
        account_alias: str,
        provider_id: str,
        currency: str,
        snapshot: PropRiskTelemetrySnapshot | None,
        reason: str,
        age_ms: int | None = None,
    ) -> PropRiskTelemetryValidationResult:
        return PropRiskTelemetryValidationResult(
            status=status,
            account_alias=account_alias,
            provider_id=provider_id,
            currency=currency,
            age_ms=age_ms,
            snapshot=snapshot,
            reason_codes=(reason, "PROP_POSITION_SIZING_FAILS_CLOSED_UNTIL_RISK_STATE_IS_VERIFIED"),
        )
