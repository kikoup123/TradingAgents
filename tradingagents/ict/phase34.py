"""Phase 34: single-account canary deployment and activation gate.

Phase 34 adds a deliberate deployment step in front of Phase 33. Observation is
performed with an execution-disabled broker adapter and never calls submit or
reconcile. It validates the exact Phase 32 command, durable ledger state, adapter
identity/capabilities, freshness and canary risk ceiling, then persists a short-
lived attestation token in the same SQLite database.

Activation requires that exact token, the same adapter identity now explicitly
enabled for execution, a fresh Phase 31 snapshot, and a reconciliation-only
provider probe returning NOT_FOUND. Only then is the token atomically claimed and
one account handed to Phase 33. Multi-account replication expansion remains
blocked by this phase.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import OrchestrationPolicy
from tradingagents.brokers.execution import (
    BrokerExecutionAdapter,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)

from .phase32 import ExecutionCommand, Phase32LedgerState, SQLiteExecutionAuthorizationLedger
from .phase33 import (
    LondresPhase33ExactlyOnceBrokerExecutionEngine,
    Phase33BrokerBinding,
    Phase33ExecutionPolicy,
)
from .risk_sizing import ALLOWED_RISK_FRACTIONS


class Phase34CanaryStatus(str, Enum):
    READY_FOR_CANARY_ARMING = "READY_FOR_CANARY_ARMING"
    BLOCKED_PHASE32 = "BLOCKED_PHASE32"
    BLOCKED_BINDING = "BLOCKED_BINDING"
    BLOCKED_CAPABILITIES = "BLOCKED_CAPABILITIES"
    BLOCKED_STALE_COMMAND = "BLOCKED_STALE_COMMAND"
    BLOCKED_RISK = "BLOCKED_RISK"
    BLOCKED_ATTESTATION = "BLOCKED_ATTESTATION"
    BLOCKED_RECONCILIATION_PROBE = "BLOCKED_RECONCILIATION_PROBE"
    ACTIVATION_ACKNOWLEDGED = "ACTIVATION_ACKNOWLEDGED"
    ACTIVATION_FAILED_SAFE = "ACTIVATION_FAILED_SAFE"
    ACTIVATION_RECONCILIATION_REQUIRED = "ACTIVATION_RECONCILIATION_REQUIRED"


class Phase34AttestationState(str, Enum):
    OBSERVED = "OBSERVED"
    ACTIVATING = "ACTIVATING"
    COMPLETED = "COMPLETED"
    FAILED_SAFE = "FAILED_SAFE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True)
class Phase34CanaryPolicy:
    target_account_alias: str
    max_phase31_snapshot_age_ms: int
    observation_ttl_ms: int = 300_000
    max_canary_risk_fraction: float = 0.03
    execution_enabled: bool = False
    require_stop_protection: bool = True
    require_target_protection: bool = True

    def __post_init__(self) -> None:
        if not self.target_account_alias.strip():
            raise ValueError("target_account_alias is required")
        for name, value in {
            "max_phase31_snapshot_age_ms": self.max_phase31_snapshot_age_ms,
            "observation_ttl_ms": self.observation_ttl_ms,
        }.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.execution_enabled, bool):
            raise ValueError("execution_enabled must be an explicit boolean")
        if not any(
            math.isclose(self.max_canary_risk_fraction, allowed, abs_tol=1e-12)
            for allowed in ALLOWED_RISK_FRACTIONS
        ):
            raise ValueError("max_canary_risk_fraction must be one of the Londres 3/5/10% tiers")


@dataclass(frozen=True)
class Phase34CanaryBinding:
    account_alias: str
    adapter: BrokerExecutionAdapter

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")


@dataclass(frozen=True)
class Phase34Observation:
    status: Phase34CanaryStatus
    account_alias: str
    command_id: str | None
    canary_token: str | None
    observed_at_ms: int | None
    expires_at_ms: int | None
    adapter_id: str | None
    venue: str | None
    broker_type: str | None
    exact_volume: float | None
    volume_unit: str | None
    selected_risk_fraction: float | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE34_CANARY_DEPLOYMENT_OBSERVATION",
            "status": self.status.value,
            "account_alias": self.account_alias,
            "command_id": self.command_id,
            "canary_token": self.canary_token,
            "observed_at_ms": self.observed_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "adapter_id": self.adapter_id,
            "venue": self.venue,
            "broker_type": self.broker_type,
            "exact_volume": self.exact_volume,
            "volume_unit": self.volume_unit,
            "selected_risk_fraction": self.selected_risk_fraction,
            "observation_only": True,
            "single_account_canary": True,
            "replication_expansion_enabled": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class Phase34CanaryLedger:
    """Durable one-use canary attestations stored beside Phase 32/33 state."""

    def __init__(self, phase32_ledger: SQLiteExecutionAuthorizationLedger) -> None:
        self.path = phase32_ledger.path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS canary_attestations (
                    canary_token TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    broker_type TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_phase34_command "
                "ON canary_attestations(command_id, observed_at_ms)"
            )

    def record_observation(self, *, payload: dict[str, Any]) -> str:
        raw = _canonical_json(payload)
        token = _sha256_text(raw)
        digest = _sha256_text(raw)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_sha256, payload_json FROM canary_attestations WHERE canary_token = ?",
                (token,),
            ).fetchone()
            if row is not None:
                if str(row["payload_sha256"]) != digest or str(row["payload_json"]) != raw:
                    connection.rollback()
                    raise RuntimeError("Phase 34 canary token collision or payload conflict")
                connection.commit()
                return token
            connection.execute(
                """
                INSERT INTO canary_attestations (
                    canary_token, command_id, account_alias, adapter_id, venue, broker_type,
                    payload_sha256, payload_json, state, observed_at_ms, expires_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    payload["command_id"],
                    payload["account_alias"],
                    payload["adapter_id"],
                    payload["venue"],
                    payload["broker_type"],
                    digest,
                    raw,
                    Phase34AttestationState.OBSERVED.value,
                    payload["observed_at_ms"],
                    payload["expires_at_ms"],
                    payload["observed_at_ms"],
                ),
            )
            connection.commit()
        return token

    def inspect(self, token: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM canary_attestations WHERE canary_token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        raw = str(row["payload_json"])
        valid = _sha256_text(raw) == str(row["payload_sha256"]) and _sha256_text(raw) == token
        payload = None
        if valid:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                valid = False
            else:
                payload = parsed if isinstance(parsed, dict) else None
                valid = payload is not None
        return {
            "canary_token": token,
            "state": str(row["state"]),
            "integrity_valid": valid,
            "payload": payload if valid else None,
            "observed_at_ms": int(row["observed_at_ms"]),
            "expires_at_ms": int(row["expires_at_ms"]),
            "account_environment": "HIDDEN_INTERNAL",
        }

    def claim_activation(
        self,
        *,
        token: str,
        command_id: str,
        account_alias: str,
        adapter_id: str,
        now_ms: int,
    ) -> tuple[bool, str]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM canary_attestations WHERE canary_token = ?",
                (token,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False, "CANARY_ATTESTATION_NOT_FOUND"
            raw = str(row["payload_json"])
            if _sha256_text(raw) != str(row["payload_sha256"]) or _sha256_text(raw) != token:
                connection.rollback()
                return False, "CANARY_ATTESTATION_INTEGRITY_FAILURE"
            if str(row["state"]) != Phase34AttestationState.OBSERVED.value:
                connection.rollback()
                return False, "CANARY_ATTESTATION_ALREADY_CLAIMED_OR_TERMINAL"
            if now_ms > int(row["expires_at_ms"]):
                connection.rollback()
                return False, "CANARY_ATTESTATION_EXPIRED"
            if (
                str(row["command_id"]) != command_id
                or str(row["account_alias"]) != account_alias
                or str(row["adapter_id"]) != adapter_id
            ):
                connection.rollback()
                return False, "CANARY_ATTESTATION_BINDING_MISMATCH"
            updated = connection.execute(
                """
                UPDATE canary_attestations SET state = ?, updated_at_ms = ?
                WHERE canary_token = ? AND state = ?
                """,
                (
                    Phase34AttestationState.ACTIVATING.value,
                    now_ms,
                    token,
                    Phase34AttestationState.OBSERVED.value,
                ),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return False, "CANARY_ATTESTATION_ATOMIC_CLAIM_LOST_RACE"
            connection.commit()
            return True, "CANARY_ATTESTATION_ATOMICALLY_CLAIMED"
        finally:
            connection.close()

    def complete(self, *, token: str, state: Phase34AttestationState, now_ms: int) -> None:
        if state not in {
            Phase34AttestationState.COMPLETED,
            Phase34AttestationState.FAILED_SAFE,
            Phase34AttestationState.RECONCILIATION_REQUIRED,
        }:
            raise ValueError("Invalid terminal Phase 34 attestation state")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE canary_attestations SET state = ?, updated_at_ms = ?
                WHERE canary_token = ? AND state = ?
                """,
                (state.value, now_ms, token, Phase34AttestationState.ACTIVATING.value),
            )
            connection.commit()


class LondresPhase34CanaryDeploymentEngine:
    """Observe with execution disabled, then arm exactly one Phase 33 account."""

    def observe(
        self,
        *,
        phase32_plan: dict[str, Any],
        phase32_ledger: SQLiteExecutionAuthorizationLedger,
        binding: Phase34CanaryBinding,
        policy: Phase34CanaryPolicy,
        now_ms: int,
    ) -> dict[str, Any]:
        _validate_now_ms(now_ms)
        command, account, reasons = self._validated_target(
            phase32_plan=phase32_plan,
            phase32_ledger=phase32_ledger,
            binding=binding,
            policy=policy,
            now_ms=now_ms,
            require_execution_enabled=False,
        )
        if command is None or account is None:
            return self._blocked_observation(policy.target_account_alias, reasons).to_dict()

        payload = {
            "schema_version": "LONDRES_CANARY_ATTESTATION_V1",
            "command_id": command.command_id,
            "account_alias": command.account_alias,
            "adapter_id": binding.adapter.adapter_id,
            "venue": command.venue,
            "broker_type": command.broker_type,
            "broker_symbol": command.broker_symbol,
            "exact_volume": command.exact_volume,
            "volume_unit": command.volume_unit,
            "selected_risk_fraction": command.selected_risk_fraction,
            "phase30_authorization_fingerprint": command.phase30_authorization_fingerprint,
            "phase31_pre_submit_fingerprint": command.phase31_pre_submit_fingerprint,
            "quote_timestamp_ms": command.quote_timestamp_ms,
            "observed_at_ms": now_ms,
            "expires_at_ms": now_ms + policy.observation_ttl_ms,
        }
        token = Phase34CanaryLedger(phase32_ledger).record_observation(payload=payload)
        return Phase34Observation(
            status=Phase34CanaryStatus.READY_FOR_CANARY_ARMING,
            account_alias=command.account_alias,
            command_id=command.command_id,
            canary_token=token,
            observed_at_ms=now_ms,
            expires_at_ms=now_ms + policy.observation_ttl_ms,
            adapter_id=binding.adapter.adapter_id,
            venue=command.venue,
            broker_type=command.broker_type,
            exact_volume=command.exact_volume,
            volume_unit=command.volume_unit,
            selected_risk_fraction=command.selected_risk_fraction,
            reason_codes=("EXECUTION_DISABLED_CANARY_OBSERVATION_ATTESTED",),
        ).to_dict()

    def activate(
        self,
        *,
        phase32_plan: dict[str, Any],
        phase32_ledger: SQLiteExecutionAuthorizationLedger,
        binding: Phase34CanaryBinding,
        policy: Phase34CanaryPolicy,
        canary_token: str,
        now_ms: int,
    ) -> dict[str, Any]:
        _validate_now_ms(now_ms)
        if not policy.execution_enabled:
            return self._activation_blocked(
                policy.target_account_alias,
                ("PHASE34_EXPLICIT_EXECUTION_ENABLEMENT_REQUIRED",),
            )

        command, account, reasons = self._validated_target(
            phase32_plan=phase32_plan,
            phase32_ledger=phase32_ledger,
            binding=binding,
            policy=policy,
            now_ms=now_ms,
            require_execution_enabled=True,
        )
        if command is None or account is None:
            return self._activation_blocked(policy.target_account_alias, reasons)

        canary_ledger = Phase34CanaryLedger(phase32_ledger)
        attestation = canary_ledger.inspect(canary_token)
        attestation_reason = self._verify_attestation(
            attestation=attestation,
            command=command,
            binding=binding,
            now_ms=now_ms,
        )
        if attestation_reason is not None:
            return self._activation_blocked(command.account_alias, (attestation_reason,))

        try:
            probe = binding.adapter.reconcile(command.to_dict(), now_ms=now_ms)
        except Exception as exc:
            return self._activation_blocked(
                command.account_alias,
                (f"CANARY_RECONCILIATION_PROBE_EXCEPTION_{type(exc).__name__}",),
                status=Phase34CanaryStatus.BLOCKED_RECONCILIATION_PROBE,
            )
        if probe.outcome is not BrokerExecutionOutcome.NOT_FOUND:
            return self._activation_blocked(
                command.account_alias,
                ("CANARY_RECONCILIATION_PROBE_MUST_PROVE_NO_EXISTING_COMMAND_EVIDENCE",),
                status=Phase34CanaryStatus.BLOCKED_RECONCILIATION_PROBE,
                probe=probe,
            )

        claimed, claim_reason = canary_ledger.claim_activation(
            token=canary_token,
            command_id=command.command_id,
            account_alias=command.account_alias,
            adapter_id=binding.adapter.adapter_id,
            now_ms=now_ms,
        )
        if not claimed:
            return self._activation_blocked(command.account_alias, (claim_reason,))

        single_account_plan = dict(phase32_plan)
        single_account_plan["policy"] = OrchestrationPolicy.BEST_EFFORT.value
        single_account_plan["accounts"] = [account]
        single_account_plan["enabled_accounts"] = 1
        single_account_plan["shadow_ready_accounts"] = 1
        single_account_plan["blocked_accounts"] = 0
        single_account_plan["skipped_accounts"] = 0
        single_account_plan["shadow_ready"] = True

        try:
            result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
                phase32_plan=single_account_plan,
                phase32_ledger=phase32_ledger,
                bindings=(
                    Phase33BrokerBinding(
                        account_alias=command.account_alias,
                        adapter=binding.adapter,
                    ),
                ),
                execution_policy=Phase33ExecutionPolicy(
                    execution_enabled=True,
                    max_phase31_snapshot_age_ms=policy.max_phase31_snapshot_age_ms,
                    require_stop_protection=policy.require_stop_protection,
                    require_target_protection=policy.require_target_protection,
                ),
                now_ms=now_ms,
            )
        except Exception as exc:
            canary_ledger.complete(
                token=canary_token,
                state=Phase34AttestationState.RECONCILIATION_REQUIRED,
                now_ms=now_ms,
            )
            return self._activation_blocked(
                command.account_alias,
                (f"PHASE33_CANARY_EXECUTION_EXCEPTION_{type(exc).__name__}",),
                status=Phase34CanaryStatus.ACTIVATION_RECONCILIATION_REQUIRED,
                probe=probe,
                token_consumed=True,
            )

        account_result = result.get("accounts", [{}])[0] if result.get("accounts") else {}
        raw_status = str(account_result.get("status") or "")
        acknowledged = raw_status in {
            "ACKNOWLEDGED",
            "IDEMPOTENT_ACKNOWLEDGED",
            "RECOVERED_ACKNOWLEDGED",
        }
        failed_safe = raw_status == "FAILED_SAFE"
        if acknowledged:
            terminal = Phase34AttestationState.COMPLETED
            status = Phase34CanaryStatus.ACTIVATION_ACKNOWLEDGED
        elif failed_safe:
            terminal = Phase34AttestationState.FAILED_SAFE
            status = Phase34CanaryStatus.ACTIVATION_FAILED_SAFE
        else:
            terminal = Phase34AttestationState.RECONCILIATION_REQUIRED
            status = Phase34CanaryStatus.ACTIVATION_RECONCILIATION_REQUIRED
        canary_ledger.complete(token=canary_token, state=terminal, now_ms=now_ms)
        return {
            "phase": "LONDRES_PHASE34_SINGLE_ACCOUNT_CANARY_ACTIVATION",
            "status": status.value,
            "account_alias": command.account_alias,
            "command_id": command.command_id,
            "canary_token": canary_token,
            "canary_token_consumed": True,
            "reconciliation_probe": probe.public_dict(),
            "phase33_result": result,
            "single_account_canary": True,
            "replication_expansion_enabled": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": True,
            "order_submission_enabled": True,
            "canary_acknowledged": acknowledged,
            "reason_codes": [claim_reason, "SINGLE_ACCOUNT_PHASE33_CANARY_HANDOFF_COMPLETED"],
        }

    def _validated_target(
        self,
        *,
        phase32_plan: dict[str, Any],
        phase32_ledger: SQLiteExecutionAuthorizationLedger,
        binding: Phase34CanaryBinding,
        policy: Phase34CanaryPolicy,
        now_ms: int,
        require_execution_enabled: bool,
    ) -> tuple[ExecutionCommand | None, dict[str, Any] | None, tuple[str, ...]]:
        reasons: list[str] = []
        if not str(phase32_plan.get("phase") or "").startswith("LONDRES_PHASE32"):
            reasons.append("PHASE32_PLAN_REQUIRED")
        if phase32_plan.get("order_submission_enabled") is not False:
            reasons.append("PHASE32_PLAN_MUST_REMAIN_SHADOW_ONLY")
        accounts = phase32_plan.get("accounts")
        if not isinstance(accounts, list):
            reasons.append("PHASE32_ACCOUNT_LIST_REQUIRED")
            return None, None, tuple(reasons)
        matches = [
            account
            for account in accounts
            if str(account.get("account_alias") or "") == policy.target_account_alias
        ]
        if len(matches) != 1:
            reasons.append("EXACTLY_ONE_CANARY_TARGET_ACCOUNT_REQUIRED")
            return None, None, tuple(reasons)
        account = matches[0]
        if account.get("status") not in {"SHADOW_READY", "IDEMPOTENT_SHADOW_READY"}:
            reasons.append("CANARY_ACCOUNT_MUST_BE_PHASE32_SHADOW_READY")
        if account.get("authorization_reserved") is not True or account.get("shadow_ready") is not True:
            reasons.append("CANARY_ACCOUNT_REQUIRES_DURABLE_PHASE32_RESERVATION")
        raw = account.get("command")
        if not isinstance(raw, dict):
            reasons.append("CANARY_EXECUTION_COMMAND_REQUIRED")
            return None, account, tuple(reasons)
        try:
            command = ExecutionCommand(**raw)
        except (TypeError, ValueError):
            reasons.append("CANARY_EXECUTION_COMMAND_SCHEMA_INVALID")
            return None, account, tuple(reasons)
        if not command.integrity_valid():
            reasons.append("CANARY_EXECUTION_COMMAND_INTEGRITY_INVALID")
        if command.account_alias != policy.target_account_alias or binding.account_alias != command.account_alias:
            reasons.append("CANARY_ACCOUNT_BINDING_MISMATCH")
        if binding.adapter.venue != command.venue or binding.adapter.broker_type.value != command.broker_type:
            reasons.append("CANARY_ADAPTER_VENUE_OR_TYPE_MISMATCH")

        capabilities = binding.adapter.execution_capabilities()
        if capabilities.venue != command.venue or capabilities.broker_type.value != command.broker_type:
            reasons.append("CANARY_CAPABILITIES_IDENTITY_MISMATCH")
        if capabilities.execution_enabled is not require_execution_enabled:
            reasons.append(
                "CANARY_EXECUTION_ADAPTER_MUST_BE_ENABLED_FOR_ACTIVATION"
                if require_execution_enabled
                else "CANARY_OBSERVATION_REQUIRES_EXECUTION_ADAPTER_DISABLED"
            )
        if not capabilities.supports_market_orders:
            reasons.append("CANARY_MARKET_ORDER_CAPABILITY_REQUIRED")
        if not capabilities.supports_reconciliation:
            reasons.append("CANARY_RECONCILIATION_CAPABILITY_REQUIRED")
        if policy.require_stop_protection and not capabilities.supports_server_side_stop:
            reasons.append("CANARY_SERVER_SIDE_STOP_CAPABILITY_REQUIRED")
        if policy.require_target_protection and not capabilities.supports_server_side_target:
            reasons.append("CANARY_SERVER_SIDE_TARGET_CAPABILITY_REQUIRED")

        ledger_row = phase32_ledger.inspect(command.command_id)
        if (
            ledger_row is None
            or ledger_row.get("integrity_valid") is not True
            or ledger_row.get("state") != Phase32LedgerState.SHADOW_READY.value
            or ledger_row.get("command") != command.to_dict()
        ):
            reasons.append("CANARY_COMMAND_NOT_EXACTLY_SHADOW_READY_IN_DURABLE_LEDGER")

        timestamp = command.quote_timestamp_ms
        if timestamp is None or now_ms < timestamp or now_ms - timestamp > policy.max_phase31_snapshot_age_ms:
            reasons.append("CANARY_PHASE31_SNAPSHOT_STALE_OR_INVALID")
        if command.selected_risk_fraction > policy.max_canary_risk_fraction + 1e-12:
            reasons.append("CANARY_SELECTED_RISK_EXCEEDS_EXPLICIT_CANARY_CEILING")

        if reasons:
            return None, account, tuple(reasons)
        return command, account, ()

    @staticmethod
    def _verify_attestation(
        *,
        attestation: dict[str, Any] | None,
        command: ExecutionCommand,
        binding: Phase34CanaryBinding,
        now_ms: int,
    ) -> str | None:
        if attestation is None:
            return "CANARY_ATTESTATION_NOT_FOUND"
        if attestation.get("integrity_valid") is not True:
            return "CANARY_ATTESTATION_INTEGRITY_FAILURE"
        if attestation.get("state") != Phase34AttestationState.OBSERVED.value:
            return "CANARY_ATTESTATION_NOT_IN_OBSERVED_STATE"
        if now_ms > int(attestation.get("expires_at_ms") or -1):
            return "CANARY_ATTESTATION_EXPIRED"
        payload = attestation.get("payload")
        if not isinstance(payload, dict):
            return "CANARY_ATTESTATION_PAYLOAD_MISSING"
        expected = {
            "command_id": command.command_id,
            "account_alias": command.account_alias,
            "adapter_id": binding.adapter.adapter_id,
            "venue": command.venue,
            "broker_type": command.broker_type,
            "broker_symbol": command.broker_symbol,
            "exact_volume": command.exact_volume,
            "volume_unit": command.volume_unit,
            "selected_risk_fraction": command.selected_risk_fraction,
            "phase30_authorization_fingerprint": command.phase30_authorization_fingerprint,
            "phase31_pre_submit_fingerprint": command.phase31_pre_submit_fingerprint,
            "quote_timestamp_ms": command.quote_timestamp_ms,
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                return f"CANARY_ATTESTATION_FIELD_MISMATCH_{key.upper()}"
        return None

    @staticmethod
    def _blocked_observation(
        account_alias: str,
        reasons: tuple[str, ...],
    ) -> Phase34Observation:
        status = Phase34CanaryStatus.BLOCKED_PHASE32
        if any("RISK" in reason for reason in reasons):
            status = Phase34CanaryStatus.BLOCKED_RISK
        elif any("STALE" in reason for reason in reasons):
            status = Phase34CanaryStatus.BLOCKED_STALE_COMMAND
        elif any("CAPABILITY" in reason for reason in reasons):
            status = Phase34CanaryStatus.BLOCKED_CAPABILITIES
        elif any("BINDING" in reason or "ADAPTER" in reason for reason in reasons):
            status = Phase34CanaryStatus.BLOCKED_BINDING
        return Phase34Observation(
            status=status,
            account_alias=account_alias,
            command_id=None,
            canary_token=None,
            observed_at_ms=None,
            expires_at_ms=None,
            adapter_id=None,
            venue=None,
            broker_type=None,
            exact_volume=None,
            volume_unit=None,
            selected_risk_fraction=None,
            reason_codes=reasons,
        )

    @staticmethod
    def _activation_blocked(
        account_alias: str,
        reasons: tuple[str, ...],
        *,
        status: Phase34CanaryStatus = Phase34CanaryStatus.BLOCKED_ATTESTATION,
        probe: BrokerExecutionReceipt | None = None,
        token_consumed: bool = False,
    ) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE34_SINGLE_ACCOUNT_CANARY_ACTIVATION",
            "status": status.value,
            "account_alias": account_alias,
            "canary_token_consumed": token_consumed,
            "reconciliation_probe": probe.public_dict() if probe is not None else None,
            "phase33_result": None,
            "single_account_canary": True,
            "replication_expansion_enabled": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "canary_acknowledged": False,
            "reason_codes": list(reasons),
        }


def _validate_now_ms(now_ms: int) -> None:
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
