"""Phase 30: universal deterministic execution authorization.

Phase 30 normalizes the three supported live-broker paths into one account-level
authorization envelope:

- NinjaTrader futures enter through an already-authorized Phase 27 envelope;
- FP Markets cTrader and Vantage MT5 enter through Phase 29 supervised,
  independently risk-sized account plans.

The engine re-verifies the Londres strategy gate and exact account geometry,
then emits a deterministic SHA-256 authorization fingerprint. It does not submit,
modify, cancel, close, or flatten any broker order.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import (
    BrokerType,
    OrchestrationPolicy,
    TradeIntent,
    canonicalize_symbol,
)

from .multi_account import MultiAccountBatchStatus
from .risk_sizing import ALLOWED_RISK_FRACTIONS, MAX_ACCOUNT_RISK_FRACTION


class Phase30BrokerVenue(str, Enum):
    NINJATRADER = "NINJATRADER"
    FP_MARKETS_CTRADER = "FP_MARKETS_CTRADER"
    VANTAGE_MT5 = "VANTAGE_MT5"


class Phase30AccountStatus(str, Enum):
    AUTHORIZED_FOR_UNIVERSAL_HANDOFF = "AUTHORIZED_FOR_UNIVERSAL_HANDOFF"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_STRATEGY_GATE = "BLOCKED_STRATEGY_GATE"
    BLOCKED_UPSTREAM_PLAN = "BLOCKED_UPSTREAM_PLAN"
    BLOCKED_INTENT_MISMATCH = "BLOCKED_INTENT_MISMATCH"
    BLOCKED_BROKER_ENVELOPE = "BLOCKED_BROKER_ENVELOPE"
    BLOCKED_RISK_ENVELOPE = "BLOCKED_RISK_ENVELOPE"
    BLOCKED_BATCH_POLICY = "BLOCKED_BATCH_POLICY"


@dataclass(frozen=True)
class Phase30AccountAuthorization:
    account_alias: str
    venue: Phase30BrokerVenue
    broker_type: BrokerType
    status: Phase30AccountStatus
    trade_id: str
    canonical_symbol: str
    broker_symbol: str | None
    broker_name: str | None
    direction: str
    prepared_volume: float | None
    volume_unit: str | None
    selected_risk_fraction: float | None
    account_equity: float | None
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    entry_price: float
    stop_price: float
    target_price: float
    selected_exit_mode: str
    execution_handoff_ready: bool
    order_authorized: bool
    authorization_fingerprint: str | None
    upstream_authorization_fingerprint: str | None
    source_phase: str
    upstream_account_plan: dict[str, Any] | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["venue"] = self.venue.value
        payload["broker_type"] = self.broker_type.value
        payload["status"] = self.status.value
        payload["account_scope"] = "LIVE_BROKERAGE_ACCOUNTS_ONLY"
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["execution_enabled"] = False
        payload["order_submission_enabled"] = False
        payload["broker_order_placed"] = False
        return payload


@dataclass(frozen=True)
class Phase30AuthorizationBatch:
    trade_id: str
    canonical_symbol: str
    direction: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase30AccountAuthorization, ...]
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
            "phase": "LONDRES_PHASE30_UNIVERSAL_EXECUTION_AUTHORIZATION",
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
            "authorization_authority": "LONDRES_PHASE30_UNIVERSAL_ACCOUNT_HANDOFF_GATE",
            "position_source": "ACCOUNT_SPECIFIC_CURRENT_EQUITY_RISK_PLAN",
            "supported_venues": [venue.value for venue in Phase30BrokerVenue],
            "account_scope": "LIVE_BROKERAGE_ACCOUNTS_ONLY",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase30UniversalExecutionAuthorizationEngine:
    """Authorize exact multi-broker account envelopes without submitting orders."""

    def authorize(
        self,
        *,
        intent: TradeIntent,
        strategy_context: dict[str, Any],
        phase29_plan: dict[str, Any] | None = None,
        phase27_plan: dict[str, Any] | None = None,
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> dict[str, Any]:
        strategy_ready, strategy_gate, strategy_reasons = self._strategy_gate(
            intent=intent,
            context=strategy_context,
        )

        plans: list[Phase30AccountAuthorization] = []
        source_reasons: list[str] = []
        if phase27_plan is not None:
            ninja_plans, reasons = self._ninjatrader_authorizations(
                intent=intent,
                plan=phase27_plan,
                strategy_ready=strategy_ready,
                strategy_reasons=strategy_reasons,
            )
            plans.extend(ninja_plans)
            source_reasons.extend(reasons)
        if phase29_plan is not None:
            cfd_plans, reasons = self._cfd_authorizations(
                intent=intent,
                plan=phase29_plan,
                strategy_ready=strategy_ready,
                strategy_reasons=strategy_reasons,
            )
            plans.extend(cfd_plans)
            source_reasons.extend(reasons)

        if phase27_plan is None and phase29_plan is None:
            source_reasons.append("AT_LEAST_ONE_SUPPORTED_UPSTREAM_BROKER_PLAN_REQUIRED")

        aliases = [item.account_alias for item in plans if item.account_alias]
        if len(set(aliases)) != len(aliases):
            plans = [
                self._block_existing(
                    item,
                    Phase30AccountStatus.BLOCKED_BROKER_ENVELOPE,
                    "UNIVERSAL_ACCOUNT_ALIASES_MUST_BE_UNIQUE",
                )
                if item.status is not Phase30AccountStatus.SKIPPED_DISABLED
                else item
                for item in plans
            ]
            source_reasons.append("UNIVERSAL_ACCOUNT_ALIASES_MUST_BE_UNIQUE")

        global_ready = strategy_ready and not source_reasons
        batch = self._batch(
            intent=intent,
            plans=tuple(plans),
            policy=policy,
            strategy_gate=strategy_gate,
            global_ready=global_ready,
            global_reasons=tuple((*strategy_reasons, *source_reasons)),
        )
        return batch.to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"universal_execution_authorization_state": context}

    def _ninjatrader_authorizations(
        self,
        *,
        intent: TradeIntent,
        plan: dict[str, Any],
        strategy_ready: bool,
        strategy_reasons: tuple[str, ...],
    ) -> tuple[list[Phase30AccountAuthorization], tuple[str, ...]]:
        reasons = self._validate_upstream_batch(
            intent=intent,
            plan=plan,
            expected_phase_prefix="LONDRES_PHASE27",
        )
        result: list[Phase30AccountAuthorization] = []
        for account in plan.get("accounts") or []:
            alias = str(account.get("account_alias") or "")
            if account.get("status") == "SKIPPED_DISABLED":
                result.append(
                    self._blocked(
                        intent=intent,
                        alias=alias,
                        venue=Phase30BrokerVenue.NINJATRADER,
                        broker_type=BrokerType.NINJATRADER,
                        source_phase="PHASE27",
                        upstream=account,
                        status=Phase30AccountStatus.SKIPPED_DISABLED,
                        reasons=("ACCOUNT_DISABLED_BY_USER_CONFIGURATION",),
                    )
                )
                continue

            account_reasons: list[str] = []
            if account.get("status") != "AUTHORIZED_FOR_EXECUTION_HANDOFF":
                account_reasons.append("PHASE27_ACCOUNT_AUTHORIZATION_REQUIRED")
            if account.get("execution_handoff_ready") is not True:
                account_reasons.append("PHASE27_EXECUTION_HANDOFF_READY_REQUIRED")
            if account.get("order_authorized") is not True:
                account_reasons.append("PHASE27_ORDER_AUTHORIZATION_REQUIRED")
            if canonicalize_symbol(str(account.get("canonical_symbol") or "")) != intent.canonical:
                account_reasons.append("PHASE27_ACCOUNT_SYMBOL_DOES_NOT_MATCH_INTENT")
            if str(account.get("direction") or "") != intent.direction:
                account_reasons.append("PHASE27_ACCOUNT_DIRECTION_DOES_NOT_MATCH_INTENT")
            self._match_trade_geometry(account_reasons, intent=intent, account=account)

            fingerprint = str(account.get("authorization_fingerprint") or "")
            if not self._valid_sha256(fingerprint):
                account_reasons.append("VALID_PHASE27_AUTHORIZATION_FINGERPRINT_REQUIRED")

            broker_symbol = str(account.get("active_contract") or "").strip() or None
            quantity = self._positive_number(account.get("contract_quantity"))
            if quantity is None or not math.isclose(quantity, round(quantity), abs_tol=1e-12):
                account_reasons.append("POSITIVE_INTEGER_NINJATRADER_CONTRACT_QUANTITY_REQUIRED")
            if not broker_symbol:
                account_reasons.append("EXACT_NINJATRADER_ACTIVE_CONTRACT_REQUIRED")

            if reasons or strategy_reasons or not strategy_ready or account_reasons:
                result.append(
                    self._blocked(
                        intent=intent,
                        alias=alias,
                        venue=Phase30BrokerVenue.NINJATRADER,
                        broker_type=BrokerType.NINJATRADER,
                        source_phase="PHASE27",
                        upstream=account,
                        broker_symbol=broker_symbol,
                        prepared_volume=quantity,
                        volume_unit="contracts" if quantity is not None else None,
                        upstream_fingerprint=fingerprint if self._valid_sha256(fingerprint) else None,
                        status=(
                            Phase30AccountStatus.BLOCKED_STRATEGY_GATE
                            if strategy_reasons or not strategy_ready
                            else Phase30AccountStatus.BLOCKED_UPSTREAM_PLAN
                        ),
                        reasons=tuple((*reasons, *strategy_reasons, *account_reasons)),
                    )
                )
                continue

            universal_fingerprint = self._fingerprint(
                intent=intent,
                alias=alias,
                venue=Phase30BrokerVenue.NINJATRADER,
                broker_type=BrokerType.NINJATRADER,
                broker_symbol=broker_symbol,
                broker_name=None,
                prepared_volume=float(round(quantity)),
                volume_unit="contracts",
                risk_fraction=None,
                account_equity=None,
                upstream_fingerprint=fingerprint,
            )
            result.append(
                Phase30AccountAuthorization(
                    account_alias=alias,
                    venue=Phase30BrokerVenue.NINJATRADER,
                    broker_type=BrokerType.NINJATRADER,
                    status=Phase30AccountStatus.AUTHORIZED_FOR_UNIVERSAL_HANDOFF,
                    trade_id=intent.trade_id,
                    canonical_symbol=intent.canonical,
                    broker_symbol=broker_symbol,
                    broker_name=None,
                    direction=intent.direction,
                    prepared_volume=float(round(quantity)),
                    volume_unit="contracts",
                    selected_risk_fraction=None,
                    account_equity=None,
                    projected_cash_risk=None,
                    projected_equity_risk_fraction=None,
                    entry_price=intent.entry_price,
                    stop_price=intent.stop_price,
                    target_price=intent.target_price,
                    selected_exit_mode=intent.selected_exit_mode,
                    execution_handoff_ready=True,
                    order_authorized=True,
                    authorization_fingerprint=universal_fingerprint,
                    upstream_authorization_fingerprint=fingerprint,
                    source_phase="PHASE27",
                    upstream_account_plan=account,
                    reason_codes=(
                        "PHASE27_NINJATRADER_AUTHORIZATION_REVERIFIED",
                        "PHASE30_UNIVERSAL_FINGERPRINT_BOUND_TO_PHASE27_FINGERPRINT",
                        "AUTHORIZED_FOR_UNIVERSAL_EXECUTION_ADAPTER_HANDOFF",
                    ),
                )
            )
        return result, reasons

    def _cfd_authorizations(
        self,
        *,
        intent: TradeIntent,
        plan: dict[str, Any],
        strategy_ready: bool,
        strategy_reasons: tuple[str, ...],
    ) -> tuple[list[Phase30AccountAuthorization], tuple[str, ...]]:
        reasons = self._validate_upstream_batch(
            intent=intent,
            plan=plan,
            expected_phase_prefix="LONDRES_PHASE29",
        )
        result: list[Phase30AccountAuthorization] = []
        for account in plan.get("accounts") or []:
            alias = str(account.get("account_alias") or "")
            venue = self._phase29_venue(account.get("venue"))
            broker_type = self._broker_type(account.get("broker_type"))
            if account.get("status") == "SKIPPED_DISABLED":
                result.append(
                    self._blocked(
                        intent=intent,
                        alias=alias,
                        venue=venue or Phase30BrokerVenue.FP_MARKETS_CTRADER,
                        broker_type=broker_type or BrokerType.CUSTOM,
                        source_phase="PHASE29",
                        upstream=account,
                        status=Phase30AccountStatus.SKIPPED_DISABLED,
                        reasons=("ACCOUNT_DISABLED_BY_USER_CONFIGURATION",),
                    )
                )
                continue

            account_reasons: list[str] = []
            if venue is None:
                account_reasons.append("SUPPORTED_PHASE29_BROKER_VENUE_REQUIRED")
            if broker_type is None:
                account_reasons.append("VALID_PHASE29_BROKER_TYPE_REQUIRED")
            elif venue is Phase30BrokerVenue.FP_MARKETS_CTRADER and broker_type is not BrokerType.CTRADER:
                account_reasons.append("FP_MARKETS_PHASE30_REQUIRES_CTRADER")
            elif venue is Phase30BrokerVenue.VANTAGE_MT5 and broker_type is not BrokerType.MT5:
                account_reasons.append("VANTAGE_PHASE30_REQUIRES_MT5")

            if account.get("status") != "READY" or account.get("preparation_ready") is not True:
                account_reasons.append("PHASE29_ACCOUNT_PREPARATION_REQUIRED")
            if account.get("trade_id") != intent.trade_id:
                account_reasons.append("PHASE29_ACCOUNT_TRADE_ID_DOES_NOT_MATCH_INTENT")
            if canonicalize_symbol(str(account.get("canonical_symbol") or "")) != intent.canonical:
                account_reasons.append("PHASE29_ACCOUNT_SYMBOL_DOES_NOT_MATCH_INTENT")

            broker_symbol = str(account.get("broker_symbol") or "").strip() or None
            broker_name = str(account.get("broker_name") or "").strip() or None
            if not broker_symbol:
                account_reasons.append("EXACT_CFD_BROKER_SYMBOL_REQUIRED")
            if not self._provider_matches(venue, broker_name):
                account_reasons.append("PHASE30_BROKER_IDENTITY_REVERIFICATION_FAILED")

            supervision = account.get("supervision_state") or {}
            if supervision.get("execution_data_ready") is not True:
                account_reasons.append("PHASE29_BROKER_SUPERVISION_READY_STATE_REQUIRED")
            if canonicalize_symbol(str(supervision.get("canonical_symbol") or "")) != intent.canonical:
                account_reasons.append("SUPERVISION_SYMBOL_DOES_NOT_MATCH_INTENT")
            if broker_symbol and str(supervision.get("broker_symbol") or "") != broker_symbol:
                account_reasons.append("SUPERVISION_BROKER_SYMBOL_DOES_NOT_MATCH_PHASE29")

            phase23 = account.get("phase23_account_plan") or {}
            prepared_volume, volume_unit, risk_fraction, equity, cash_risk, equity_risk = (
                self._validate_phase23_account(
                    intent=intent,
                    alias=alias,
                    broker_symbol=broker_symbol,
                    account=phase23,
                    reasons=account_reasons,
                )
            )

            effective_venue = venue or Phase30BrokerVenue.FP_MARKETS_CTRADER
            effective_type = broker_type or BrokerType.CUSTOM
            if reasons or strategy_reasons or not strategy_ready or account_reasons:
                result.append(
                    self._blocked(
                        intent=intent,
                        alias=alias,
                        venue=effective_venue,
                        broker_type=effective_type,
                        source_phase="PHASE29",
                        upstream=account,
                        broker_symbol=broker_symbol,
                        broker_name=broker_name,
                        prepared_volume=prepared_volume,
                        volume_unit=volume_unit,
                        risk_fraction=risk_fraction,
                        account_equity=equity,
                        projected_cash_risk=cash_risk,
                        projected_equity_risk_fraction=equity_risk,
                        status=(
                            Phase30AccountStatus.BLOCKED_STRATEGY_GATE
                            if strategy_reasons or not strategy_ready
                            else Phase30AccountStatus.BLOCKED_BROKER_ENVELOPE
                        ),
                        reasons=tuple((*reasons, *strategy_reasons, *account_reasons)),
                    )
                )
                continue

            fingerprint = self._fingerprint(
                intent=intent,
                alias=alias,
                venue=effective_venue,
                broker_type=effective_type,
                broker_symbol=broker_symbol,
                broker_name=broker_name,
                prepared_volume=prepared_volume,
                volume_unit=volume_unit,
                risk_fraction=risk_fraction,
                account_equity=equity,
                upstream_fingerprint=None,
            )
            result.append(
                Phase30AccountAuthorization(
                    account_alias=alias,
                    venue=effective_venue,
                    broker_type=effective_type,
                    status=Phase30AccountStatus.AUTHORIZED_FOR_UNIVERSAL_HANDOFF,
                    trade_id=intent.trade_id,
                    canonical_symbol=intent.canonical,
                    broker_symbol=broker_symbol,
                    broker_name=broker_name,
                    direction=intent.direction,
                    prepared_volume=prepared_volume,
                    volume_unit=volume_unit,
                    selected_risk_fraction=risk_fraction,
                    account_equity=equity,
                    projected_cash_risk=cash_risk,
                    projected_equity_risk_fraction=equity_risk,
                    entry_price=intent.entry_price,
                    stop_price=intent.stop_price,
                    target_price=intent.target_price,
                    selected_exit_mode=intent.selected_exit_mode,
                    execution_handoff_ready=True,
                    order_authorized=True,
                    authorization_fingerprint=fingerprint,
                    upstream_authorization_fingerprint=None,
                    source_phase="PHASE29",
                    upstream_account_plan=account,
                    reason_codes=(
                        "SMT_CSD_IOF_STRATEGY_GATE_CONFIRMED",
                        "PHASE17_HARD_VALIDATION_CONFIRMED",
                        "PHASE29_BROKER_IDENTITY_AND_SUPERVISION_REVERIFIED",
                        "PHASE23_CURRENT_EQUITY_RISK_PLAN_REVERIFIED",
                        "EXACT_BROKER_SYMBOL_AND_ACCOUNT_VOLUME_LOCKED",
                        "AUTHORIZED_FOR_UNIVERSAL_EXECUTION_ADAPTER_HANDOFF",
                    ),
                )
            )
        return result, reasons

    @staticmethod
    def _validate_upstream_batch(
        *,
        intent: TradeIntent,
        plan: dict[str, Any],
        expected_phase_prefix: str,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if plan.get("trade_id") != intent.trade_id:
            reasons.append(f"{expected_phase_prefix}_TRADE_ID_DOES_NOT_MATCH_INTENT")
        if canonicalize_symbol(str(plan.get("canonical_symbol") or "")) != intent.canonical:
            reasons.append(f"{expected_phase_prefix}_SYMBOL_DOES_NOT_MATCH_INTENT")
        phase = str(plan.get("phase") or "")
        if phase and not phase.startswith(expected_phase_prefix):
            reasons.append(f"{expected_phase_prefix}_PLAN_PHASE_MARKER_MISMATCH")
        accounts = plan.get("accounts")
        if not isinstance(accounts, list):
            reasons.append(f"{expected_phase_prefix}_ACCOUNT_LIST_REQUIRED")
        return tuple(reasons)

    def _validate_phase23_account(
        self,
        *,
        intent: TradeIntent,
        alias: str,
        broker_symbol: str | None,
        account: dict[str, Any],
        reasons: list[str],
    ) -> tuple[float | None, str | None, float | None, float | None, float | None, float | None]:
        if account.get("status") != "READY_FOR_EXECUTION_ADAPTER":
            reasons.append("PHASE23_READY_FOR_EXECUTION_ADAPTER_REQUIRED")
        if account.get("preparation_ready") is not True:
            reasons.append("PHASE23_PREPARATION_READY_REQUIRED")
        if account.get("trade_id") != intent.trade_id:
            reasons.append("PHASE23_TRADE_ID_DOES_NOT_MATCH_INTENT")
        if str(account.get("account_alias") or "") != alias:
            reasons.append("PHASE23_ACCOUNT_ALIAS_DOES_NOT_MATCH_PHASE29")
        if canonicalize_symbol(str(account.get("canonical_symbol") or "")) != intent.canonical:
            reasons.append("PHASE23_CANONICAL_SYMBOL_DOES_NOT_MATCH_INTENT")
        if broker_symbol and str(account.get("broker_symbol") or "") != broker_symbol:
            reasons.append("PHASE23_BROKER_SYMBOL_DOES_NOT_MATCH_PHASE29")
        if str(account.get("selected_exit_mode") or "") != intent.selected_exit_mode:
            reasons.append("PHASE23_EXIT_MODE_DOES_NOT_MATCH_INTENT")

        self._match_price(reasons, account.get("intended_entry_price"), intent.entry_price, "PHASE23_ENTRY_DOES_NOT_MATCH_INTENT")
        self._match_price(reasons, account.get("intended_stop_price"), intent.stop_price, "PHASE23_STOP_DOES_NOT_MATCH_INTENT")
        self._match_price(reasons, account.get("selected_target_price"), intent.target_price, "PHASE23_TARGET_DOES_NOT_MATCH_INTENT")

        volume = self._positive_number(account.get("prepared_volume"))
        if volume is None:
            reasons.append("POSITIVE_PHASE23_PREPARED_VOLUME_REQUIRED")
        unit = str(account.get("volume_unit") or "").strip().lower() or None
        if unit not in {"units", "lots"}:
            reasons.append("CFD_VOLUME_UNIT_MUST_BE_UNITS_OR_LOTS")

        risk_fraction = self._positive_number(account.get("selected_risk_fraction"))
        if risk_fraction is None or not any(
            math.isclose(risk_fraction, allowed, abs_tol=1e-12)
            for allowed in ALLOWED_RISK_FRACTIONS
        ):
            reasons.append("PHASE23_RISK_FRACTION_MUST_BE_EXACTLY_3_5_OR_10_PERCENT")

        equity = self._positive_number(account.get("account_equity"))
        cash_risk = self._positive_number(account.get("projected_cash_risk"))
        equity_risk = self._positive_number(account.get("projected_equity_risk_fraction"))
        if equity is None:
            reasons.append("POSITIVE_CURRENT_ACCOUNT_EQUITY_REQUIRED")
        if cash_risk is None:
            reasons.append("POSITIVE_PROJECTED_CASH_RISK_REQUIRED")
        if equity_risk is None:
            reasons.append("POSITIVE_PROJECTED_EQUITY_RISK_FRACTION_REQUIRED")
        elif risk_fraction is not None and equity_risk > risk_fraction + 1e-12:
            reasons.append("PROJECTED_RISK_EXCEEDS_SELECTED_ACCOUNT_RISK_FRACTION")
        if equity_risk is not None and equity_risk > MAX_ACCOUNT_RISK_FRACTION + 1e-12:
            reasons.append("PROJECTED_RISK_EXCEEDS_LONDRES_HARD_ACCOUNT_LIMIT")

        return volume, unit, risk_fraction, equity, cash_risk, equity_risk

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
            pre_broker.get("status") == "AUTHORIZED"
            and pre_broker.get("order_authorized") is True
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
        if str(entry.get("direction") or "") != intent.direction:
            reasons.append("ENTRY_DIRECTION_DOES_NOT_MATCH_TRADE_INTENT")
        if str(stop.get("direction") or "") != intent.direction:
            reasons.append("STOP_DIRECTION_DOES_NOT_MATCH_TRADE_INTENT")
        if str(calculation.get("direction") or "") != intent.direction:
            reasons.append("TRADE_CALCULATION_DIRECTION_DOES_NOT_MATCH_INTENT")
        if str(pre_broker.get("direction") or "") != intent.direction:
            reasons.append("PRE_BROKER_DIRECTION_DOES_NOT_MATCH_INTENT")
        if canonicalize_symbol(str(calculation.get("symbol") or "")) != intent.canonical:
            reasons.append("STRATEGY_SYMBOL_DOES_NOT_MATCH_TRADE_INTENT")
        if canonicalize_symbol(str(pre_broker.get("symbol") or "")) != intent.canonical:
            reasons.append("PRE_BROKER_SYMBOL_DOES_NOT_MATCH_TRADE_INTENT")
        if str(calculation.get("selected_exit_mode") or "") != intent.selected_exit_mode:
            reasons.append("EXIT_MODE_DOES_NOT_MATCH_TRADE_INTENT")

        self._match_price(reasons, entry.get("exact_entry_price"), intent.entry_price, "ENTRY_PRICE_DOES_NOT_MATCH_TRADE_INTENT")
        self._match_price(reasons, stop.get("executable_stop_price"), intent.stop_price, "STOP_PRICE_DOES_NOT_MATCH_TRADE_INTENT")
        self._match_price(reasons, calculation.get("selected_target_price"), intent.target_price, "TARGET_PRICE_DOES_NOT_MATCH_TRADE_INTENT")

        gate = {
            "smt_detected": smt_detected,
            "csd_confirmed": csd_confirmed,
            "iof_aligned": iof_aligned,
            "smt_validated": smt_validated,
            "pre_broker_authorized": pre_broker_authorized,
            "direction": gate_direction,
            "required_sequence": "SMT_DETECTED+CSD_CONFIRMED+IOF_ALIGNED",
            "phase17_volume_used": False,
        }
        return not reasons, gate, tuple(reasons)

    @staticmethod
    def _match_trade_geometry(reasons: list[str], *, intent: TradeIntent, account: dict[str, Any]) -> None:
        LondresPhase30UniversalExecutionAuthorizationEngine._match_price(
            reasons, account.get("entry_price"), intent.entry_price, "UPSTREAM_ENTRY_DOES_NOT_MATCH_INTENT"
        )
        LondresPhase30UniversalExecutionAuthorizationEngine._match_price(
            reasons, account.get("stop_price"), intent.stop_price, "UPSTREAM_STOP_DOES_NOT_MATCH_INTENT"
        )
        LondresPhase30UniversalExecutionAuthorizationEngine._match_price(
            reasons, account.get("target_price"), intent.target_price, "UPSTREAM_TARGET_DOES_NOT_MATCH_INTENT"
        )
        if str(account.get("selected_exit_mode") or "") != intent.selected_exit_mode:
            reasons.append("UPSTREAM_EXIT_MODE_DOES_NOT_MATCH_INTENT")

    @staticmethod
    def _match_price(reasons: list[str], actual: Any, expected: float, reason: str) -> None:
        try:
            value = float(actual)
        except (TypeError, ValueError):
            reasons.append(reason)
            return
        if not math.isfinite(value) or not math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-9):
            reasons.append(reason)

    @staticmethod
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

    @staticmethod
    def _valid_sha256(value: str) -> bool:
        return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())

    @staticmethod
    def _phase29_venue(value: Any) -> Phase30BrokerVenue | None:
        try:
            venue = Phase30BrokerVenue(str(value))
        except ValueError:
            return None
        if venue is Phase30BrokerVenue.NINJATRADER:
            return None
        return venue

    @staticmethod
    def _broker_type(value: Any) -> BrokerType | None:
        try:
            return BrokerType(str(value))
        except ValueError:
            return None

    @staticmethod
    def _provider_matches(venue: Phase30BrokerVenue | None, broker_name: str | None) -> bool:
        normalized = "".join(
            character for character in str(broker_name or "").upper() if character.isalnum()
        )
        if venue is Phase30BrokerVenue.FP_MARKETS_CTRADER:
            return "FPMARKETS" in normalized
        if venue is Phase30BrokerVenue.VANTAGE_MT5:
            return "VANTAGE" in normalized
        return False

    @staticmethod
    def _fingerprint(
        *,
        intent: TradeIntent,
        alias: str,
        venue: Phase30BrokerVenue,
        broker_type: BrokerType,
        broker_symbol: str,
        broker_name: str | None,
        prepared_volume: float,
        volume_unit: str,
        risk_fraction: float | None,
        account_equity: float | None,
        upstream_fingerprint: str | None,
    ) -> str:
        payload = {
            "account_alias": alias,
            "account_equity": account_equity,
            "broker_name": broker_name,
            "broker_symbol": broker_symbol,
            "broker_type": broker_type.value,
            "canonical_symbol": intent.canonical,
            "direction": intent.direction,
            "entry_price": intent.entry_price,
            "prepared_volume": prepared_volume,
            "risk_fraction": risk_fraction,
            "selected_exit_mode": intent.selected_exit_mode,
            "stop_price": intent.stop_price,
            "target_price": intent.target_price,
            "trade_id": intent.trade_id,
            "upstream_authorization_fingerprint": upstream_fingerprint,
            "venue": venue.value,
            "volume_unit": volume_unit,
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _blocked(
        *,
        intent: TradeIntent,
        alias: str,
        venue: Phase30BrokerVenue,
        broker_type: BrokerType,
        source_phase: str,
        upstream: dict[str, Any],
        status: Phase30AccountStatus,
        reasons: tuple[str, ...],
        broker_symbol: str | None = None,
        broker_name: str | None = None,
        prepared_volume: float | None = None,
        volume_unit: str | None = None,
        risk_fraction: float | None = None,
        account_equity: float | None = None,
        projected_cash_risk: float | None = None,
        projected_equity_risk_fraction: float | None = None,
        upstream_fingerprint: str | None = None,
    ) -> Phase30AccountAuthorization:
        return Phase30AccountAuthorization(
            account_alias=alias,
            venue=venue,
            broker_type=broker_type,
            status=status,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            broker_name=broker_name,
            direction=intent.direction,
            prepared_volume=prepared_volume,
            volume_unit=volume_unit,
            selected_risk_fraction=risk_fraction,
            account_equity=account_equity,
            projected_cash_risk=projected_cash_risk,
            projected_equity_risk_fraction=projected_equity_risk_fraction,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            selected_exit_mode=intent.selected_exit_mode,
            execution_handoff_ready=False,
            order_authorized=False,
            authorization_fingerprint=None,
            upstream_authorization_fingerprint=upstream_fingerprint,
            source_phase=source_phase,
            upstream_account_plan=upstream,
            reason_codes=reasons,
        )

    @staticmethod
    def _block_existing(
        account: Phase30AccountAuthorization,
        status: Phase30AccountStatus,
        reason: str,
    ) -> Phase30AccountAuthorization:
        return replace(
            account,
            status=status,
            execution_handoff_ready=False,
            order_authorized=False,
            authorization_fingerprint=None,
            reason_codes=account.reason_codes + (reason,),
        )

    def _batch(
        self,
        *,
        intent: TradeIntent,
        plans: tuple[Phase30AccountAuthorization, ...],
        policy: OrchestrationPolicy,
        strategy_gate: dict[str, Any],
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase30AuthorizationBatch:
        skipped = sum(item.status is Phase30AccountStatus.SKIPPED_DISABLED for item in plans)
        enabled = len(plans) - skipped
        authorized = sum(item.execution_handoff_ready for item in plans)
        blocked = enabled - authorized

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            handoff_ready = False
            reasons = ("NO_ENABLED_SUPPORTED_BROKER_ACCOUNT_FOR_PHASE30",)
        elif not global_ready:
            status = MultiAccountBatchStatus.BLOCKED
            handoff_ready = False
            reasons = global_reasons or ("GLOBAL_PHASE30_AUTHORIZATION_GATE_NOT_READY",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE and blocked:
            plans = tuple(
                self._block_existing(
                    item,
                    Phase30AccountStatus.BLOCKED_BATCH_POLICY,
                    "ALL_OR_NONE_REVOKED_OTHERWISE_AUTHORIZED_PHASE30_ACCOUNT",
                )
                if item.execution_handoff_ready
                else item
                for item in plans
            )
            authorized = 0
            blocked = enabled
            status = MultiAccountBatchStatus.BLOCKED
            handoff_ready = False
            reasons = ("ALL_OR_NONE_REQUIRES_EVERY_ENABLED_PHASE30_ACCOUNT_AUTHORIZED",)
        elif authorized == enabled:
            status = MultiAccountBatchStatus.READY
            handoff_ready = True
            reasons = ("ALL_ENABLED_BROKER_ACCOUNTS_AUTHORIZED_FOR_UNIVERSAL_HANDOFF",)
        elif authorized > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            handoff_ready = True
            reasons = ("BEST_EFFORT_AUTHORIZES_ONLY_PHASE30_ACCOUNTS_THAT_PASSED",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            handoff_ready = False
            reasons = ("NO_SUPPORTED_ACCOUNT_PASSED_PHASE30_AUTHORIZATION",)

        return Phase30AuthorizationBatch(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            policy=policy,
            status=status,
            accounts=plans,
            enabled_accounts=enabled,
            authorized_accounts=authorized,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            execution_handoff_ready=handoff_ready,
            order_authorized=handoff_ready,
            strategy_gate=strategy_gate,
            reason_codes=(*reasons, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE30"),
        )
