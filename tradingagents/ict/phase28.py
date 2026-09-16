"""Phase 28: fail-closed pre-submit revalidation without broker execution.

Phase 28 consumes only Phase 27-authorized account envelopes. Immediately before
a future execution adapter is allowed to receive one, this layer re-verifies the
current NinjaTrader account, active contract, quote freshness, spread, adverse
entry deviation and risk against current broker-reported equity.

This module deliberately exposes no broker order-submission API.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Iterable

from tradingagents.brokers.contracts import (
    OrchestrationPolicy,
    TradeIntent,
    canonicalize_symbol,
)
from tradingagents.brokers.ninjatrader import NinjaTraderUniversalReadOnlyAdapter
from tradingagents.brokers.ninjatrader_futures import NINJATRADER_EQUITY_INDEX_FUTURES
from tradingagents.brokers.supervision import (
    BrokerConnectionSupervisor,
    BrokerSupervisionPolicy,
)

from .multi_account import MultiAccountBatchStatus
from .risk_sizing import ALLOWED_RISK_FRACTIONS, MAX_ACCOUNT_RISK_FRACTION


class Phase28AccountStatus(str, Enum):
    READY_FOR_SUBMISSION_ADAPTER_HANDOFF = "READY_FOR_SUBMISSION_ADAPTER_HANDOFF"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_PHASE27 = "BLOCKED_PHASE27"
    BLOCKED_DUPLICATE_AUTHORIZATION = "BLOCKED_DUPLICATE_AUTHORIZATION"
    BLOCKED_CONTRACT_REVALIDATION = "BLOCKED_CONTRACT_REVALIDATION"
    BLOCKED_SUPERVISION = "BLOCKED_SUPERVISION"
    BLOCKED_RISK_REVALIDATION = "BLOCKED_RISK_REVALIDATION"
    BLOCKED_MARKET_REVALIDATION = "BLOCKED_MARKET_REVALIDATION"
    BLOCKED_BATCH_POLICY = "BLOCKED_BATCH_POLICY"


@dataclass(frozen=True)
class Phase28PreSubmitPolicy:
    """Explicit market-quality limits for the immediate pre-submit check."""

    max_spread_ticks: int
    max_adverse_entry_deviation_ticks: int

    def __post_init__(self) -> None:
        for name, value in {
            "max_spread_ticks": self.max_spread_ticks,
            "max_adverse_entry_deviation_ticks": self.max_adverse_entry_deviation_ticks,
        }.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an explicit non-negative integer")


@dataclass(frozen=True)
class Phase28AccountRevalidation:
    account_alias: str
    status: Phase28AccountStatus
    trade_id: str
    canonical_symbol: str
    direction: str
    selected_root: str | None
    active_contract: str | None
    contract_quantity: int | None
    phase27_authorization_fingerprint: str | None
    pre_submit_snapshot_fingerprint: str | None
    current_equity: float | None
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    quote_bid: float | None
    quote_ask: float | None
    quote_timestamp_ms: int | None
    spread_ticks: float | None
    adverse_entry_deviation_ticks: float | None
    pre_submit_ready: bool
    order_authorized: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["account_scope"] = "LIVE_BROKERAGE_ACCOUNTS_ONLY"
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["execution_enabled"] = False
        payload["order_submission_enabled"] = False
        payload["broker_order_placed"] = False
        payload["fingerprint_consumed"] = False
        return payload


@dataclass(frozen=True)
class Phase28RevalidationBatch:
    trade_id: str
    canonical_symbol: str
    direction: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase28AccountRevalidation, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    pre_submit_ready: bool
    order_authorized: bool
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
            "ready_accounts": self.ready_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "pre_submit_ready": self.pre_submit_ready,
            "order_authorized": self.order_authorized,
            "authorization_authority": "LONDRES_PHASE28_PRE_SUBMIT_REVALIDATION_FIREWALL",
            "account_scope": "LIVE_BROKERAGE_ACCOUNTS_ONLY",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase28PreSubmitRevalidationEngine:
    """Revalidate Phase 27 envelopes against current read-only broker state."""

    def __init__(self) -> None:
        self._supervisor = BrokerConnectionSupervisor()

    def revalidate(
        self,
        *,
        intent: TradeIntent,
        phase27_plan: dict[str, Any],
        adapter: NinjaTraderUniversalReadOnlyAdapter,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        pre_submit_policy: Phase28PreSubmitPolicy,
        used_authorization_fingerprints: Iterable[str],
    ) -> dict[str, Any]:
        policy, global_reasons = self._phase27_contract(
            intent=intent,
            plan=phase27_plan,
        )
        global_ready = policy is not None and not global_reasons
        effective_policy = policy or OrchestrationPolicy.BEST_EFFORT
        used = {str(value).lower() for value in used_authorization_fingerprints}
        fingerprints_seen_in_batch: set[str] = set()

        plans: list[Phase28AccountRevalidation] = []
        for account in phase27_plan.get("accounts") or []:
            result = self._revalidate_account(
                intent=intent,
                account=account,
                adapter=adapter,
                now_ms=now_ms,
                supervision_policy=supervision_policy,
                pre_submit_policy=pre_submit_policy,
                used_fingerprints=used,
                fingerprints_seen_in_batch=fingerprints_seen_in_batch,
                global_ready=global_ready,
                global_reasons=global_reasons,
            )
            plans.append(result)
            fingerprint = result.phase27_authorization_fingerprint
            if fingerprint:
                fingerprints_seen_in_batch.add(fingerprint.lower())

        batch = self._batch(
            intent=intent,
            plans=tuple(plans),
            policy=effective_policy,
            global_ready=global_ready,
            global_reasons=global_reasons,
        )
        payload = batch.to_dict()
        payload["phase"] = "LONDRES_PHASE28_PRE_SUBMIT_REVALIDATION"
        payload["fingerprint_registry_mutated"] = False
        return payload

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"pre_submit_revalidation_state": context}

    @staticmethod
    def _phase27_contract(
        *,
        intent: TradeIntent,
        plan: dict[str, Any],
    ) -> tuple[OrchestrationPolicy | None, tuple[str, ...]]:
        reasons: list[str] = []
        try:
            policy = OrchestrationPolicy(str(plan.get("policy")))
        except ValueError:
            policy = None
            reasons.append("VALID_PHASE27_ORCHESTRATION_POLICY_REQUIRED")

        if plan.get("trade_id") != intent.trade_id:
            reasons.append("PHASE27_TRADE_ID_DOES_NOT_MATCH_INTENT")
        if canonicalize_symbol(str(plan.get("canonical_symbol") or "")) != intent.canonical:
            reasons.append("PHASE27_CANONICAL_SYMBOL_DOES_NOT_MATCH_INTENT")
        if str(plan.get("direction") or "") != intent.direction:
            reasons.append("PHASE27_DIRECTION_DOES_NOT_MATCH_INTENT")
        if plan.get("execution_handoff_ready") is not True:
            reasons.append("PHASE27_EXECUTION_HANDOFF_NOT_READY")
        if plan.get("order_authorized") is not True:
            reasons.append("PHASE27_ORDER_AUTHORIZATION_REQUIRED")
        if plan.get("order_submission_enabled") is not False:
            reasons.append("PHASE27_MUST_NOT_ENABLE_ORDER_SUBMISSION")
        return policy, tuple(reasons)

    def _revalidate_account(
        self,
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        adapter: NinjaTraderUniversalReadOnlyAdapter,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        pre_submit_policy: Phase28PreSubmitPolicy,
        used_fingerprints: set[str],
        fingerprints_seen_in_batch: set[str],
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase28AccountRevalidation:
        alias = str(account.get("account_alias") or "")
        if account.get("status") == "SKIPPED_DISABLED":
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.SKIPPED_DISABLED,
                reasons=("ACCOUNT_DISABLED_BEFORE_PHASE28",),
            )
        if (
            account.get("status") != "AUTHORIZED_FOR_EXECUTION_HANDOFF"
            or account.get("execution_handoff_ready") is not True
            or account.get("order_authorized") is not True
        ):
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_PHASE27,
                reasons=("PHASE27_ACCOUNT_AUTHORIZATION_REQUIRED",),
            )
        if not global_ready:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_PHASE27,
                reasons=global_reasons or ("PHASE27_BATCH_NOT_READY",),
            )

        fingerprint = str(account.get("authorization_fingerprint") or "").lower()
        if not self._valid_fingerprint(fingerprint):
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_PHASE27,
                reasons=("VALID_PHASE27_SHA256_AUTHORIZATION_FINGERPRINT_REQUIRED",),
            )
        if fingerprint in used_fingerprints or fingerprint in fingerprints_seen_in_batch:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_DUPLICATE_AUTHORIZATION,
                reasons=("PHASE27_AUTHORIZATION_FINGERPRINT_ALREADY_USED_OR_DUPLICATED",),
            )

        if (
            account.get("trade_id") != intent.trade_id
            or canonicalize_symbol(str(account.get("canonical_symbol") or "")) != intent.canonical
            or str(account.get("direction") or "") != intent.direction
            or not self._same_price(account.get("entry_price"), intent.entry_price)
            or not self._same_price(account.get("stop_price"), intent.stop_price)
            or not self._same_price(account.get("target_price"), intent.target_price)
            or str(account.get("selected_exit_mode") or "") != intent.selected_exit_mode
        ):
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_PHASE27,
                reasons=("PHASE27_ACCOUNT_ENVELOPE_DOES_NOT_MATCH_TRADE_INTENT",),
            )

        selected_root = str(account.get("selected_root") or "").strip().upper()
        active_contract = str(account.get("active_contract") or "").strip().upper()
        quantity = self._positive_integer(account.get("contract_quantity"))
        spec = NINJATRADER_EQUITY_INDEX_FUTURES.get(selected_root)
        if (
            not alias
            or not selected_root
            or not active_contract
            or quantity is None
            or spec is None
            or canonicalize_symbol(spec.canonical_symbol) != intent.canonical
        ):
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_CONTRACT_REVALIDATION,
                reasons=("PHASE27_FUTURES_CONTRACT_ENVELOPE_INVALID",),
            )

        try:
            resolution = adapter.resolve_active_contract(selected_root)
        except Exception:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_CONTRACT_REVALIDATION,
                reasons=("CURRENT_ACTIVE_CONTRACT_REVALIDATION_FAILED",),
            )
        if not resolution.ready or resolution.active_contract != active_contract:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_CONTRACT_REVALIDATION,
                reasons=("ACTIVE_CONTRACT_CHANGED_OR_ROLLOVER_NO_LONGER_VERIFIED",),
            )

        supervision = self._supervisor.check(
            adapter=adapter,
            account_alias=alias,
            canonical_symbol=intent.canonical,
            broker_symbol=active_contract,
            now_ms=now_ms,
            policy=supervision_policy,
        )
        if not supervision.execution_data_ready:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_SUPERVISION,
                reasons=(
                    "IMMEDIATE_PRE_SUBMIT_BROKER_SUPERVISION_FAILED",
                    *supervision.reason_codes,
                ),
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
            )

        try:
            current_account = adapter.account_snapshot(alias)
            instrument = adapter.instrument_snapshot(
                account_alias=alias,
                canonical_symbol=intent.canonical,
                broker_symbol=active_contract,
            )
        except Exception:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_SUPERVISION,
                reasons=("CURRENT_ACCOUNT_OR_INSTRUMENT_REVALIDATION_FAILED",),
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
            )

        risk_result = self._risk_revalidation(
            intent=intent,
            account=account,
            quantity=quantity,
            current_equity=current_account.equity,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value_account_currency,
            broker_max_volume=instrument.max_volume,
        )
        if risk_result["reasons"]:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_RISK_REVALIDATION,
                reasons=tuple(risk_result["reasons"]),
                current_equity=current_account.equity,
                projected_cash_risk=risk_result["projected_cash_risk"],
                projected_equity_risk_fraction=risk_result["projected_equity_risk_fraction"],
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
            )

        market_result = self._market_revalidation(
            intent=intent,
            bid=supervision.quote_bid,
            ask=supervision.quote_ask,
            tick_size=instrument.tick_size,
            policy=pre_submit_policy,
        )
        if market_result["reasons"]:
            return self._blocked(
                intent=intent,
                account=account,
                status=Phase28AccountStatus.BLOCKED_MARKET_REVALIDATION,
                reasons=tuple(market_result["reasons"]),
                current_equity=current_account.equity,
                projected_cash_risk=risk_result["projected_cash_risk"],
                projected_equity_risk_fraction=risk_result["projected_equity_risk_fraction"],
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
                spread_ticks=market_result["spread_ticks"],
                adverse_entry_deviation_ticks=market_result[
                    "adverse_entry_deviation_ticks"
                ],
            )

        snapshot_fingerprint = self._snapshot_fingerprint(
            phase27_fingerprint=fingerprint,
            account_alias=alias,
            active_contract=active_contract,
            quantity=quantity,
            current_equity=current_account.equity,
            bid=float(supervision.quote_bid),
            ask=float(supervision.quote_ask),
            quote_timestamp_ms=int(supervision.quote_timestamp_ms),
            now_ms=now_ms,
        )
        return Phase28AccountRevalidation(
            account_alias=alias,
            status=Phase28AccountStatus.READY_FOR_SUBMISSION_ADAPTER_HANDOFF,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            selected_root=selected_root,
            active_contract=active_contract,
            contract_quantity=quantity,
            phase27_authorization_fingerprint=fingerprint,
            pre_submit_snapshot_fingerprint=snapshot_fingerprint,
            current_equity=current_account.equity,
            projected_cash_risk=risk_result["projected_cash_risk"],
            projected_equity_risk_fraction=risk_result["projected_equity_risk_fraction"],
            quote_bid=supervision.quote_bid,
            quote_ask=supervision.quote_ask,
            quote_timestamp_ms=supervision.quote_timestamp_ms,
            spread_ticks=market_result["spread_ticks"],
            adverse_entry_deviation_ticks=market_result[
                "adverse_entry_deviation_ticks"
            ],
            pre_submit_ready=True,
            order_authorized=True,
            reason_codes=(
                "PHASE27_AUTHORIZATION_REVERIFIED",
                "ACTIVE_CONTRACT_REVERIFIED",
                "CURRENT_ACCOUNT_AND_QUOTE_SUPERVISION_HEALTHY",
                "CURRENT_EQUITY_RISK_REVALIDATED",
                "SPREAD_AND_ADVERSE_ENTRY_DEVIATION_WITHIN_EXPLICIT_LIMITS",
                "READY_FOR_FUTURE_SUBMISSION_ADAPTER_HANDOFF",
                "PHASE28_DOES_NOT_CONSUME_OR_SUBMIT_THE_AUTHORIZATION",
            ),
        )

    @staticmethod
    def _risk_revalidation(
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        quantity: int,
        current_equity: float,
        tick_size: float,
        tick_value: float | None,
        broker_max_volume: float,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        phase26 = account.get("phase26_account_plan") or {}
        policy_state = phase26.get("policy_state") or {}
        risk_fraction_raw = policy_state.get("risk_fraction")
        max_risk_cash_raw = policy_state.get("max_risk_cash")
        max_contracts = LondresPhase28PreSubmitRevalidationEngine._positive_integer(
            phase26.get("max_contracts")
        )

        try:
            risk_fraction = float(risk_fraction_raw)
        except (TypeError, ValueError):
            risk_fraction = math.nan
        if not any(
            math.isclose(risk_fraction, allowed, rel_tol=0.0, abs_tol=1e-12)
            for allowed in ALLOWED_RISK_FRACTIONS
        ):
            reasons.append("VALID_PHASE26_ACCOUNT_RISK_FRACTION_REQUIRED")

        if current_equity <= 0 or not math.isfinite(current_equity):
            reasons.append("POSITIVE_CURRENT_BROKER_EQUITY_REQUIRED")
        if tick_size <= 0 or not math.isfinite(tick_size):
            reasons.append("VALID_CURRENT_TICK_SIZE_REQUIRED")
        if tick_value is None or tick_value <= 0 or not math.isfinite(tick_value):
            reasons.append("VALID_CURRENT_TICK_VALUE_REQUIRED")
        if max_contracts is None or quantity > max_contracts:
            reasons.append("QUANTITY_EXCEEDS_PHASE26_ACCOUNT_MAX_CONTRACTS")
        if quantity > broker_max_volume + 1e-12:
            reasons.append("QUANTITY_EXCEEDS_CURRENT_BROKER_MAX_VOLUME")

        if reasons:
            return {
                "projected_cash_risk": None,
                "projected_equity_risk_fraction": None,
                "reasons": reasons,
            }

        stop_distance = abs(intent.entry_price - intent.stop_price)
        risk_per_contract = (stop_distance / tick_size) * float(tick_value)
        projected_cash_risk = risk_per_contract * quantity
        projected_fraction = projected_cash_risk / current_equity
        if projected_fraction > risk_fraction + 1e-12:
            reasons.append("CURRENT_EQUITY_RISK_EXCEEDS_PHASE26_ACCOUNT_RISK_FRACTION")
        if projected_fraction > MAX_ACCOUNT_RISK_FRACTION + 1e-12:
            reasons.append("CURRENT_EQUITY_RISK_EXCEEDS_LONDRES_HARD_MAXIMUM")

        if max_risk_cash_raw is not None:
            try:
                max_risk_cash = float(max_risk_cash_raw)
            except (TypeError, ValueError):
                max_risk_cash = math.nan
            if not math.isfinite(max_risk_cash) or max_risk_cash <= 0:
                reasons.append("VALID_PHASE26_MAX_RISK_CASH_REQUIRED")
            elif projected_cash_risk > max_risk_cash + 1e-9:
                reasons.append("CURRENT_PROJECTED_CASH_RISK_EXCEEDS_PHASE26_CAP")

        return {
            "projected_cash_risk": projected_cash_risk,
            "projected_equity_risk_fraction": projected_fraction,
            "reasons": reasons,
        }

    @staticmethod
    def _market_revalidation(
        *,
        intent: TradeIntent,
        bid: float | None,
        ask: float | None,
        tick_size: float,
        policy: Phase28PreSubmitPolicy,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if bid is None or ask is None:
            return {
                "spread_ticks": None,
                "adverse_entry_deviation_ticks": None,
                "reasons": ["COMPLETE_CURRENT_BID_ASK_REQUIRED"],
            }
        bid_value = float(bid)
        ask_value = float(ask)
        if (
            not math.isfinite(bid_value)
            or not math.isfinite(ask_value)
            or ask_value < bid_value
        ):
            return {
                "spread_ticks": None,
                "adverse_entry_deviation_ticks": None,
                "reasons": ["VALID_NON_CROSSED_CURRENT_BID_ASK_REQUIRED"],
            }

        spread_ticks = (ask_value - bid_value) / tick_size
        if spread_ticks > policy.max_spread_ticks + 1e-12:
            reasons.append("CURRENT_SPREAD_EXCEEDS_EXPLICIT_PHASE28_LIMIT")

        executable_quote = ask_value if intent.direction == "BULLISH" else bid_value
        if intent.direction == "BULLISH":
            adverse = max(0.0, executable_quote - intent.entry_price) / tick_size
            valid_geometry = intent.stop_price < executable_quote < intent.target_price
        else:
            adverse = max(0.0, intent.entry_price - executable_quote) / tick_size
            valid_geometry = intent.target_price < executable_quote < intent.stop_price
        if adverse > policy.max_adverse_entry_deviation_ticks + 1e-12:
            reasons.append("CURRENT_QUOTE_EXCEEDS_EXPLICIT_ADVERSE_ENTRY_DEVIATION_LIMIT")
        if not valid_geometry:
            reasons.append("CURRENT_EXECUTABLE_QUOTE_ALREADY_OUTSIDE_TRADE_GEOMETRY")

        return {
            "spread_ticks": spread_ticks,
            "adverse_entry_deviation_ticks": adverse,
            "reasons": reasons,
        }

    @staticmethod
    def _blocked(
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        status: Phase28AccountStatus,
        reasons: tuple[str, ...],
        current_equity: float | None = None,
        projected_cash_risk: float | None = None,
        projected_equity_risk_fraction: float | None = None,
        quote_bid: float | None = None,
        quote_ask: float | None = None,
        quote_timestamp_ms: int | None = None,
        spread_ticks: float | None = None,
        adverse_entry_deviation_ticks: float | None = None,
    ) -> Phase28AccountRevalidation:
        fingerprint = str(account.get("authorization_fingerprint") or "") or None
        quantity = LondresPhase28PreSubmitRevalidationEngine._positive_integer(
            account.get("contract_quantity")
        )
        return Phase28AccountRevalidation(
            account_alias=str(account.get("account_alias") or ""),
            status=status,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            selected_root=account.get("selected_root"),
            active_contract=account.get("active_contract"),
            contract_quantity=quantity,
            phase27_authorization_fingerprint=fingerprint,
            pre_submit_snapshot_fingerprint=None,
            current_equity=current_equity,
            projected_cash_risk=projected_cash_risk,
            projected_equity_risk_fraction=projected_equity_risk_fraction,
            quote_bid=quote_bid,
            quote_ask=quote_ask,
            quote_timestamp_ms=quote_timestamp_ms,
            spread_ticks=spread_ticks,
            adverse_entry_deviation_ticks=adverse_entry_deviation_ticks,
            pre_submit_ready=False,
            order_authorized=False,
            reason_codes=reasons,
        )

    @staticmethod
    def _valid_fingerprint(value: str) -> bool:
        return len(value) == 64 and all(character in "0123456789abcdef" for character in value)

    @staticmethod
    def _positive_integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        rounded = round(number)
        if not math.isclose(number, rounded, rel_tol=0.0, abs_tol=1e-12):
            return None
        return int(rounded)

    @staticmethod
    def _same_price(value: Any, expected: float) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and math.isclose(
            number,
            float(expected),
            rel_tol=0.0,
            abs_tol=1e-9,
        )

    @staticmethod
    def _snapshot_fingerprint(
        *,
        phase27_fingerprint: str,
        account_alias: str,
        active_contract: str,
        quantity: int,
        current_equity: float,
        bid: float,
        ask: float,
        quote_timestamp_ms: int,
        now_ms: int,
    ) -> str:
        payload = {
            "phase27_authorization_fingerprint": phase27_fingerprint,
            "account_alias": account_alias,
            "active_contract": active_contract,
            "contract_quantity": quantity,
            "current_equity": current_equity,
            "bid": bid,
            "ask": ask,
            "quote_timestamp_ms": quote_timestamp_ms,
            "revalidated_at_ms": now_ms,
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        plans: tuple[Phase28AccountRevalidation, ...],
        policy: OrchestrationPolicy,
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase28RevalidationBatch:
        skipped = sum(
            item.status is Phase28AccountStatus.SKIPPED_DISABLED
            for item in plans
        )
        enabled = len(plans) - skipped
        ready_count = sum(item.pre_submit_ready for item in plans)
        blocked = enabled - ready_count

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_LIVE_ACCOUNTS_FOR_PHASE28",)
        elif not global_ready:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = global_reasons or ("PHASE27_BATCH_NOT_READY_FOR_PHASE28",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE and blocked:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_PASS_PHASE28",)
            plans = tuple(
                LondresPhase28PreSubmitRevalidationEngine._revoke_for_batch_policy(item)
                if item.pre_submit_ready
                else item
                for item in plans
            )
            ready_count = 0
            blocked = enabled
        elif ready_count == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = ("ALL_ENABLED_LIVE_ACCOUNTS_PASSED_PHASE28",)
        elif ready_count > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = ("BEST_EFFORT_ISOLATES_ACCOUNTS_BLOCKED_BY_PHASE28",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("NO_LIVE_ACCOUNT_PASSED_PHASE28_PRE_SUBMIT_REVALIDATION",)

        return Phase28RevalidationBatch(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            policy=policy,
            status=status,
            accounts=plans,
            enabled_accounts=enabled,
            ready_accounts=ready_count,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            pre_submit_ready=batch_ready,
            order_authorized=batch_ready,
            reason_codes=(*reasons, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE28"),
        )

    @staticmethod
    def _revoke_for_batch_policy(
        account: Phase28AccountRevalidation,
    ) -> Phase28AccountRevalidation:
        return replace(
            account,
            status=Phase28AccountStatus.BLOCKED_BATCH_POLICY,
            pre_submit_ready=False,
            order_authorized=False,
            pre_submit_snapshot_fingerprint=None,
            reason_codes=(
                "ACCOUNT_PASSED_PHASE28_BUT_ALL_OR_NONE_BATCH_POLICY_BLOCKED_HANDOFF",
            ),
        )
