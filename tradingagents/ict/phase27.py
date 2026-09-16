"""Phase 27: deterministic live-account execution authorization envelopes.

Phase 27 is the bridge between the fully validated Londres strategy stack and
Phase 26 live-account preparation. It authorizes an account-specific handoff to
a future execution adapter only when the strategy gate and that account's
prepared NinjaTrader contract plan agree.

It never submits, modifies, cancels, or closes a broker order.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import OrchestrationPolicy, TradeIntent, canonicalize_symbol

from .multi_account import MultiAccountBatchStatus


class Phase27AccountStatus(str, Enum):
    AUTHORIZED_FOR_EXECUTION_HANDOFF = "AUTHORIZED_FOR_EXECUTION_HANDOFF"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_STRATEGY_GATE = "BLOCKED_STRATEGY_GATE"
    BLOCKED_INTENT_MISMATCH = "BLOCKED_INTENT_MISMATCH"
    BLOCKED_PHASE26 = "BLOCKED_PHASE26"
    BLOCKED_EXECUTION_ENVELOPE = "BLOCKED_EXECUTION_ENVELOPE"
    BLOCKED_BATCH_POLICY = "BLOCKED_BATCH_POLICY"


@dataclass(frozen=True)
class Phase27AccountAuthorization:
    account_alias: str
    status: Phase27AccountStatus
    trade_id: str
    canonical_symbol: str
    direction: str
    selected_root: str | None
    active_contract: str | None
    contract_quantity: int | None
    entry_price: float
    stop_price: float
    target_price: float
    selected_exit_mode: str
    execution_handoff_ready: bool
    order_authorized: bool
    authorization_fingerprint: str | None
    phase26_account_plan: dict[str, Any] | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["account_scope"] = "LIVE_BROKERAGE_ACCOUNTS_ONLY"
        payload["order_submission_enabled"] = False
        payload["broker_order_placed"] = False
        return payload


@dataclass(frozen=True)
class Phase27AuthorizationBatch:
    trade_id: str
    canonical_symbol: str
    direction: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase27AccountAuthorization, ...]
    enabled_accounts: int
    authorized_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    execution_handoff_ready: bool
    order_authorized: bool
    strategy_gate: dict[str, Any]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "canonical_symbol": self.canonical_symbol,
            "direction": self.direction,
            "policy": self.policy.value,
            "status": self.status.value,
            "accounts": [account.to_dict() for account in self.accounts],
            "enabled_accounts": self.enabled_accounts,
            "authorized_accounts": self.authorized_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "execution_handoff_ready": self.execution_handoff_ready,
            "order_authorized": self.order_authorized,
            "strategy_gate": dict(self.strategy_gate),
            "authorization_authority": "LONDRES_PHASE27_DETERMINISTIC_ACCOUNT_HANDOFF_GATE",
            "position_source": "PHASE26_ACCOUNT_SPECIFIC_CURRENT_EQUITY_RISK_PLAN",
            "account_scope": "LIVE_BROKERAGE_ACCOUNTS_ONLY",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase27ExecutionAuthorizationEngine:
    """Authorize deterministic per-account handoff without broker submission.

    The strategy context must come from the Phase 17 stack (or an equivalent
    deterministic payload containing the same gates). Phase 27 re-verifies the
    user's required execution sequence explicitly:

        SMT detected -> CSD confirmed after SMT -> post-CSD IOFC aligned
        -> hard pre-broker validation authorized

    Phase 17's position volume is intentionally ignored. Futures quantity comes
    only from each Phase 26 account plan, where the account was independently
    risk-sized from current broker-reported equity.
    """

    def authorize(
        self,
        *,
        intent: TradeIntent,
        strategy_context: dict[str, Any],
        phase26_plan: dict[str, Any],
    ) -> dict[str, Any]:
        policy, phase26_reasons = self._phase26_contract(intent=intent, plan=phase26_plan)
        strategy_ready, strategy_gate, strategy_reasons = self._strategy_gate(
            intent=intent,
            context=strategy_context,
        )
        global_reasons = (*phase26_reasons, *strategy_reasons)
        global_ready = policy is not None and not phase26_reasons and strategy_ready
        effective_policy = policy or OrchestrationPolicy.BEST_EFFORT

        plans: list[Phase27AccountAuthorization] = []
        for account in phase26_plan.get("accounts") or []:
            plans.append(
                self._authorize_account(
                    intent=intent,
                    account=account,
                    global_ready=global_ready,
                    global_reasons=global_reasons,
                )
            )

        batch = self._batch(
            intent=intent,
            plans=tuple(plans),
            policy=effective_policy,
            strategy_gate=strategy_gate,
            global_ready=global_ready,
            global_reasons=global_reasons,
        )
        payload = batch.to_dict()
        payload["phase"] = "LONDRES_PHASE27_LIVE_ACCOUNT_EXECUTION_AUTHORIZATION"
        return payload

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"execution_authorization_state": context}

    @staticmethod
    def _phase26_contract(
        *, intent: TradeIntent, plan: dict[str, Any]
    ) -> tuple[OrchestrationPolicy | None, tuple[str, ...]]:
        reasons: list[str] = []
        try:
            policy = OrchestrationPolicy(str(plan.get("policy")))
        except ValueError:
            policy = None
            reasons.append("VALID_PHASE26_ORCHESTRATION_POLICY_REQUIRED")

        if plan.get("trade_id") != intent.trade_id:
            reasons.append("PHASE26_TRADE_ID_DOES_NOT_MATCH_INTENT")
        phase26_symbol = canonicalize_symbol(str(plan.get("canonical_symbol") or ""))
        if phase26_symbol != intent.canonical:
            reasons.append("PHASE26_CANONICAL_SYMBOL_DOES_NOT_MATCH_INTENT")
        return policy, tuple(reasons)

    def _strategy_gate(
        self,
        *,
        intent: TradeIntent,
        context: dict[str, Any],
    ) -> tuple[bool, dict[str, Any], tuple[str, ...]]:
        execution_gate = context.get("execution_gate") or {}
        pre_broker = context.get("pre_broker_validation") or {}
        entry = context.get("entry_execution") or {}
        stop = context.get("executable_stop") or {}
        calculation = context.get("trade_calculation") or {}

        smt_detected = execution_gate.get("smt_detected") is True
        csd_confirmed = execution_gate.get("csd_confirmed_after_smt") is True
        iof_confirmed = execution_gate.get("post_csd_iofc_confirmed") is True
        smt_validated = execution_gate.get("smt_validated") is True
        gate_direction = str(execution_gate.get("direction") or "UNCONFIRMED")
        iof_aligned = iof_confirmed and gate_direction == intent.direction
        pre_broker_authorized = bool(
            pre_broker.get("status") == "AUTHORIZED" and pre_broker.get("order_authorized") is True
        )

        reasons: list[str] = []
        if not smt_detected:
            reasons.append("SMT_DETECTED_REQUIRED")
        if not csd_confirmed:
            reasons.append("CSD_CONFIRMED_AFTER_SMT_REQUIRED")
        if not iof_aligned:
            reasons.append("POST_CSD_IOF_ALIGNMENT_REQUIRED")
        if not smt_validated:
            reasons.append("SMT_CSD_IOF_VALIDATION_REQUIRED")
        if not pre_broker_authorized:
            reasons.append("PHASE17_HARD_PRE_BROKER_AUTHORIZATION_REQUIRED")

        if gate_direction != intent.direction:
            reasons.append("STRATEGY_DIRECTION_DOES_NOT_MATCH_TRADE_INTENT")
        if entry.get("status") != "ENTRY_TRIGGERED":
            reasons.append("ENTRY_TRIGGERED_STATE_REQUIRED")
        if stop.get("status") != "READY":
            reasons.append("EXECUTABLE_STOP_READY_STATE_REQUIRED")
        if calculation.get("status") != "READY":
            reasons.append("TRADE_CALCULATION_READY_STATE_REQUIRED")

        calculation_symbol = canonicalize_symbol(str(calculation.get("symbol") or ""))
        if calculation_symbol != intent.canonical:
            reasons.append("STRATEGY_SYMBOL_DOES_NOT_MATCH_TRADE_INTENT")
        if str(calculation.get("direction") or "") != intent.direction:
            reasons.append("TRADE_CALCULATION_DIRECTION_DOES_NOT_MATCH_INTENT")
        if str(calculation.get("selected_exit_mode") or "") != intent.selected_exit_mode:
            reasons.append("EXIT_MODE_DOES_NOT_MATCH_TRADE_INTENT")

        self._match_price(
            reasons,
            actual=entry.get("exact_entry_price"),
            expected=intent.entry_price,
            reason="ENTRY_PRICE_DOES_NOT_MATCH_TRADE_INTENT",
        )
        self._match_price(
            reasons,
            actual=stop.get("executable_stop_price"),
            expected=intent.stop_price,
            reason="STOP_PRICE_DOES_NOT_MATCH_TRADE_INTENT",
        )
        self._match_price(
            reasons,
            actual=calculation.get("selected_target_price"),
            expected=intent.target_price,
            reason="TARGET_PRICE_DOES_NOT_MATCH_TRADE_INTENT",
        )

        strategy_gate = {
            "smt_detected": smt_detected,
            "csd_confirmed": csd_confirmed,
            "iof_aligned": iof_aligned,
            "smt_validated": smt_validated,
            "pre_broker_authorized": pre_broker_authorized,
            "direction": gate_direction,
            "required_sequence": "SMT_DETECTED+CSD_CONFIRMED+IOF_ALIGNED",
            "phase17_volume_used": False,
        }
        return not reasons, strategy_gate, tuple(reasons)

    @staticmethod
    def _match_price(
        reasons: list[str],
        *,
        actual: Any,
        expected: float,
        reason: str,
    ) -> None:
        try:
            value = float(actual)
        except (TypeError, ValueError):
            reasons.append(reason)
            return
        if not math.isfinite(value) or not math.isclose(
            value,
            float(expected),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            reasons.append(reason)

    def _authorize_account(
        self,
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase27AccountAuthorization:
        alias = str(account.get("account_alias") or "")
        if account.get("status") == "SKIPPED_DISABLED":
            return self._blocked(
                intent=intent,
                alias=alias,
                status=Phase27AccountStatus.SKIPPED_DISABLED,
                account=account,
                reasons=("ACCOUNT_DISABLED_BY_USER_CONFIGURATION",),
            )
        if not account.get("preparation_ready"):
            return self._blocked(
                intent=intent,
                alias=alias,
                status=Phase27AccountStatus.BLOCKED_PHASE26,
                account=account,
                reasons=("PHASE26_ACCOUNT_PREPARATION_REQUIRED",),
            )
        if not global_ready:
            status = (
                Phase27AccountStatus.BLOCKED_INTENT_MISMATCH
                if any("MATCH" in reason for reason in global_reasons)
                else Phase27AccountStatus.BLOCKED_STRATEGY_GATE
            )
            return self._blocked(
                intent=intent,
                alias=alias,
                status=status,
                account=account,
                reasons=global_reasons or ("GLOBAL_EXECUTION_GATE_NOT_READY",),
            )

        selected_root = str(account.get("selected_root") or "").strip().upper()
        phase24 = account.get("phase24_account_plan") or {}
        active_contract = str(phase24.get("active_contract") or "").strip().upper()
        quantity = self._contract_quantity(account.get("prepared_contracts"))
        if not alias or not selected_root or not active_contract or quantity is None:
            return self._blocked(
                intent=intent,
                alias=alias,
                status=Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE,
                account=account,
                reasons=("EXACT_ACCOUNT_CONTRACT_AND_POSITIVE_INTEGER_QUANTITY_REQUIRED",),
            )
        if not active_contract.startswith(f"{selected_root} "):
            return self._blocked(
                intent=intent,
                alias=alias,
                status=Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE,
                account=account,
                reasons=("ACTIVE_CONTRACT_ROOT_DOES_NOT_MATCH_PHASE26_SELECTED_ROOT",),
            )

        fingerprint = self._fingerprint(
            intent=intent,
            account_alias=alias,
            active_contract=active_contract,
            quantity=quantity,
        )
        return Phase27AccountAuthorization(
            account_alias=alias,
            status=Phase27AccountStatus.AUTHORIZED_FOR_EXECUTION_HANDOFF,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            selected_root=selected_root,
            active_contract=active_contract,
            contract_quantity=quantity,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            selected_exit_mode=intent.selected_exit_mode,
            execution_handoff_ready=True,
            order_authorized=True,
            authorization_fingerprint=fingerprint,
            phase26_account_plan=account,
            reason_codes=(
                "SMT_CSD_IOF_STRATEGY_GATE_CONFIRMED",
                "PHASE17_HARD_VALIDATION_CONFIRMED",
                "PHASE26_LIVE_ACCOUNT_PLAN_CONFIRMED",
                "CONTRACT_QUANTITY_TAKEN_ONLY_FROM_PHASE26_ACCOUNT_PLAN",
                "AUTHORIZED_FOR_FUTURE_EXECUTION_ADAPTER_HANDOFF",
            ),
        )

    @staticmethod
    def _contract_quantity(value: Any) -> int | None:
        try:
            quantity = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(quantity) or quantity <= 0:
            return None
        rounded = round(quantity)
        if not math.isclose(quantity, rounded, rel_tol=0.0, abs_tol=1e-12):
            return None
        return int(rounded)

    @staticmethod
    def _fingerprint(
        *,
        intent: TradeIntent,
        account_alias: str,
        active_contract: str,
        quantity: int,
    ) -> str:
        payload = {
            "account_alias": account_alias,
            "active_contract": active_contract,
            "canonical_symbol": intent.canonical,
            "contract_quantity": quantity,
            "direction": intent.direction,
            "entry_price": intent.entry_price,
            "selected_exit_mode": intent.selected_exit_mode,
            "stop_price": intent.stop_price,
            "target_price": intent.target_price,
            "trade_id": intent.trade_id,
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _blocked(
        *,
        intent: TradeIntent,
        alias: str,
        status: Phase27AccountStatus,
        account: dict[str, Any],
        reasons: tuple[str, ...],
    ) -> Phase27AccountAuthorization:
        selected_root = account.get("selected_root")
        phase24 = account.get("phase24_account_plan") or {}
        active_contract = phase24.get("active_contract")
        return Phase27AccountAuthorization(
            account_alias=alias,
            status=status,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            selected_root=selected_root,
            active_contract=active_contract,
            contract_quantity=None,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            selected_exit_mode=intent.selected_exit_mode,
            execution_handoff_ready=False,
            order_authorized=False,
            authorization_fingerprint=None,
            phase26_account_plan=account,
            reason_codes=reasons,
        )

    def _batch(
        self,
        *,
        intent: TradeIntent,
        plans: tuple[Phase27AccountAuthorization, ...],
        policy: OrchestrationPolicy,
        strategy_gate: dict[str, Any],
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase27AuthorizationBatch:
        skipped = sum(item.status is Phase27AccountStatus.SKIPPED_DISABLED for item in plans)
        enabled = len(plans) - skipped
        candidates = sum(item.execution_handoff_ready for item in plans)
        blocked = enabled - candidates

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            ready = False
            reasons = ("NO_ENABLED_LIVE_ACCOUNTS_FOR_PHASE27",)
        elif not global_ready:
            status = MultiAccountBatchStatus.BLOCKED
            ready = False
            reasons = global_reasons or ("GLOBAL_EXECUTION_GATE_NOT_READY",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE and blocked:
            status = MultiAccountBatchStatus.BLOCKED
            ready = False
            reasons = ("ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_PASS_PHASE27",)
            plans = tuple(
                self._revoke_for_batch_policy(item)
                if item.execution_handoff_ready
                else item
                for item in plans
            )
            candidates = 0
            blocked = enabled
        elif candidates == enabled:
            status = MultiAccountBatchStatus.READY
            ready = True
            reasons = ("ALL_ENABLED_LIVE_ACCOUNTS_AUTHORIZED_FOR_EXECUTION_HANDOFF",)
        elif candidates > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            ready = True
            reasons = ("BEST_EFFORT_AUTHORIZES_ONLY_ACCOUNTS_THAT_PASSED_PHASE27",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            ready = False
            reasons = ("NO_LIVE_ACCOUNT_PASSED_PHASE27_EXECUTION_AUTHORIZATION",)

        return Phase27AuthorizationBatch(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            policy=policy,
            status=status,
            accounts=plans,
            enabled_accounts=enabled,
            authorized_accounts=candidates,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            execution_handoff_ready=ready,
            order_authorized=ready,
            strategy_gate=strategy_gate,
            reason_codes=(*reasons, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE27"),
        )

    @staticmethod
    def _revoke_for_batch_policy(
        account: Phase27AccountAuthorization,
    ) -> Phase27AccountAuthorization:
        return replace(
            account,
            status=Phase27AccountStatus.BLOCKED_BATCH_POLICY,
            execution_handoff_ready=False,
            order_authorized=False,
            authorization_fingerprint=None,
            reason_codes=(
                "ACCOUNT_PASSED_INDIVIDUALLY_BUT_ALL_OR_NONE_BATCH_POLICY_BLOCKED_HANDOFF",
            ),
        )
