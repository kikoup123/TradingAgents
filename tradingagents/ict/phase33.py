"""Phase 33: exactly-once broker submission and reconciliation.

Phase 33 is the first Londres phase allowed to call a broker execution adapter.
It consumes only durable Phase 32 commands, atomically claims each command in the
same SQLite ledger, and never blindly retries an uncertain submission.

Exactly-once in this phase means exactly-once *command submission semantics* from
Londres. A broker/network timeout cannot prove that no order was placed, so an
uncertain outcome is locked as AMBIGUOUS/RECONCILIATION_REQUIRED until broker
state is reconciled.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import BrokerType, OrchestrationPolicy
from tradingagents.brokers.execution import (
    BrokerExecutionAdapter,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)

from .multi_account import MultiAccountBatchStatus
from .phase32 import ExecutionCommand, Phase32LedgerState, SQLiteExecutionAuthorizationLedger


class Phase33ClaimAction(str, Enum):
    SUBMIT = "SUBMIT"
    RECONCILE = "RECONCILE"
    IDEMPOTENT_ACK = "IDEMPOTENT_ACK"
    TERMINAL_FAILED_SAFE = "TERMINAL_FAILED_SAFE"
    BLOCKED = "BLOCKED"


class Phase33AccountStatus(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IDEMPOTENT_ACKNOWLEDGED = "IDEMPOTENT_ACKNOWLEDGED"
    RECOVERED_ACKNOWLEDGED = "RECOVERED_ACKNOWLEDGED"
    FAILED_SAFE = "FAILED_SAFE"
    AMBIGUOUS = "AMBIGUOUS"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_PHASE32 = "BLOCKED_PHASE32"
    BLOCKED_BINDING = "BLOCKED_BINDING"
    BLOCKED_EXECUTION_DISABLED = "BLOCKED_EXECUTION_DISABLED"
    BLOCKED_STALE_COMMAND = "BLOCKED_STALE_COMMAND"
    BLOCKED_LEDGER = "BLOCKED_LEDGER"
    BLOCKED_POLICY = "BLOCKED_POLICY"


@dataclass(frozen=True)
class Phase33ExecutionPolicy:
    """Explicit live-execution gate and freshness/protection requirements."""

    execution_enabled: bool
    max_phase31_snapshot_age_ms: int
    require_stop_protection: bool = True
    require_target_protection: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.execution_enabled, bool):
            raise ValueError("execution_enabled must be an explicit boolean")
        if (
            isinstance(self.max_phase31_snapshot_age_ms, bool)
            or not isinstance(self.max_phase31_snapshot_age_ms, int)
            or self.max_phase31_snapshot_age_ms < 0
        ):
            raise ValueError("max_phase31_snapshot_age_ms must be a non-negative integer")


@dataclass(frozen=True)
class Phase33BrokerBinding:
    account_alias: str
    adapter: BrokerExecutionAdapter

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")


@dataclass(frozen=True)
class Phase33ClaimResult:
    command_id: str
    action: Phase33ClaimAction
    state: Phase32LedgerState | None
    ledger_mutated: bool
    reason: str


@dataclass(frozen=True)
class Phase33TransitionResult:
    command_id: str
    state: Phase32LedgerState
    ledger_mutated: bool
    reason: str


class Phase33ExecutionLedger:
    """Submission/reconciliation transitions on the same durable Phase 32 DB."""

    _ALLOWED_TRANSITIONS = {
        Phase32LedgerState.SUBMITTING: {
            Phase32LedgerState.ACKNOWLEDGED,
            Phase32LedgerState.AMBIGUOUS,
            Phase32LedgerState.FAILED_SAFE,
            Phase32LedgerState.RECONCILIATION_REQUIRED,
        },
        Phase32LedgerState.AMBIGUOUS: {
            Phase32LedgerState.ACKNOWLEDGED,
            Phase32LedgerState.FAILED_SAFE,
            Phase32LedgerState.RECONCILIATION_REQUIRED,
        },
        Phase32LedgerState.RECONCILIATION_REQUIRED: {
            Phase32LedgerState.ACKNOWLEDGED,
            Phase32LedgerState.FAILED_SAFE,
            Phase32LedgerState.RECONCILIATION_REQUIRED,
        },
    }

    def __init__(self, phase32_ledger: SQLiteExecutionAuthorizationLedger) -> None:
        self.phase32_ledger = phase32_ledger
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
                CREATE TABLE IF NOT EXISTS execution_receipts (
                    command_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_phase33_events_command "
                "ON execution_events(command_id, event_id)"
            )

    def claim(self, *, command: ExecutionCommand, now_ms: int) -> Phase33ClaimResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM execution_commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return Phase33ClaimResult(
                    command_id=command.command_id,
                    action=Phase33ClaimAction.BLOCKED,
                    state=None,
                    ledger_mutated=False,
                    reason="PHASE32_COMMAND_NOT_FOUND_IN_DURABLE_LEDGER",
                )

            verified, reason = self._verify_row(row=row, command=command)
            if not verified:
                connection.execute(
                    "UPDATE execution_commands SET state = ?, updated_at_ms = ? WHERE command_id = ?",
                    (
                        Phase32LedgerState.RECONCILIATION_REQUIRED.value,
                        now_ms,
                        command.command_id,
                    ),
                )
                connection.commit()
                return Phase33ClaimResult(
                    command_id=command.command_id,
                    action=Phase33ClaimAction.BLOCKED,
                    state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                    ledger_mutated=True,
                    reason=reason,
                )

            state = self._state(row["state"])
            if state is Phase32LedgerState.SHADOW_READY:
                updated = connection.execute(
                    """
                    UPDATE execution_commands
                    SET state = ?, updated_at_ms = ?
                    WHERE command_id = ? AND state = ?
                    """,
                    (
                        Phase32LedgerState.SUBMITTING.value,
                        now_ms,
                        command.command_id,
                        Phase32LedgerState.SHADOW_READY.value,
                    ),
                ).rowcount
                if updated != 1:
                    connection.rollback()
                    return Phase33ClaimResult(
                        command_id=command.command_id,
                        action=Phase33ClaimAction.BLOCKED,
                        state=None,
                        ledger_mutated=False,
                        reason="ATOMIC_PHASE33_SUBMISSION_CLAIM_LOST_RACE",
                    )
                connection.commit()
                return Phase33ClaimResult(
                    command_id=command.command_id,
                    action=Phase33ClaimAction.SUBMIT,
                    state=Phase32LedgerState.SUBMITTING,
                    ledger_mutated=True,
                    reason="PHASE32_COMMAND_ATOMICALLY_CLAIMED_FOR_SINGLE_SUBMISSION",
                )

            connection.commit()
            if state is Phase32LedgerState.ACKNOWLEDGED:
                return Phase33ClaimResult(
                    command_id=command.command_id,
                    action=Phase33ClaimAction.IDEMPOTENT_ACK,
                    state=state,
                    ledger_mutated=False,
                    reason="COMMAND_ALREADY_ACKNOWLEDGED_NO_RESUBMISSION",
                )
            if state is Phase32LedgerState.FAILED_SAFE:
                return Phase33ClaimResult(
                    command_id=command.command_id,
                    action=Phase33ClaimAction.TERMINAL_FAILED_SAFE,
                    state=state,
                    ledger_mutated=False,
                    reason="COMMAND_ALREADY_FAILED_SAFE_NO_RESUBMISSION",
                )
            if state in {
                Phase32LedgerState.SUBMITTING,
                Phase32LedgerState.AMBIGUOUS,
                Phase32LedgerState.RECONCILIATION_REQUIRED,
            }:
                return Phase33ClaimResult(
                    command_id=command.command_id,
                    action=Phase33ClaimAction.RECONCILE,
                    state=state,
                    ledger_mutated=False,
                    reason="INFLIGHT_OR_UNCERTAIN_COMMAND_MUST_RECONCILE_NOT_RESUBMIT",
                )
            return Phase33ClaimResult(
                command_id=command.command_id,
                action=Phase33ClaimAction.BLOCKED,
                state=state,
                ledger_mutated=False,
                reason="UNSUPPORTED_LEDGER_STATE_FOR_PHASE33",
            )
        finally:
            connection.close()

    def record_receipt(
        self,
        *,
        command_id: str,
        new_state: Phase32LedgerState,
        receipt: BrokerExecutionReceipt,
        now_ms: int,
    ) -> Phase33TransitionResult:
        receipt_payload = receipt.public_dict()
        receipt_json = _canonical_json(receipt_payload)
        receipt_hash = _sha256_text(receipt_json)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM execution_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return Phase33TransitionResult(
                    command_id=command_id,
                    state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                    ledger_mutated=False,
                    reason="COMMAND_MISSING_WHILE_RECORDING_BROKER_RECEIPT",
                )
            current = self._state(row["state"])

            existing = connection.execute(
                "SELECT * FROM execution_receipts WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if current is new_state and existing is not None:
                exact = bool(
                    str(existing["state"]) == new_state.value
                    and str(existing["receipt_sha256"]) == receipt_hash
                    and str(existing["receipt_json"]) == receipt_json
                )
                if exact:
                    connection.commit()
                    return Phase33TransitionResult(
                        command_id=command_id,
                        state=new_state,
                        ledger_mutated=False,
                        reason="IDENTICAL_BROKER_RECEIPT_ALREADY_PERSISTED",
                    )

            allowed = self._ALLOWED_TRANSITIONS.get(current, set())
            if new_state not in allowed:
                connection.execute(
                    "UPDATE execution_commands SET state = ?, updated_at_ms = ? WHERE command_id = ?",
                    (
                        Phase32LedgerState.RECONCILIATION_REQUIRED.value,
                        now_ms,
                        command_id,
                    ),
                )
                connection.commit()
                return Phase33TransitionResult(
                    command_id=command_id,
                    state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                    ledger_mutated=True,
                    reason="ILLEGAL_EXECUTION_STATE_TRANSITION_FORCED_RECONCILIATION",
                )

            connection.execute(
                "UPDATE execution_commands SET state = ?, updated_at_ms = ? WHERE command_id = ?",
                (new_state.value, now_ms, command_id),
            )
            connection.execute(
                """
                INSERT INTO execution_receipts (
                    command_id, state, receipt_sha256, receipt_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(command_id) DO UPDATE SET
                    state = excluded.state,
                    receipt_sha256 = excluded.receipt_sha256,
                    receipt_json = excluded.receipt_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (command_id, new_state.value, receipt_hash, receipt_json, now_ms),
            )
            connection.execute(
                """
                INSERT INTO execution_events (
                    command_id, from_state, to_state,
                    receipt_sha256, receipt_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    current.value,
                    new_state.value,
                    receipt_hash,
                    receipt_json,
                    now_ms,
                ),
            )
            connection.commit()
            return Phase33TransitionResult(
                command_id=command_id,
                state=new_state,
                ledger_mutated=True,
                reason="BROKER_EXECUTION_RECEIPT_ATOMICALLY_PERSISTED",
            )
        finally:
            connection.close()

    def receipt(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_receipts WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            return None
        raw = str(row["receipt_json"])
        valid = _sha256_text(raw) == str(row["receipt_sha256"])
        if not valid:
            return {
                "command_id": command_id,
                "state": Phase32LedgerState.RECONCILIATION_REQUIRED.value,
                "integrity_valid": False,
                "receipt": None,
                "reason": "EXECUTION_RECEIPT_HASH_MISMATCH_RECONCILIATION_REQUIRED",
            }
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "command_id": command_id,
                "state": Phase32LedgerState.RECONCILIATION_REQUIRED.value,
                "integrity_valid": False,
                "receipt": None,
                "reason": "EXECUTION_RECEIPT_JSON_INVALID_RECONCILIATION_REQUIRED",
            }
        return {
            "command_id": command_id,
            "state": str(row["state"]),
            "integrity_valid": True,
            "receipt": payload,
            "reason": "EXECUTION_RECEIPT_HASH_VERIFIED",
        }

    def event_count(self, command_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM execution_events WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _verify_row(*, row: sqlite3.Row, command: ExecutionCommand) -> tuple[bool, str]:
        stored_json = str(row["command_json"])
        stored_hash = str(row["payload_sha256"])
        expected_json = _canonical_json(command.to_dict())
        expected_hash = _sha256_text(expected_json)
        if _sha256_text(stored_json) != stored_hash:
            return False, "PHASE32_LEDGER_COMMAND_PAYLOAD_HASH_MISMATCH"
        if stored_hash != expected_hash or stored_json != expected_json:
            return False, "PHASE32_LEDGER_COMMAND_DOES_NOT_MATCH_PHASE33_COMMAND"
        if str(row["phase30_fingerprint"]) != command.phase30_authorization_fingerprint:
            return False, "PHASE30_FINGERPRINT_CHANGED_BEFORE_PHASE33"
        if str(row["phase31_fingerprint"]) != command.phase31_pre_submit_fingerprint:
            return False, "PHASE31_FINGERPRINT_CHANGED_BEFORE_PHASE33"
        return True, "PHASE32_LEDGER_COMMAND_VERIFIED"

    @staticmethod
    def _state(value: Any) -> Phase32LedgerState:
        try:
            return Phase32LedgerState(str(value))
        except ValueError:
            return Phase32LedgerState.RECONCILIATION_REQUIRED


@dataclass(frozen=True)
class Phase33AccountExecution:
    account_alias: str
    venue: str | None
    broker_type: str | None
    status: Phase33AccountStatus
    command_id: str | None
    client_order_label: str | None
    ledger_state: Phase32LedgerState | None
    receipt: BrokerExecutionReceipt | None
    authorization_reserved: bool
    authorization_consumed: bool
    authorization_locked_against_retry: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_alias": self.account_alias,
            "venue": self.venue,
            "broker_type": self.broker_type,
            "status": self.status.value,
            "command_id": self.command_id,
            "client_order_label": self.client_order_label,
            "ledger_state": self.ledger_state.value if self.ledger_state else None,
            "receipt": self.receipt.public_dict() if self.receipt else None,
            "authorization_reserved": self.authorization_reserved,
            "authorization_consumed": self.authorization_consumed,
            "authorization_locked_against_retry": self.authorization_locked_against_retry,
            "automatic_retry_allowed": False,
            "broker_order_placed": self.broker_order_placed,
            "authorized_volume_resized": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class Phase33ExecutionBatch:
    trade_id: str
    canonical_symbol: str
    direction: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase33AccountExecution, ...]
    enabled_accounts: int
    acknowledged_accounts: int
    failed_safe_accounts: int
    uncertain_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    execution_enabled: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE33_EXACTLY_ONCE_BROKER_EXECUTION_RECONCILIATION",
            "trade_id": self.trade_id,
            "canonical_symbol": self.canonical_symbol,
            "direction": self.direction,
            "policy": self.policy.value,
            "status": self.status.value,
            "accounts": [item.to_dict() for item in self.accounts],
            "enabled_accounts": self.enabled_accounts,
            "acknowledged_accounts": self.acknowledged_accounts,
            "failed_safe_accounts": self.failed_safe_accounts,
            "uncertain_accounts": self.uncertain_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "execution_enabled": self.execution_enabled,
            "order_submission_enabled": self.execution_enabled,
            "distributed_all_or_none_guaranteed": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase33ExactlyOnceBrokerExecutionEngine:
    """Claim one Phase 32 command, submit once, then reconcile uncertainty."""

    def execute(
        self,
        *,
        phase32_plan: dict[str, Any],
        phase32_ledger: SQLiteExecutionAuthorizationLedger,
        bindings: tuple[Phase33BrokerBinding, ...] | list[Phase33BrokerBinding],
        execution_policy: Phase33ExecutionPolicy,
        now_ms: int,
    ) -> dict[str, Any]:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        aliases = [binding.account_alias for binding in bindings]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Phase 33 broker bindings require unique account aliases")
        binding_map = {binding.account_alias: binding for binding in bindings}
        ledger = Phase33ExecutionLedger(phase32_ledger)

        policy, global_reasons = self._phase32_contract(phase32_plan)
        effective_policy = policy or OrchestrationPolicy.BEST_EFFORT
        raw_accounts = (
            phase32_plan.get("accounts") if isinstance(phase32_plan.get("accounts"), list) else []
        )
        enabled_count = sum(item.get("status") != "SKIPPED_DISABLED" for item in raw_accounts)

        if (
            execution_policy.execution_enabled
            and effective_policy is OrchestrationPolicy.ALL_OR_NONE
            and enabled_count > 1
        ):
            global_reasons = (
                *global_reasons,
                "MULTI_BROKER_ALL_OR_NONE_LIVE_EXECUTION_CANNOT_BE_ATOMICALLY_GUARANTEED",
            )

        results = tuple(
            self._execute_account(
                account=account,
                binding=binding_map.get(str(account.get("account_alias") or "")),
                ledger=ledger,
                execution_policy=execution_policy,
                now_ms=now_ms,
                global_reasons=global_reasons,
            )
            for account in raw_accounts
        )
        return self._batch(
            phase32_plan=phase32_plan,
            policy=effective_policy,
            results=results,
            execution_policy=execution_policy,
            global_reasons=global_reasons,
        ).to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"exactly_once_broker_execution_state": context}

    @staticmethod
    def _phase32_contract(
        plan: dict[str, Any],
    ) -> tuple[OrchestrationPolicy | None, tuple[str, ...]]:
        reasons: list[str] = []
        try:
            policy = OrchestrationPolicy(str(plan.get("policy")))
        except ValueError:
            policy = None
            reasons.append("VALID_PHASE32_ORCHESTRATION_POLICY_REQUIRED")
        if not str(plan.get("phase") or "").startswith("LONDRES_PHASE32"):
            reasons.append("PHASE32_PLAN_PHASE_MARKER_REQUIRED")
        if not str(plan.get("trade_id") or "").strip():
            reasons.append("PHASE32_TRADE_ID_REQUIRED")
        if not str(plan.get("canonical_symbol") or "").strip():
            reasons.append("PHASE32_CANONICAL_SYMBOL_REQUIRED")
        if str(plan.get("direction") or "") not in {"BULLISH", "BEARISH"}:
            reasons.append("PHASE32_DIRECTION_REQUIRED")
        if plan.get("order_submission_enabled") is not False:
            reasons.append("PHASE32_MUST_REMAIN_SHADOW_ONLY")
        if not isinstance(plan.get("accounts"), list):
            reasons.append("PHASE32_ACCOUNT_LIST_REQUIRED")
        return policy, tuple(reasons)

    def _execute_account(
        self,
        *,
        account: dict[str, Any],
        binding: Phase33BrokerBinding | None,
        ledger: Phase33ExecutionLedger,
        execution_policy: Phase33ExecutionPolicy,
        now_ms: int,
        global_reasons: tuple[str, ...],
    ) -> Phase33AccountExecution:
        alias = str(account.get("account_alias") or "")
        venue = str(account.get("venue") or "") or None
        broker_type = str(account.get("broker_type") or "") or None
        if account.get("status") == "SKIPPED_DISABLED":
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.SKIPPED_DISABLED,
                reason="ACCOUNT_DISABLED_BEFORE_PHASE33",
            )
        if global_reasons:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.BLOCKED_POLICY,
                reason=global_reasons[0],
                extra_reasons=global_reasons[1:],
            )
        if not execution_policy.execution_enabled:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.BLOCKED_EXECUTION_DISABLED,
                reason="PHASE33_LIVE_EXECUTION_REQUIRES_EXPLICIT_ENABLEMENT",
            )

        command, reasons = self._command_from_account(account)
        if command is None:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.BLOCKED_PHASE32,
                reason=reasons[0] if reasons else "VALID_PHASE32_COMMAND_REQUIRED",
                extra_reasons=reasons[1:],
            )
        freshness = self._freshness_reason(
            command=command,
            now_ms=now_ms,
            max_age_ms=execution_policy.max_phase31_snapshot_age_ms,
        )
        if freshness is not None:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.BLOCKED_STALE_COMMAND,
                reason=freshness,
                command=command,
            )
        binding_reason = self._binding_reason(
            command=command,
            binding=binding,
            execution_policy=execution_policy,
        )
        if binding_reason is not None:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.BLOCKED_BINDING,
                reason=binding_reason,
                command=command,
            )
        assert binding is not None

        claim = ledger.claim(command=command, now_ms=now_ms)
        if claim.action is Phase33ClaimAction.BLOCKED:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.BLOCKED_LEDGER,
                reason=claim.reason,
                command=command,
                ledger_state=claim.state,
            )
        if claim.action is Phase33ClaimAction.IDEMPOTENT_ACK:
            stored = ledger.receipt(command.command_id)
            receipt = self._receipt_from_stored(stored)
            if receipt is None or not self._receipt_matches_command(receipt, command):
                return self._blocked(
                    alias=alias,
                    venue=venue,
                    broker_type=broker_type,
                    status=Phase33AccountStatus.RECONCILIATION_REQUIRED,
                    reason="ACKNOWLEDGED_LEDGER_STATE_REQUIRES_VALID_STORED_RECEIPT",
                    command=command,
                    ledger_state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                )
            return self._success(
                command=command,
                receipt=receipt,
                status=Phase33AccountStatus.IDEMPOTENT_ACKNOWLEDGED,
                state=Phase32LedgerState.ACKNOWLEDGED,
                reason="COMMAND_ALREADY_ACKNOWLEDGED_NO_BROKER_RESUBMISSION",
            )
        if claim.action is Phase33ClaimAction.TERMINAL_FAILED_SAFE:
            return self._blocked(
                alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase33AccountStatus.FAILED_SAFE,
                reason="COMMAND_ALREADY_FAILED_SAFE_NEW_AUTHORIZATION_REQUIRED",
                command=command,
                ledger_state=Phase32LedgerState.FAILED_SAFE,
                locked=True,
            )

        if claim.action is Phase33ClaimAction.RECONCILE:
            receipt = self._safe_reconcile(binding.adapter, command=command, now_ms=now_ms)
            return self._finalize_receipt(
                command=command,
                receipt=receipt,
                ledger=ledger,
                now_ms=now_ms,
                execution_policy=execution_policy,
                reconciliation=True,
            )

        receipt = self._safe_submit(binding.adapter, command=command, now_ms=now_ms)
        return self._finalize_receipt(
            command=command,
            receipt=receipt,
            ledger=ledger,
            now_ms=now_ms,
            execution_policy=execution_policy,
            reconciliation=False,
        )

    @staticmethod
    def _command_from_account(
        account: dict[str, Any],
    ) -> tuple[ExecutionCommand | None, tuple[str, ...]]:
        reasons: list[str] = []
        if account.get("status") not in {"SHADOW_READY", "IDEMPOTENT_SHADOW_READY"}:
            reasons.append("PHASE32_ACCOUNT_MUST_BE_SHADOW_READY")
        if account.get("authorization_reserved") is not True:
            reasons.append("PHASE32_AUTHORIZATION_RESERVATION_REQUIRED")
        if account.get("shadow_ready") is not True:
            reasons.append("PHASE32_SHADOW_READY_FLAG_REQUIRED")
        raw = account.get("command")
        if not isinstance(raw, dict):
            reasons.append("PHASE32_EXECUTION_COMMAND_REQUIRED")
            return None, tuple(reasons)
        try:
            command = ExecutionCommand(**raw)
        except (TypeError, ValueError):
            reasons.append("PHASE32_EXECUTION_COMMAND_SCHEMA_INVALID")
            return None, tuple(reasons)
        if not command.integrity_valid():
            reasons.append("PHASE32_EXECUTION_COMMAND_INTEGRITY_INVALID")
        if command.account_alias != str(account.get("account_alias") or ""):
            reasons.append("PHASE32_COMMAND_ACCOUNT_ALIAS_MISMATCH")
        if command.venue != str(account.get("venue") or ""):
            reasons.append("PHASE32_COMMAND_VENUE_MISMATCH")
        if command.broker_type != str(account.get("broker_type") or ""):
            reasons.append("PHASE32_COMMAND_BROKER_TYPE_MISMATCH")
        return (command if not reasons else None), tuple(reasons)

    @staticmethod
    def _freshness_reason(
        *,
        command: ExecutionCommand,
        now_ms: int,
        max_age_ms: int,
    ) -> str | None:
        timestamp = command.quote_timestamp_ms
        if timestamp is None:
            return "PHASE31_QUOTE_TIMESTAMP_REQUIRED_FOR_LIVE_EXECUTION"
        if timestamp > now_ms:
            return "PHASE31_QUOTE_TIMESTAMP_CANNOT_BE_IN_THE_FUTURE"
        if now_ms - timestamp > max_age_ms:
            return "PHASE31_PRE_SUBMIT_SNAPSHOT_EXPIRED_BEFORE_LIVE_EXECUTION"
        return None

    @staticmethod
    def _binding_reason(
        *,
        command: ExecutionCommand,
        binding: Phase33BrokerBinding | None,
        execution_policy: Phase33ExecutionPolicy,
    ) -> str | None:
        if binding is None or binding.account_alias != command.account_alias:
            return "EXACT_PHASE33_ACCOUNT_EXECUTION_BINDING_REQUIRED"
        adapter = binding.adapter
        if adapter.broker_type.value != command.broker_type:
            return "PHASE33_EXECUTION_ADAPTER_BROKER_TYPE_MISMATCH"
        if adapter.venue != command.venue:
            return "PHASE33_EXECUTION_ADAPTER_VENUE_MISMATCH"
        try:
            capabilities = adapter.execution_capabilities()
        except Exception:
            return "PHASE33_EXECUTION_CAPABILITIES_UNAVAILABLE"
        if capabilities.broker_type is not adapter.broker_type or capabilities.venue != command.venue:
            return "PHASE33_EXECUTION_CAPABILITY_IDENTITY_MISMATCH"
        if not capabilities.execution_enabled:
            return "BROKER_EXECUTION_ADAPTER_NOT_EXPLICITLY_ENABLED"
        if not capabilities.supports_market_orders:
            return "BROKER_EXECUTION_ADAPTER_DOES_NOT_SUPPORT_MARKET_ORDERS"
        if execution_policy.require_stop_protection and not capabilities.supports_server_side_stop:
            return "BROKER_EXECUTION_ADAPTER_CANNOT_GUARANTEE_STOP_PROTECTION"
        if execution_policy.require_target_protection and not capabilities.supports_server_side_target:
            return "BROKER_EXECUTION_ADAPTER_CANNOT_GUARANTEE_TARGET_PROTECTION"
        if not capabilities.supports_reconciliation:
            return "BROKER_EXECUTION_ADAPTER_MUST_SUPPORT_RECONCILIATION"
        return None

    @staticmethod
    def _safe_submit(
        adapter: BrokerExecutionAdapter,
        *,
        command: ExecutionCommand,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        try:
            return adapter.submit_market(command.to_dict(), now_ms=now_ms)
        except Exception as exc:
            return BrokerExecutionReceipt(
                command_id=command.command_id,
                account_alias=command.account_alias,
                venue=command.venue,
                broker_type=BrokerType(command.broker_type),
                client_order_label=command.client_order_label,
                outcome=BrokerExecutionOutcome.AMBIGUOUS,
                provider_code="ADAPTER_SUBMIT_EXCEPTION",
                provider_message=type(exc).__name__,
                submitted_at_ms=now_ms,
                definite_no_fill=False,
            )

    @staticmethod
    def _safe_reconcile(
        adapter: BrokerExecutionAdapter,
        *,
        command: ExecutionCommand,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        try:
            return adapter.reconcile(command.to_dict(), now_ms=now_ms)
        except Exception as exc:
            return BrokerExecutionReceipt(
                command_id=command.command_id,
                account_alias=command.account_alias,
                venue=command.venue,
                broker_type=BrokerType(command.broker_type),
                client_order_label=command.client_order_label,
                outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
                provider_code="ADAPTER_RECONCILIATION_EXCEPTION",
                provider_message=type(exc).__name__,
                submitted_at_ms=now_ms,
                definite_no_fill=False,
            )

    def _finalize_receipt(
        self,
        *,
        command: ExecutionCommand,
        receipt: BrokerExecutionReceipt,
        ledger: Phase33ExecutionLedger,
        now_ms: int,
        execution_policy: Phase33ExecutionPolicy,
        reconciliation: bool,
    ) -> Phase33AccountExecution:
        if not self._receipt_matches_command(receipt, command):
            invalid = replace(
                receipt,
                outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
                provider_code="RECEIPT_COMMAND_IDENTITY_MISMATCH",
                definite_no_fill=False,
            )
            transition = ledger.record_receipt(
                command_id=command.command_id,
                new_state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                receipt=invalid,
                now_ms=now_ms,
            )
            return self._uncertain(
                command=command,
                receipt=invalid,
                status=Phase33AccountStatus.RECONCILIATION_REQUIRED,
                state=transition.state,
                reason=transition.reason,
            )

        if receipt.outcome is BrokerExecutionOutcome.ACKNOWLEDGED:
            protection_ok = bool(
                (not execution_policy.require_stop_protection or receipt.stop_protection_active)
                and (not execution_policy.require_target_protection or receipt.target_protection_active)
            )
            exact_fill = bool(
                receipt.filled_volume is not None
                and abs(float(receipt.filled_volume) - command.exact_volume) <= 1e-12
            )
            if not protection_ok or not exact_fill:
                receipt = replace(
                    receipt,
                    outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
                    provider_code="ACK_RECEIPT_NOT_EXACTLY_FILLED_AND_PROTECTED",
                    definite_no_fill=False,
                )
                transition = ledger.record_receipt(
                    command_id=command.command_id,
                    new_state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                    receipt=receipt,
                    now_ms=now_ms,
                )
                return self._uncertain(
                    command=command,
                    receipt=receipt,
                    status=Phase33AccountStatus.RECONCILIATION_REQUIRED,
                    state=transition.state,
                    reason=transition.reason,
                )
            transition = ledger.record_receipt(
                command_id=command.command_id,
                new_state=Phase32LedgerState.ACKNOWLEDGED,
                receipt=receipt,
                now_ms=now_ms,
            )
            status = (
                Phase33AccountStatus.RECOVERED_ACKNOWLEDGED
                if reconciliation
                else Phase33AccountStatus.ACKNOWLEDGED
            )
            return self._success(
                command=command,
                receipt=receipt,
                status=status,
                state=transition.state,
                reason=transition.reason,
            )

        if receipt.outcome is BrokerExecutionOutcome.REJECTED and receipt.definite_no_fill:
            transition = ledger.record_receipt(
                command_id=command.command_id,
                new_state=Phase32LedgerState.FAILED_SAFE,
                receipt=receipt,
                now_ms=now_ms,
            )
            return self._blocked(
                alias=command.account_alias,
                venue=command.venue,
                broker_type=command.broker_type,
                status=Phase33AccountStatus.FAILED_SAFE,
                reason=transition.reason,
                command=command,
                ledger_state=transition.state,
                receipt=receipt,
                locked=True,
            )

        if receipt.outcome is BrokerExecutionOutcome.AMBIGUOUS and not reconciliation:
            transition = ledger.record_receipt(
                command_id=command.command_id,
                new_state=Phase32LedgerState.AMBIGUOUS,
                receipt=receipt,
                now_ms=now_ms,
            )
            return self._uncertain(
                command=command,
                receipt=receipt,
                status=Phase33AccountStatus.AMBIGUOUS,
                state=transition.state,
                reason=transition.reason,
            )

        receipt = replace(
            receipt,
            outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
            definite_no_fill=False,
        )
        transition = ledger.record_receipt(
            command_id=command.command_id,
            new_state=Phase32LedgerState.RECONCILIATION_REQUIRED,
            receipt=receipt,
            now_ms=now_ms,
        )
        return self._uncertain(
            command=command,
            receipt=receipt,
            status=Phase33AccountStatus.RECONCILIATION_REQUIRED,
            state=transition.state,
            reason=transition.reason,
        )

    @staticmethod
    def _receipt_matches_command(
        receipt: BrokerExecutionReceipt,
        command: ExecutionCommand,
    ) -> bool:
        return bool(
            receipt.command_id == command.command_id
            and receipt.account_alias == command.account_alias
            and receipt.venue == command.venue
            and receipt.broker_type.value == command.broker_type
            and receipt.client_order_label == command.client_order_label
        )

    @staticmethod
    def _receipt_from_stored(stored: dict[str, Any] | None) -> BrokerExecutionReceipt | None:
        if not stored or stored.get("integrity_valid") is not True:
            return None
        raw = stored.get("receipt")
        if not isinstance(raw, dict):
            return None
        payload = dict(raw)
        try:
            payload["broker_type"] = BrokerType(str(payload["broker_type"]))
            payload["outcome"] = BrokerExecutionOutcome(str(payload["outcome"]))
            return BrokerExecutionReceipt(**payload)
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _success(
        *,
        command: ExecutionCommand,
        receipt: BrokerExecutionReceipt,
        status: Phase33AccountStatus,
        state: Phase32LedgerState,
        reason: str,
    ) -> Phase33AccountExecution:
        return Phase33AccountExecution(
            account_alias=command.account_alias,
            venue=command.venue,
            broker_type=command.broker_type,
            status=status,
            command_id=command.command_id,
            client_order_label=command.client_order_label,
            ledger_state=state,
            receipt=receipt,
            authorization_reserved=True,
            authorization_consumed=True,
            authorization_locked_against_retry=True,
            broker_order_placed=True,
            reason_codes=(reason,),
        )

    @staticmethod
    def _uncertain(
        *,
        command: ExecutionCommand,
        receipt: BrokerExecutionReceipt,
        status: Phase33AccountStatus,
        state: Phase32LedgerState,
        reason: str,
    ) -> Phase33AccountExecution:
        return Phase33AccountExecution(
            account_alias=command.account_alias,
            venue=command.venue,
            broker_type=command.broker_type,
            status=status,
            command_id=command.command_id,
            client_order_label=command.client_order_label,
            ledger_state=state,
            receipt=receipt,
            authorization_reserved=True,
            authorization_consumed=False,
            authorization_locked_against_retry=True,
            broker_order_placed=False,
            reason_codes=(reason, "AUTOMATIC_RESUBMISSION_FORBIDDEN_PENDING_RECONCILIATION"),
        )

    @staticmethod
    def _blocked(
        *,
        alias: str,
        venue: str | None,
        broker_type: str | None,
        status: Phase33AccountStatus,
        reason: str,
        extra_reasons: tuple[str, ...] = (),
        command: ExecutionCommand | None = None,
        ledger_state: Phase32LedgerState | None = None,
        receipt: BrokerExecutionReceipt | None = None,
        locked: bool = False,
    ) -> Phase33AccountExecution:
        return Phase33AccountExecution(
            account_alias=alias,
            venue=venue,
            broker_type=broker_type,
            status=status,
            command_id=command.command_id if command else None,
            client_order_label=command.client_order_label if command else None,
            ledger_state=ledger_state,
            receipt=receipt,
            authorization_reserved=command is not None,
            authorization_consumed=False,
            authorization_locked_against_retry=locked,
            broker_order_placed=False,
            reason_codes=(reason, *extra_reasons),
        )

    @staticmethod
    def _batch(
        *,
        phase32_plan: dict[str, Any],
        policy: OrchestrationPolicy,
        results: tuple[Phase33AccountExecution, ...],
        execution_policy: Phase33ExecutionPolicy,
        global_reasons: tuple[str, ...],
    ) -> Phase33ExecutionBatch:
        enabled = sum(item.status is not Phase33AccountStatus.SKIPPED_DISABLED for item in results)
        skipped = len(results) - enabled
        acknowledged = sum(
            item.status
            in {
                Phase33AccountStatus.ACKNOWLEDGED,
                Phase33AccountStatus.IDEMPOTENT_ACKNOWLEDGED,
                Phase33AccountStatus.RECOVERED_ACKNOWLEDGED,
            }
            for item in results
        )
        failed_safe = sum(item.status is Phase33AccountStatus.FAILED_SAFE for item in results)
        uncertain = sum(
            item.status
            in {Phase33AccountStatus.AMBIGUOUS, Phase33AccountStatus.RECONCILIATION_REQUIRED}
            for item in results
        )
        blocked = enabled - acknowledged - failed_safe - uncertain
        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            reasons = global_reasons or ("NO_ENABLED_PHASE33_ACCOUNT",)
        elif acknowledged == enabled:
            status = MultiAccountBatchStatus.READY
            reasons = ("EVERY_ENABLED_PHASE33_COMMAND_ACKNOWLEDGED_AND_PROTECTED",)
        elif acknowledged > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            reasons = ("SOME_PHASE33_COMMANDS_ACKNOWLEDGED_OTHERS_NOT",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            reasons = global_reasons or ("NO_PHASE33_COMMAND_ACKNOWLEDGED",)
        return Phase33ExecutionBatch(
            trade_id=str(phase32_plan.get("trade_id") or ""),
            canonical_symbol=str(phase32_plan.get("canonical_symbol") or ""),
            direction=str(phase32_plan.get("direction") or ""),
            policy=policy,
            status=status,
            accounts=results,
            enabled_accounts=enabled,
            acknowledged_accounts=acknowledged,
            failed_safe_accounts=failed_safe,
            uncertain_accounts=uncertain,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            execution_enabled=execution_policy.execution_enabled,
            reason_codes=reasons,
        )


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
