"""Phase 32: durable exactly-once shadow execution-command reservation.

Phase 32 consumes only Phase 31 accounts that passed the universal immediate
pre-submit firewall. It builds one immutable deterministic command per account
and reserves the upstream authorization in a durable SQLite ledger.

This phase deliberately does not submit, amend, cancel, close or flatten broker
orders. A reservation prevents replay of one Phase 30 authorization through a
different command while allowing an identical retry to remain idempotent.
Authorization consumption belongs to the later broker-submission transition.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from tradingagents.brokers.contracts import (
    BrokerType,
    OrchestrationPolicy,
    TradeIntent,
    canonicalize_symbol,
)

from .multi_account import MultiAccountBatchStatus
from .phase30 import Phase30BrokerVenue
from .risk_sizing import ALLOWED_RISK_FRACTIONS


class Phase32LedgerState(str, Enum):
    SHADOW_READY = "SHADOW_READY"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED_SAFE = "FAILED_SAFE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class Phase32AccountStatus(str, Enum):
    SHADOW_READY = "SHADOW_READY"
    IDEMPOTENT_SHADOW_READY = "IDEMPOTENT_SHADOW_READY"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_PHASE31 = "BLOCKED_PHASE31"
    BLOCKED_COMMAND_INTEGRITY = "BLOCKED_COMMAND_INTEGRITY"
    BLOCKED_LEDGER_CONFLICT = "BLOCKED_LEDGER_CONFLICT"
    BLOCKED_BATCH_POLICY = "BLOCKED_BATCH_POLICY"


class LedgerReservationKind(str, Enum):
    RESERVED_NEW = "RESERVED_NEW"
    IDEMPOTENT_EXISTING = "IDEMPOTENT_EXISTING"
    CONFLICT = "CONFLICT"
    BATCH_ABORTED = "BATCH_ABORTED"


@dataclass(frozen=True)
class ExecutionCommand:
    schema_version: str
    command_mode: str
    trade_id: str
    account_alias: str
    venue: str
    broker_type: str
    broker_symbol: str
    canonical_symbol: str
    direction: str
    execution_style: str
    exact_volume: float
    volume_unit: str
    selected_risk_fraction: float
    intended_entry_price: float
    current_executable_price: float
    stop_price: float
    target_price: float
    selected_exit_mode: str
    phase30_authorization_fingerprint: str
    phase31_pre_submit_fingerprint: str
    quote_timestamp_ms: int | None
    command_id: str
    client_order_label: str

    @classmethod
    def build(
        cls,
        *,
        intent: TradeIntent,
        account: dict[str, Any],
    ) -> ExecutionCommand:
        body = {
            "schema_version": "LONDRES_EXECUTION_COMMAND_V1",
            "command_mode": "SHADOW_ONLY",
            "trade_id": intent.trade_id,
            "account_alias": str(account["account_alias"]),
            "venue": str(account["venue"]),
            "broker_type": str(account["broker_type"]),
            "broker_symbol": str(account["broker_symbol"]),
            "canonical_symbol": intent.canonical,
            "direction": intent.direction,
            "execution_style": intent.execution_style,
            "exact_volume": float(account["prepared_volume"]),
            "volume_unit": str(account["volume_unit"]).strip().lower(),
            "selected_risk_fraction": float(account["selected_risk_fraction"]),
            "intended_entry_price": float(intent.entry_price),
            "current_executable_price": float(account["current_executable_price"]),
            "stop_price": float(intent.stop_price),
            "target_price": float(intent.target_price),
            "selected_exit_mode": intent.selected_exit_mode,
            "phase30_authorization_fingerprint": str(
                account["phase30_authorization_fingerprint"]
            ).lower(),
            "phase31_pre_submit_fingerprint": str(
                account["pre_submit_snapshot_fingerprint"]
            ).lower(),
            "quote_timestamp_ms": account.get("quote_timestamp_ms"),
        }
        command_id = _sha256_json(body)
        return cls(
            **body,
            command_id=command_id,
            client_order_label=f"L32-{command_id[:20]}",
        )

    def identity_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("command_id")
        payload.pop("client_order_label")
        return payload

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def payload_sha256(self) -> str:
        return _sha256_json(self.to_dict())

    def integrity_valid(self) -> bool:
        return bool(
            self.command_id == _sha256_json(self.identity_dict())
            and self.client_order_label == f"L32-{self.command_id[:20]}"
            and _valid_sha256(self.command_id)
            and _valid_sha256(self.phase30_authorization_fingerprint)
            and _valid_sha256(self.phase31_pre_submit_fingerprint)
        )


@dataclass(frozen=True)
class LedgerReservation:
    command_id: str
    kind: LedgerReservationKind
    ledger_state: Phase32LedgerState | None
    ledger_mutated: bool
    reason: str


@dataclass(frozen=True)
class Phase32AccountCommand:
    account_alias: str
    venue: str | None
    broker_type: str | None
    status: Phase32AccountStatus
    command: ExecutionCommand | None
    phase30_authorization_fingerprint: str | None
    phase31_pre_submit_fingerprint: str | None
    ledger_state: Phase32LedgerState | None
    authorization_reserved: bool
    shadow_ready: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_alias": self.account_alias,
            "venue": self.venue,
            "broker_type": self.broker_type,
            "status": self.status.value,
            "command": self.command.to_dict() if self.command is not None else None,
            "phase30_authorization_fingerprint": self.phase30_authorization_fingerprint,
            "phase31_pre_submit_fingerprint": self.phase31_pre_submit_fingerprint,
            "ledger_state": self.ledger_state.value if self.ledger_state else None,
            "authorization_reserved": self.authorization_reserved,
            "authorization_consumed": False,
            "shadow_ready": self.shadow_ready,
            "execution_handoff_ready": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "authorized_volume_resized": False,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class Phase32CommandBatch:
    trade_id: str
    canonical_symbol: str
    direction: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase32AccountCommand, ...]
    enabled_accounts: int
    shadow_ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    ledger_mutated: bool
    shadow_ready: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE32_EXACTLY_ONCE_SHADOW_EXECUTION_COMMANDS",
            "trade_id": self.trade_id,
            "canonical_symbol": self.canonical_symbol,
            "direction": self.direction,
            "policy": self.policy.value,
            "status": self.status.value,
            "accounts": [account.to_dict() for account in self.accounts],
            "enabled_accounts": self.enabled_accounts,
            "shadow_ready_accounts": self.shadow_ready_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "ledger_backend": "SQLITE_DURABLE",
            "ledger_mutated": self.ledger_mutated,
            "shadow_ready": self.shadow_ready,
            "authorization_consumed": False,
            "execution_handoff_ready": False,
            "order_authorized": False,
            "account_scope": "BROKERAGE_ACCOUNTS",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class SQLiteExecutionAuthorizationLedger:
    """Durable command reservation ledger using SQLite uniqueness + transactions."""

    def __init__(self, path: str | Path) -> None:
        raw = str(path).strip()
        if not raw or raw == ":memory:":
            raise ValueError("Phase 32 requires a durable file-backed SQLite path")
        self.path = Path(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS execution_commands (
                    command_id TEXT PRIMARY KEY,
                    phase30_fingerprint TEXT NOT NULL UNIQUE,
                    phase31_fingerprint TEXT NOT NULL UNIQUE,
                    account_alias TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    broker_type TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_phase32_account_alias "
                "ON execution_commands(account_alias)"
            )

    def reserve_shadow(
        self,
        *,
        commands: tuple[ExecutionCommand, ...],
        policy: OrchestrationPolicy,
        now_ms: int,
    ) -> tuple[LedgerReservation, ...]:
        if policy is OrchestrationPolicy.ALL_OR_NONE:
            return self._reserve_all_or_none(commands=commands, now_ms=now_ms)
        return tuple(self._reserve_one(command=command, now_ms=now_ms) for command in commands)

    def _reserve_one(self, *, command: ExecutionCommand, now_ms: int) -> LedgerReservation:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._classify(connection, command)
            if reservation is not None:
                connection.commit()
                return reservation
            self._insert(connection, command=command, now_ms=now_ms)
            connection.commit()
            return LedgerReservation(
                command_id=command.command_id,
                kind=LedgerReservationKind.RESERVED_NEW,
                ledger_state=Phase32LedgerState.SHADOW_READY,
                ledger_mutated=True,
                reason="NEW_COMMAND_ATOMICALLY_RESERVED_IN_DURABLE_LEDGER",
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            return LedgerReservation(
                command_id=command.command_id,
                kind=LedgerReservationKind.CONFLICT,
                ledger_state=None,
                ledger_mutated=False,
                reason="SQLITE_UNIQUENESS_RACE_BLOCKED_COMMAND_RESERVATION",
            )
        finally:
            connection.close()

    def _reserve_all_or_none(
        self,
        *,
        commands: tuple[ExecutionCommand, ...],
        now_ms: int,
    ) -> tuple[LedgerReservation, ...]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            classified = [self._classify(connection, command) for command in commands]
            if any(
                item is not None and item.kind is LedgerReservationKind.CONFLICT
                for item in classified
            ):
                connection.rollback()
                return tuple(
                    item
                    if item is not None and item.kind is LedgerReservationKind.CONFLICT
                    else LedgerReservation(
                        command_id=command.command_id,
                        kind=LedgerReservationKind.BATCH_ABORTED,
                        ledger_state=(item.ledger_state if item is not None else None),
                        ledger_mutated=False,
                        reason="ALL_OR_NONE_LEDGER_CONFLICT_ABORTED_BATCH_RESERVATION",
                    )
                    for command, item in zip(commands, classified, strict=True)
                )

            reservations: list[LedgerReservation] = []
            for command, item in zip(commands, classified, strict=True):
                if item is not None:
                    reservations.append(item)
                    continue
                self._insert(connection, command=command, now_ms=now_ms)
                reservations.append(
                    LedgerReservation(
                        command_id=command.command_id,
                        kind=LedgerReservationKind.RESERVED_NEW,
                        ledger_state=Phase32LedgerState.SHADOW_READY,
                        ledger_mutated=True,
                        reason="NEW_COMMAND_ATOMICALLY_RESERVED_IN_DURABLE_LEDGER",
                    )
                )
            connection.commit()
            return tuple(reservations)
        except sqlite3.IntegrityError:
            connection.rollback()
            return tuple(
                LedgerReservation(
                    command_id=command.command_id,
                    kind=LedgerReservationKind.CONFLICT,
                    ledger_state=None,
                    ledger_mutated=False,
                    reason="ALL_OR_NONE_SQLITE_UNIQUENESS_RACE_ROLLED_BACK_BATCH",
                )
                for command in commands
            )
        finally:
            connection.close()

    def _classify(
        self,
        connection: sqlite3.Connection,
        command: ExecutionCommand,
    ) -> LedgerReservation | None:
        rows = connection.execute(
            """
            SELECT command_id, phase30_fingerprint, phase31_fingerprint,
                   payload_sha256, command_json, state
            FROM execution_commands
            WHERE command_id = ? OR phase30_fingerprint = ? OR phase31_fingerprint = ?
            """,
            (
                command.command_id,
                command.phase30_authorization_fingerprint,
                command.phase31_pre_submit_fingerprint,
            ),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            return LedgerReservation(
                command_id=command.command_id,
                kind=LedgerReservationKind.CONFLICT,
                ledger_state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                ledger_mutated=False,
                reason="MULTIPLE_LEDGER_ROWS_MATCH_ONE_COMMAND_AUTHORIZATION",
            )

        row = rows[0]
        stored_json = str(row["command_json"])
        stored_payload_hash = str(row["payload_sha256"])
        if _sha256_text(stored_json) != stored_payload_hash:
            return LedgerReservation(
                command_id=command.command_id,
                kind=LedgerReservationKind.CONFLICT,
                ledger_state=Phase32LedgerState.RECONCILIATION_REQUIRED,
                ledger_mutated=False,
                reason="LEDGER_PAYLOAD_HASH_MISMATCH_RECONCILIATION_REQUIRED",
            )

        state = _ledger_state(row["state"])
        exact = bool(
            row["command_id"] == command.command_id
            and row["phase30_fingerprint"] == command.phase30_authorization_fingerprint
            and row["phase31_fingerprint"] == command.phase31_pre_submit_fingerprint
            and stored_payload_hash == command.payload_sha256
            and stored_json == _canonical_json(command.to_dict())
        )
        if exact and state is Phase32LedgerState.SHADOW_READY:
            return LedgerReservation(
                command_id=command.command_id,
                kind=LedgerReservationKind.IDEMPOTENT_EXISTING,
                ledger_state=state,
                ledger_mutated=False,
                reason="IDENTICAL_COMMAND_ALREADY_RESERVED_IDEMPOTENT_RETRY",
            )
        if exact:
            return LedgerReservation(
                command_id=command.command_id,
                kind=LedgerReservationKind.CONFLICT,
                ledger_state=state,
                ledger_mutated=False,
                reason="EXISTING_COMMAND_ALREADY_ADVANCED_BEYOND_PHASE32_SHADOW_STATE",
            )
        return LedgerReservation(
            command_id=command.command_id,
            kind=LedgerReservationKind.CONFLICT,
            ledger_state=state,
            ledger_mutated=False,
            reason="AUTHORIZATION_OR_SNAPSHOT_RESERVED_BY_DIFFERENT_COMMAND",
        )

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        *,
        command: ExecutionCommand,
        now_ms: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO execution_commands (
                command_id, phase30_fingerprint, phase31_fingerprint,
                account_alias, venue, broker_type, payload_sha256,
                command_json, state, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command.command_id,
                command.phase30_authorization_fingerprint,
                command.phase31_pre_submit_fingerprint,
                command.account_alias,
                command.venue,
                command.broker_type,
                command.payload_sha256,
                _canonical_json(command.to_dict()),
                Phase32LedgerState.SHADOW_READY.value,
                now_ms,
                now_ms,
            ),
        )

    def inspect(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            return None
        command_json = str(row["command_json"])
        integrity_valid = _sha256_text(command_json) == str(row["payload_sha256"])
        reason = "STORED_PAYLOAD_HASH_VERIFIED" if integrity_valid else (
            "LEDGER_PAYLOAD_HASH_MISMATCH_RECONCILIATION_REQUIRED"
        )
        payload: dict[str, Any] | None = None
        if integrity_valid:
            try:
                parsed = json.loads(command_json)
            except json.JSONDecodeError:
                integrity_valid = False
                reason = "LEDGER_COMMAND_JSON_INVALID_RECONCILIATION_REQUIRED"
            else:
                payload = parsed
                identity = dict(parsed)
                stored_id = str(identity.pop("command_id", ""))
                label = str(identity.pop("client_order_label", ""))
                if (
                    stored_id != command_id
                    or _sha256_json(identity) != command_id
                    or label != f"L32-{command_id[:20]}"
                ):
                    integrity_valid = False
                    reason = "LEDGER_COMMAND_IDENTITY_MISMATCH_RECONCILIATION_REQUIRED"
        return {
            "command_id": command_id,
            "state": str(row["state"]),
            "integrity_valid": integrity_valid,
            "reason": reason,
            "command": payload if integrity_valid else None,
            "account_environment": "HIDDEN_INTERNAL",
        }

    def reserved_phase30_fingerprints(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT phase30_fingerprint FROM execution_commands"
            ).fetchall()
        return {str(row["phase30_fingerprint"]) for row in rows}

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM execution_commands").fetchone()
        return int(row["count"])


class LondresPhase32ExactlyOnceShadowCommandEngine:
    """Build and durably reserve Phase 31 commands without broker submission."""

    def prepare(
        self,
        *,
        intent: TradeIntent,
        phase31_plan: dict[str, Any],
        ledger: SQLiteExecutionAuthorizationLedger,
        now_ms: int,
    ) -> dict[str, Any]:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")

        policy, global_reasons = self._phase31_batch_contract(
            intent=intent,
            plan=phase31_plan,
        )
        effective_policy = policy or OrchestrationPolicy.BEST_EFFORT
        accounts = phase31_plan.get("accounts") if isinstance(phase31_plan.get("accounts"), list) else []

        candidates = tuple(
            self._candidate(intent=intent, account=account, global_reasons=global_reasons)
            for account in accounts
        )
        candidates, uniqueness_reasons = self._enforce_candidate_uniqueness(candidates)
        global_reasons = (*global_reasons, *uniqueness_reasons)

        if global_reasons:
            candidates = tuple(
                self._block_existing(
                    item,
                    status=Phase32AccountStatus.BLOCKED_PHASE31,
                    reason="PHASE31_BATCH_CONTRACT_NOT_VALID_FOR_PHASE32",
                )
                if item.status not in {Phase32AccountStatus.SKIPPED_DISABLED}
                else item
                for item in candidates
            )
            return self._batch(
                intent=intent,
                policy=effective_policy,
                accounts=candidates,
                ledger_mutated=False,
                global_reasons=global_reasons,
            ).to_dict()

        enabled = [
            item for item in candidates if item.status is not Phase32AccountStatus.SKIPPED_DISABLED
        ]
        invalid = [
            item
            for item in enabled
            if item.status is not Phase32AccountStatus.SHADOW_READY or item.command is None
        ]
        if effective_policy is OrchestrationPolicy.ALL_OR_NONE and invalid:
            blocked = tuple(
                self._block_existing(
                    item,
                    status=Phase32AccountStatus.BLOCKED_BATCH_POLICY,
                    reason="ALL_OR_NONE_PHASE32_VALIDATION_BLOCKED_LEDGER_RESERVATION",
                )
                if item.status is Phase32AccountStatus.SHADOW_READY
                else item
                for item in candidates
            )
            return self._batch(
                intent=intent,
                policy=effective_policy,
                accounts=blocked,
                ledger_mutated=False,
                global_reasons=("ALL_OR_NONE_REQUIRES_EVERY_ENABLED_PHASE32_COMMAND_VALID",),
            ).to_dict()

        reservable = tuple(
            item.command
            for item in candidates
            if item.status is Phase32AccountStatus.SHADOW_READY and item.command is not None
        )
        reservations = ledger.reserve_shadow(
            commands=reservable,
            policy=effective_policy,
            now_ms=now_ms,
        )
        by_command = {item.command_id: item for item in reservations}
        final = tuple(self._apply_reservation(item, by_command) for item in candidates)
        ledger_mutated = any(item.ledger_mutated for item in reservations)
        return self._batch(
            intent=intent,
            policy=effective_policy,
            accounts=final,
            ledger_mutated=ledger_mutated,
            global_reasons=(),
        ).to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"exactly_once_shadow_execution_command_state": context}

    @staticmethod
    def _phase31_batch_contract(
        *,
        intent: TradeIntent,
        plan: dict[str, Any],
    ) -> tuple[OrchestrationPolicy | None, tuple[str, ...]]:
        reasons: list[str] = []
        try:
            policy = OrchestrationPolicy(str(plan.get("policy")))
        except ValueError:
            policy = None
            reasons.append("VALID_PHASE31_ORCHESTRATION_POLICY_REQUIRED")
        if not str(plan.get("phase") or "").startswith("LONDRES_PHASE31"):
            reasons.append("PHASE31_PLAN_PHASE_MARKER_REQUIRED")
        if plan.get("trade_id") != intent.trade_id:
            reasons.append("PHASE31_TRADE_ID_DOES_NOT_MATCH_INTENT")
        if canonicalize_symbol(str(plan.get("canonical_symbol") or "")) != intent.canonical:
            reasons.append("PHASE31_CANONICAL_SYMBOL_DOES_NOT_MATCH_INTENT")
        if str(plan.get("direction") or "") != intent.direction:
            reasons.append("PHASE31_DIRECTION_DOES_NOT_MATCH_INTENT")
        if intent.execution_style != "MARKET_ON_SIGNAL":
            reasons.append("PHASE32_CURRENTLY_REQUIRES_MARKET_ON_SIGNAL_EXECUTION_STYLE")
        if plan.get("pre_submit_ready") is not True:
            reasons.append("PHASE31_PRE_SUBMIT_BATCH_NOT_READY")
        if plan.get("order_authorized") is not True:
            reasons.append("PHASE31_ORDER_AUTHORIZATION_REQUIRED")
        if plan.get("order_submission_enabled") is not False:
            reasons.append("PHASE31_MUST_NOT_ENABLE_ORDER_SUBMISSION")
        if not isinstance(plan.get("accounts"), list):
            reasons.append("PHASE31_ACCOUNT_LIST_REQUIRED")
        return policy, tuple(reasons)

    def _candidate(
        self,
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        global_reasons: tuple[str, ...],
    ) -> Phase32AccountCommand:
        alias = str(account.get("account_alias") or "")
        venue = str(account.get("venue") or "") or None
        broker_type = str(account.get("broker_type") or "") or None
        phase30 = str(account.get("phase30_authorization_fingerprint") or "").lower() or None
        phase31 = str(account.get("pre_submit_snapshot_fingerprint") or "").lower() or None
        if account.get("status") == "SKIPPED_DISABLED":
            return Phase32AccountCommand(
                account_alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase32AccountStatus.SKIPPED_DISABLED,
                command=None,
                phase30_authorization_fingerprint=phase30,
                phase31_pre_submit_fingerprint=phase31,
                ledger_state=None,
                authorization_reserved=False,
                shadow_ready=False,
                reason_codes=("ACCOUNT_DISABLED_BEFORE_PHASE32",),
            )

        reasons = list(global_reasons)
        if account.get("status") != "READY_FOR_EXECUTION_ADAPTER_HANDOFF":
            reasons.append("PHASE31_ACCOUNT_READY_STATUS_REQUIRED")
        if account.get("pre_submit_ready") is not True:
            reasons.append("PHASE31_ACCOUNT_PRE_SUBMIT_READY_REQUIRED")
        if account.get("order_authorized") is not True:
            reasons.append("PHASE31_ACCOUNT_ORDER_AUTHORIZATION_REQUIRED")
        if account.get("trade_id") != intent.trade_id:
            reasons.append("PHASE31_ACCOUNT_TRADE_ID_DOES_NOT_MATCH_INTENT")
        if canonicalize_symbol(str(account.get("canonical_symbol") or "")) != intent.canonical:
            reasons.append("PHASE31_ACCOUNT_CANONICAL_SYMBOL_DOES_NOT_MATCH_INTENT")
        if str(account.get("direction") or "") != intent.direction:
            reasons.append("PHASE31_ACCOUNT_DIRECTION_DOES_NOT_MATCH_INTENT")

        if not self._valid_venue_type(venue=venue, broker_type=broker_type):
            reasons.append("SUPPORTED_PHASE31_VENUE_AND_BROKER_TYPE_REQUIRED")
        broker_symbol = str(account.get("broker_symbol") or "").strip()
        if not broker_symbol:
            reasons.append("EXACT_PHASE31_BROKER_SYMBOL_REQUIRED")
        volume = _positive_number(account.get("prepared_volume"))
        if volume is None:
            reasons.append("POSITIVE_EXACT_PHASE31_VOLUME_REQUIRED")
        unit = str(account.get("volume_unit") or "").strip().lower()
        if unit not in {"contracts", "units", "lots"}:
            reasons.append("PHASE31_VOLUME_UNIT_MUST_BE_CONTRACTS_UNITS_OR_LOTS")
        risk_fraction = _risk_fraction(account.get("selected_risk_fraction"))
        if risk_fraction is None:
            reasons.append("PHASE31_RISK_FRACTION_MUST_BE_EXACTLY_3_5_OR_10_PERCENT")
        executable = _positive_number(account.get("current_executable_price"))
        if executable is None:
            reasons.append("POSITIVE_PHASE31_CURRENT_EXECUTABLE_PRICE_REQUIRED")
        if not _valid_sha256(phase30 or ""):
            reasons.append("VALID_PHASE30_AUTHORIZATION_FINGERPRINT_REQUIRED")
        if not _valid_sha256(phase31 or ""):
            reasons.append("VALID_PHASE31_PRE_SUBMIT_FINGERPRINT_REQUIRED")
        quote_timestamp = account.get("quote_timestamp_ms")
        if quote_timestamp is not None and (
            isinstance(quote_timestamp, bool)
            or not isinstance(quote_timestamp, int)
            or quote_timestamp < 0
        ):
            reasons.append("PHASE31_QUOTE_TIMESTAMP_MUST_BE_NON_NEGATIVE_INTEGER_OR_NONE")

        if executable is not None:
            if intent.direction == "BULLISH" and not (
                intent.stop_price < executable < intent.target_price
            ):
                reasons.append("PHASE31_EXECUTABLE_PRICE_OUTSIDE_BULLISH_TRADE_GEOMETRY")
            if intent.direction == "BEARISH" and not (
                intent.target_price < executable < intent.stop_price
            ):
                reasons.append("PHASE31_EXECUTABLE_PRICE_OUTSIDE_BEARISH_TRADE_GEOMETRY")

        if reasons:
            return Phase32AccountCommand(
                account_alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase32AccountStatus.BLOCKED_PHASE31,
                command=None,
                phase30_authorization_fingerprint=phase30,
                phase31_pre_submit_fingerprint=phase31,
                ledger_state=None,
                authorization_reserved=False,
                shadow_ready=False,
                reason_codes=tuple(reasons),
            )

        normalized = dict(account)
        normalized["prepared_volume"] = volume
        normalized["selected_risk_fraction"] = risk_fraction
        normalized["current_executable_price"] = executable
        command = ExecutionCommand.build(intent=intent, account=normalized)
        if not command.integrity_valid():
            return Phase32AccountCommand(
                account_alias=alias,
                venue=venue,
                broker_type=broker_type,
                status=Phase32AccountStatus.BLOCKED_COMMAND_INTEGRITY,
                command=None,
                phase30_authorization_fingerprint=phase30,
                phase31_pre_submit_fingerprint=phase31,
                ledger_state=None,
                authorization_reserved=False,
                shadow_ready=False,
                reason_codes=("DETERMINISTIC_EXECUTION_COMMAND_INTEGRITY_CHECK_FAILED",),
            )
        return Phase32AccountCommand(
            account_alias=alias,
            venue=venue,
            broker_type=broker_type,
            status=Phase32AccountStatus.SHADOW_READY,
            command=command,
            phase30_authorization_fingerprint=phase30,
            phase31_pre_submit_fingerprint=phase31,
            ledger_state=None,
            authorization_reserved=False,
            shadow_ready=False,
            reason_codes=("PHASE31_ACCOUNT_VALIDATED_FOR_DURABLE_COMMAND_RESERVATION",),
        )

    @staticmethod
    def _valid_venue_type(*, venue: str | None, broker_type: str | None) -> bool:
        expected = {
            Phase30BrokerVenue.NINJATRADER.value: BrokerType.NINJATRADER.value,
            Phase30BrokerVenue.FP_MARKETS_CTRADER.value: BrokerType.CTRADER.value,
            Phase30BrokerVenue.VANTAGE_MT5.value: BrokerType.MT5.value,
        }
        return bool(venue in expected and expected[venue] == broker_type)

    @staticmethod
    def _enforce_candidate_uniqueness(
        accounts: tuple[Phase32AccountCommand, ...],
    ) -> tuple[tuple[Phase32AccountCommand, ...], tuple[str, ...]]:
        active = [
            item
            for item in accounts
            if item.status is not Phase32AccountStatus.SKIPPED_DISABLED
        ]
        aliases = [item.account_alias for item in active]
        phase30 = [
            item.phase30_authorization_fingerprint
            for item in active
            if item.phase30_authorization_fingerprint is not None
        ]
        phase31 = [
            item.phase31_pre_submit_fingerprint
            for item in active
            if item.phase31_pre_submit_fingerprint is not None
        ]
        reasons: list[str] = []
        if len(set(aliases)) != len(aliases):
            reasons.append("PHASE32_ACCOUNT_ALIASES_MUST_BE_UNIQUE")
        if len(set(phase30)) != len(phase30):
            reasons.append("PHASE30_AUTHORIZATION_FINGERPRINT_DUPLICATED_IN_PHASE32_BATCH")
        if len(set(phase31)) != len(phase31):
            reasons.append("PHASE31_PRE_SUBMIT_FINGERPRINT_DUPLICATED_IN_PHASE32_BATCH")
        return accounts, tuple(reasons)

    @staticmethod
    def _apply_reservation(
        account: Phase32AccountCommand,
        reservations: dict[str, LedgerReservation],
    ) -> Phase32AccountCommand:
        if account.command is None or account.status is not Phase32AccountStatus.SHADOW_READY:
            return account
        result = reservations.get(account.command.command_id)
        if result is None:
            return LondresPhase32ExactlyOnceShadowCommandEngine._block_existing(
                account,
                status=Phase32AccountStatus.BLOCKED_LEDGER_CONFLICT,
                reason="LEDGER_DID_NOT_RETURN_COMMAND_RESERVATION_RESULT",
            )
        if result.kind is LedgerReservationKind.RESERVED_NEW:
            return replace(
                account,
                status=Phase32AccountStatus.SHADOW_READY,
                ledger_state=Phase32LedgerState.SHADOW_READY,
                authorization_reserved=True,
                shadow_ready=True,
                reason_codes=(
                    "PHASE31_PRE_SUBMIT_SNAPSHOT_BOUND_TO_IMMUTABLE_COMMAND",
                    "PHASE30_AUTHORIZATION_ATOMICALLY_RESERVED",
                    "DURABLE_SHADOW_COMMAND_READY",
                    "AUTHORIZATION_NOT_CONSUMED_UNTIL_FUTURE_BROKER_SUBMISSION_TRANSITION",
                ),
            )
        if result.kind is LedgerReservationKind.IDEMPOTENT_EXISTING:
            return replace(
                account,
                status=Phase32AccountStatus.IDEMPOTENT_SHADOW_READY,
                ledger_state=Phase32LedgerState.SHADOW_READY,
                authorization_reserved=True,
                shadow_ready=True,
                reason_codes=(
                    "IDENTICAL_PHASE32_COMMAND_ALREADY_DURABLY_RESERVED",
                    "IDEMPOTENT_RETRY_DID_NOT_CREATE_DUPLICATE_COMMAND",
                    "AUTHORIZATION_NOT_CONSUMED_UNTIL_FUTURE_BROKER_SUBMISSION_TRANSITION",
                ),
            )
        if result.kind is LedgerReservationKind.BATCH_ABORTED:
            return LondresPhase32ExactlyOnceShadowCommandEngine._block_existing(
                account,
                status=Phase32AccountStatus.BLOCKED_BATCH_POLICY,
                reason=result.reason,
            )
        return replace(
            account,
            status=Phase32AccountStatus.BLOCKED_LEDGER_CONFLICT,
            ledger_state=result.ledger_state,
            authorization_reserved=False,
            shadow_ready=False,
            reason_codes=(result.reason,),
        )

    @staticmethod
    def _block_existing(
        account: Phase32AccountCommand,
        *,
        status: Phase32AccountStatus,
        reason: str,
    ) -> Phase32AccountCommand:
        return replace(
            account,
            status=status,
            ledger_state=None,
            authorization_reserved=False,
            shadow_ready=False,
            reason_codes=account.reason_codes + (reason,),
        )

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        policy: OrchestrationPolicy,
        accounts: tuple[Phase32AccountCommand, ...],
        ledger_mutated: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase32CommandBatch:
        skipped = sum(item.status is Phase32AccountStatus.SKIPPED_DISABLED for item in accounts)
        enabled = len(accounts) - skipped
        ready = sum(item.shadow_ready for item in accounts)
        blocked = enabled - ready
        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_SUPPORTED_ACCOUNT_FOR_PHASE32",)
        elif global_reasons:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = global_reasons
        elif policy is OrchestrationPolicy.ALL_OR_NONE and blocked:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("ALL_OR_NONE_PHASE32_BATCH_NOT_DURABLY_RESERVED",)
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = ("ALL_ENABLED_COMMANDS_DURABLY_RESERVED_IN_SHADOW_LEDGER",)
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = ("BEST_EFFORT_RESERVED_ONLY_NONCONFLICTING_PHASE32_COMMANDS",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("NO_PHASE32_COMMAND_DURABLY_RESERVED",)
        return Phase32CommandBatch(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            policy=policy,
            status=status,
            accounts=accounts,
            enabled_accounts=enabled,
            shadow_ready_accounts=ready,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            ledger_mutated=ledger_mutated,
            shadow_ready=batch_ready,
            reason_codes=(*reasons, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE32"),
        )


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(value))


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _ledger_state(value: Any) -> Phase32LedgerState | None:
    try:
        return Phase32LedgerState(str(value))
    except ValueError:
        return None


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _risk_fraction(value: Any) -> float | None:
    number = _positive_number(value)
    if number is None:
        return None
    if not any(math.isclose(number, allowed, rel_tol=0.0, abs_tol=1e-12) for allowed in ALLOWED_RISK_FRACTIONS):
        return None
    return number
