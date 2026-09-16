"""Phase 31: universal immediate pre-submit revalidation firewall.

Phase 31 consumes only Phase 30-authorized account envelopes. Immediately before
a future execution adapter could receive one, it re-reads the current broker
account, instrument, quote, risk metadata and exact volume grid. NinjaTrader also
re-verifies the active futures contract/rollover binding.

The engine never resizes an authorized trade and deliberately exposes no broker
order-submission, modification, cancellation, close or flatten API.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import (
    BrokerAdapter,
    BrokerType,
    OrchestrationPolicy,
    TradeIntent,
    canonicalize_symbol,
)
from tradingagents.brokers.supervision import (
    BrokerConnectionSupervisor,
    BrokerSupervisionPolicy,
)

from .multi_account import MultiAccountBatchStatus
from .phase30 import Phase30BrokerVenue
from .risk_sizing import ALLOWED_RISK_FRACTIONS, MAX_ACCOUNT_RISK_FRACTION


class Phase31AccountStatus(str, Enum):
    READY_FOR_EXECUTION_ADAPTER_HANDOFF = "READY_FOR_EXECUTION_ADAPTER_HANDOFF"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_PHASE30 = "BLOCKED_PHASE30"
    BLOCKED_BINDING = "BLOCKED_BINDING"
    BLOCKED_DUPLICATE_AUTHORIZATION = "BLOCKED_DUPLICATE_AUTHORIZATION"
    BLOCKED_BROKER_IDENTITY = "BLOCKED_BROKER_IDENTITY"
    BLOCKED_CONTRACT_REVALIDATION = "BLOCKED_CONTRACT_REVALIDATION"
    BLOCKED_SUPERVISION = "BLOCKED_SUPERVISION"
    BLOCKED_INSTRUMENT_REVALIDATION = "BLOCKED_INSTRUMENT_REVALIDATION"
    BLOCKED_VOLUME_GRID = "BLOCKED_VOLUME_GRID"
    BLOCKED_MARKET_REVALIDATION = "BLOCKED_MARKET_REVALIDATION"
    BLOCKED_RISK_REVALIDATION = "BLOCKED_RISK_REVALIDATION"
    BLOCKED_BATCH_POLICY = "BLOCKED_BATCH_POLICY"


@dataclass(frozen=True)
class Phase31PreSubmitPolicy:
    """Explicit market-quality limits for the universal pre-submit firewall."""

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
class Phase31BrokerBinding:
    """Private adapter binding used only for immediate read-only revalidation."""

    account_alias: str
    adapter: BrokerAdapter
    max_risk_cash: float | None = None

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if self.max_risk_cash is not None:
            value = float(self.max_risk_cash)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("max_risk_cash must be > 0 when supplied")


@dataclass(frozen=True)
class Phase31AccountRevalidation:
    account_alias: str
    venue: Phase30BrokerVenue
    broker_type: BrokerType
    status: Phase31AccountStatus
    trade_id: str
    canonical_symbol: str
    direction: str
    broker_symbol: str | None
    prepared_volume: float | None
    volume_unit: str | None
    selected_risk_fraction: float | None
    phase30_authorization_fingerprint: str | None
    pre_submit_snapshot_fingerprint: str | None
    current_equity: float | None
    current_executable_price: float | None
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    quote_bid: float | None
    quote_ask: float | None
    quote_timestamp_ms: int | None
    tick_size: float | None
    tick_value_account_currency: float | None
    tick_value_timestamp_ms: int | None
    spread_ticks: float | None
    adverse_entry_deviation_ticks: float | None
    pre_submit_ready: bool
    order_authorized: bool
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
        payload["fingerprint_consumed"] = False
        payload["authorized_volume_resized"] = False
        return payload


@dataclass(frozen=True)
class Phase31RevalidationBatch:
    trade_id: str
    canonical_symbol: str
    direction: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[Phase31AccountRevalidation, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    pre_submit_ready: bool
    order_authorized: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": "LONDRES_PHASE31_UNIVERSAL_PRE_SUBMIT_FIREWALL",
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
            "authorization_authority": "LONDRES_PHASE31_UNIVERSAL_PRE_SUBMIT_FIREWALL",
            "position_policy": "EXACT_PHASE30_VOLUME_NO_SILENT_RESIZING",
            "account_scope": "LIVE_BROKERAGE_ACCOUNTS_ONLY",
            "account_environment": "HIDDEN_INTERNAL",
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "fingerprint_registry_mutated": False,
            "reason_codes": list(self.reason_codes),
        }


class LondresPhase31UniversalPreSubmitFirewallEngine:
    """Revalidate Phase 30 envelopes against current read-only broker state."""

    def __init__(self) -> None:
        self._supervisor = BrokerConnectionSupervisor()

    def revalidate(
        self,
        *,
        intent: TradeIntent,
        phase30_plan: dict[str, Any],
        bindings: Iterable[Phase31BrokerBinding],
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        pre_submit_policy: Phase31PreSubmitPolicy,
        used_authorization_fingerprints: Iterable[str],
    ) -> dict[str, Any]:
        binding_items = tuple(bindings)
        aliases = [binding.account_alias for binding in binding_items]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Phase 31 broker bindings require unique account aliases")
        binding_map = {binding.account_alias: binding for binding in binding_items}

        policy, global_reasons = self._phase30_contract(
            intent=intent,
            plan=phase30_plan,
        )
        global_ready = policy is not None and not global_reasons
        effective_policy = policy or OrchestrationPolicy.BEST_EFFORT
        used = {str(value).lower() for value in used_authorization_fingerprints}
        seen: set[str] = set()

        plans: list[Phase31AccountRevalidation] = []
        for account in phase30_plan.get("accounts") or []:
            result = self._revalidate_account(
                intent=intent,
                account=account,
                binding=binding_map.get(str(account.get("account_alias") or "")),
                now_ms=now_ms,
                supervision_policy=supervision_policy,
                pre_submit_policy=pre_submit_policy,
                used_fingerprints=used,
                fingerprints_seen_in_batch=seen,
                global_ready=global_ready,
                global_reasons=global_reasons,
            )
            plans.append(result)
            fingerprint = result.phase30_authorization_fingerprint
            if fingerprint and self._valid_fingerprint(fingerprint):
                seen.add(fingerprint.lower())

        return self._batch(
            intent=intent,
            plans=tuple(plans),
            policy=effective_policy,
            global_ready=global_ready,
            global_reasons=global_reasons,
        ).to_dict()

    @staticmethod
    def state_update(context: dict[str, Any]) -> dict[str, Any]:
        return {"universal_pre_submit_firewall_state": context}

    @staticmethod
    def _phase30_contract(
        *,
        intent: TradeIntent,
        plan: dict[str, Any],
    ) -> tuple[OrchestrationPolicy | None, tuple[str, ...]]:
        reasons: list[str] = []
        try:
            policy = OrchestrationPolicy(str(plan.get("policy")))
        except ValueError:
            policy = None
            reasons.append("VALID_PHASE30_ORCHESTRATION_POLICY_REQUIRED")

        if not str(plan.get("phase") or "").startswith("LONDRES_PHASE30"):
            reasons.append("PHASE30_PLAN_PHASE_MARKER_REQUIRED")
        if plan.get("trade_id") != intent.trade_id:
            reasons.append("PHASE30_TRADE_ID_DOES_NOT_MATCH_INTENT")
        if canonicalize_symbol(str(plan.get("canonical_symbol") or "")) != intent.canonical:
            reasons.append("PHASE30_CANONICAL_SYMBOL_DOES_NOT_MATCH_INTENT")
        if str(plan.get("direction") or "") != intent.direction:
            reasons.append("PHASE30_DIRECTION_DOES_NOT_MATCH_INTENT")
        if intent.execution_style != "MARKET_ON_SIGNAL":
            reasons.append("PHASE31_CURRENTLY_REQUIRES_MARKET_ON_SIGNAL_EXECUTION_STYLE")
        if plan.get("execution_handoff_ready") is not True:
            reasons.append("PHASE30_EXECUTION_HANDOFF_NOT_READY")
        if plan.get("order_authorized") is not True:
            reasons.append("PHASE30_ORDER_AUTHORIZATION_REQUIRED")
        if plan.get("order_submission_enabled") is not False:
            reasons.append("PHASE30_MUST_NOT_ENABLE_ORDER_SUBMISSION")
        if not isinstance(plan.get("accounts"), list):
            reasons.append("PHASE30_ACCOUNT_LIST_REQUIRED")
        return policy, tuple(reasons)

    def _revalidate_account(
        self,
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        binding: Phase31BrokerBinding | None,
        now_ms: int,
        supervision_policy: BrokerSupervisionPolicy,
        pre_submit_policy: Phase31PreSubmitPolicy,
        used_fingerprints: set[str],
        fingerprints_seen_in_batch: set[str],
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase31AccountRevalidation:
        venue = self._venue(account.get("venue"))
        broker_type = self._broker_type(account.get("broker_type"))
        effective_venue = venue or Phase30BrokerVenue.FP_MARKETS_CTRADER
        effective_type = broker_type or BrokerType.CUSTOM

        if account.get("status") == "SKIPPED_DISABLED":
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.SKIPPED_DISABLED,
                reasons=("ACCOUNT_DISABLED_BEFORE_PHASE31",),
            )
        if (
            account.get("status") != "AUTHORIZED_FOR_UNIVERSAL_HANDOFF"
            or account.get("execution_handoff_ready") is not True
            or account.get("order_authorized") is not True
        ):
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.BLOCKED_PHASE30,
                reasons=("PHASE30_ACCOUNT_AUTHORIZATION_REQUIRED",),
            )
        if not global_ready:
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.BLOCKED_PHASE30,
                reasons=global_reasons or ("PHASE30_BATCH_NOT_READY",),
            )

        fingerprint = str(account.get("authorization_fingerprint") or "").lower()
        if not self._valid_fingerprint(fingerprint):
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.BLOCKED_PHASE30,
                reasons=("VALID_PHASE30_SHA256_AUTHORIZATION_FINGERPRINT_REQUIRED",),
            )
        if fingerprint in used_fingerprints or fingerprint in fingerprints_seen_in_batch:
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.BLOCKED_DUPLICATE_AUTHORIZATION,
                reasons=("PHASE30_AUTHORIZATION_FINGERPRINT_ALREADY_USED_OR_DUPLICATED",),
            )

        if not self._account_matches_intent(intent=intent, account=account):
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.BLOCKED_PHASE30,
                reasons=("PHASE30_ACCOUNT_ENVELOPE_DOES_NOT_MATCH_TRADE_INTENT",),
            )

        broker_symbol = str(account.get("broker_symbol") or "").strip()
        prepared_volume = self._positive_number(account.get("prepared_volume"))
        volume_unit = str(account.get("volume_unit") or "").strip().lower()
        risk_fraction = self._risk_fraction(account.get("selected_risk_fraction"))
        expected_type = self._expected_broker_type(venue)
        if (
            venue is None
            or broker_type is None
            or expected_type is None
            or broker_type is not expected_type
            or not broker_symbol
            or prepared_volume is None
            or volume_unit not in {"contracts", "units", "lots"}
            or risk_fraction is None
        ):
            return self._blocked(
                intent=intent,
                account=account,
                venue=effective_venue,
                broker_type=effective_type,
                status=Phase31AccountStatus.BLOCKED_PHASE30,
                reasons=("PHASE30_EXACT_BROKER_VOLUME_AND_RISK_ENVELOPE_REQUIRED",),
            )

        if binding is None or binding.account_alias != str(account.get("account_alias") or ""):
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_BINDING,
                reasons=("EXACT_PHASE31_ACCOUNT_ADAPTER_BINDING_REQUIRED",),
            )
        adapter = binding.adapter
        try:
            capabilities = adapter.capabilities()
        except Exception:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_BINDING,
                reasons=("PHASE31_ADAPTER_CAPABILITIES_UNAVAILABLE",),
            )
        if adapter.broker_type is not broker_type or capabilities.execution_enabled:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_BINDING,
                reasons=("READ_ONLY_ADAPTER_TYPE_MUST_MATCH_PHASE30_BROKER_TYPE",),
            )

        rollover_reasons = self._revalidate_rollover(
            venue=venue,
            account=account,
            adapter=adapter,
            broker_symbol=broker_symbol,
        )
        if rollover_reasons:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_CONTRACT_REVALIDATION,
                reasons=rollover_reasons,
            )

        supervision = self._supervisor.check(
            adapter=adapter,
            account_alias=binding.account_alias,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            now_ms=now_ms,
            policy=supervision_policy,
        )
        if not supervision.execution_data_ready:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_SUPERVISION,
                reasons=(
                    "IMMEDIATE_UNIVERSAL_BROKER_SUPERVISION_FAILED",
                    *supervision.reason_codes,
                ),
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
            )

        try:
            current_account = adapter.account_snapshot(binding.account_alias)
            instrument = adapter.instrument_snapshot(
                account_alias=binding.account_alias,
                canonical_symbol=intent.canonical,
                broker_symbol=broker_symbol,
            )
        except Exception:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_INSTRUMENT_REVALIDATION,
                reasons=("CURRENT_ACCOUNT_OR_INSTRUMENT_REVALIDATION_FAILED",),
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
            )

        identity_reasons = self._identity_revalidation(
            venue=venue,
            account=account,
            current_broker_name=current_account.broker_name,
        )
        if identity_reasons:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_BROKER_IDENTITY,
                reasons=identity_reasons,
                current_equity=current_account.equity,
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
            )

        instrument_reasons = self._instrument_revalidation(
            intent=intent,
            current_account_currency=current_account.currency,
            broker_symbol=broker_symbol,
            instrument=instrument,
        )
        if instrument_reasons:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_INSTRUMENT_REVALIDATION,
                reasons=instrument_reasons,
                current_equity=current_account.equity,
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
                tick_size=instrument.tick_size,
                tick_value_account_currency=instrument.tick_value_account_currency,
                tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
            )

        volume_reasons = self._volume_grid_revalidation(
            volume=prepared_volume,
            authorized_unit=volume_unit,
            current_unit=instrument.volume_unit,
            minimum=instrument.min_volume,
            maximum=instrument.max_volume,
            step=instrument.volume_step,
        )
        if volume_reasons:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_VOLUME_GRID,
                reasons=volume_reasons,
                current_equity=current_account.equity,
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
                tick_size=instrument.tick_size,
                tick_value_account_currency=instrument.tick_value_account_currency,
                tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
            )

        market = self._market_revalidation(
            intent=intent,
            bid=supervision.quote_bid,
            ask=supervision.quote_ask,
            tick_size=instrument.tick_size,
            policy=pre_submit_policy,
        )
        if market["reasons"]:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_MARKET_REVALIDATION,
                reasons=tuple(market["reasons"]),
                current_equity=current_account.equity,
                current_executable_price=market["current_executable_price"],
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
                tick_size=instrument.tick_size,
                tick_value_account_currency=instrument.tick_value_account_currency,
                tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
                spread_ticks=market["spread_ticks"],
                adverse_entry_deviation_ticks=market[
                    "adverse_entry_deviation_ticks"
                ],
            )

        authorized_cap = self._authorized_max_risk_cash(account)
        effective_cash_cap = self._effective_cash_cap(
            authorized_cap=authorized_cap,
            binding_cap=binding.max_risk_cash,
        )
        risk = self._risk_revalidation(
            current_executable_price=float(market["current_executable_price"]),
            stop_price=intent.stop_price,
            current_equity=current_account.equity,
            selected_risk_fraction=risk_fraction,
            prepared_volume=prepared_volume,
            tick_size=instrument.tick_size,
            tick_value=float(instrument.tick_value_account_currency),
            max_risk_cash=effective_cash_cap,
        )
        if risk["reasons"]:
            return self._blocked(
                intent=intent,
                account=account,
                venue=venue,
                broker_type=broker_type,
                status=Phase31AccountStatus.BLOCKED_RISK_REVALIDATION,
                reasons=tuple(risk["reasons"]),
                current_equity=current_account.equity,
                current_executable_price=market["current_executable_price"],
                projected_cash_risk=risk["projected_cash_risk"],
                projected_equity_risk_fraction=risk[
                    "projected_equity_risk_fraction"
                ],
                quote_bid=supervision.quote_bid,
                quote_ask=supervision.quote_ask,
                quote_timestamp_ms=supervision.quote_timestamp_ms,
                tick_size=instrument.tick_size,
                tick_value_account_currency=instrument.tick_value_account_currency,
                tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
                spread_ticks=market["spread_ticks"],
                adverse_entry_deviation_ticks=market[
                    "adverse_entry_deviation_ticks"
                ],
            )

        snapshot_fingerprint = self._snapshot_fingerprint(
            phase30_fingerprint=fingerprint,
            adapter_id=adapter.adapter_id,
            account_alias=binding.account_alias,
            venue=venue,
            broker_type=broker_type,
            broker_symbol=broker_symbol,
            prepared_volume=prepared_volume,
            volume_unit=volume_unit,
            current_equity=current_account.equity,
            bid=float(supervision.quote_bid),
            ask=float(supervision.quote_ask),
            quote_timestamp_ms=int(supervision.quote_timestamp_ms),
            tick_size=instrument.tick_size,
            tick_value=float(instrument.tick_value_account_currency),
            tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
            now_ms=now_ms,
        )
        return Phase31AccountRevalidation(
            account_alias=binding.account_alias,
            venue=venue,
            broker_type=broker_type,
            status=Phase31AccountStatus.READY_FOR_EXECUTION_ADAPTER_HANDOFF,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            broker_symbol=broker_symbol,
            prepared_volume=prepared_volume,
            volume_unit=volume_unit,
            selected_risk_fraction=risk_fraction,
            phase30_authorization_fingerprint=fingerprint,
            pre_submit_snapshot_fingerprint=snapshot_fingerprint,
            current_equity=current_account.equity,
            current_executable_price=market["current_executable_price"],
            projected_cash_risk=risk["projected_cash_risk"],
            projected_equity_risk_fraction=risk[
                "projected_equity_risk_fraction"
            ],
            quote_bid=supervision.quote_bid,
            quote_ask=supervision.quote_ask,
            quote_timestamp_ms=supervision.quote_timestamp_ms,
            tick_size=instrument.tick_size,
            tick_value_account_currency=instrument.tick_value_account_currency,
            tick_value_timestamp_ms=instrument.tick_value_timestamp_ms,
            spread_ticks=market["spread_ticks"],
            adverse_entry_deviation_ticks=market[
                "adverse_entry_deviation_ticks"
            ],
            pre_submit_ready=True,
            order_authorized=True,
            reason_codes=(
                "PHASE30_AUTHORIZATION_REVERIFIED",
                "CURRENT_BROKER_IDENTITY_AND_SYMBOL_REVERIFIED",
                "CURRENT_QUOTE_AND_TICK_VALUE_SUPERVISION_HEALTHY",
                "EXACT_PHASE30_VOLUME_STILL_EXECUTABLE_ON_CURRENT_GRID",
                "CURRENT_EXECUTABLE_PRICE_WITHIN_EXPLICIT_MARKET_LIMITS",
                "CURRENT_EQUITY_RISK_REVALIDATED_WITHOUT_RESIZING",
                "READY_FOR_FUTURE_EXECUTION_ADAPTER_HANDOFF",
                "PHASE31_DOES_NOT_CONSUME_OR_SUBMIT_THE_AUTHORIZATION",
            ),
        )

    @staticmethod
    def _account_matches_intent(
        *,
        intent: TradeIntent,
        account: dict[str, Any],
    ) -> bool:
        return bool(
            account.get("trade_id") == intent.trade_id
            and canonicalize_symbol(str(account.get("canonical_symbol") or ""))
            == intent.canonical
            and str(account.get("direction") or "") == intent.direction
            and LondresPhase31UniversalPreSubmitFirewallEngine._same_price(
                account.get("entry_price"), intent.entry_price
            )
            and LondresPhase31UniversalPreSubmitFirewallEngine._same_price(
                account.get("stop_price"), intent.stop_price
            )
            and LondresPhase31UniversalPreSubmitFirewallEngine._same_price(
                account.get("target_price"), intent.target_price
            )
            and str(account.get("selected_exit_mode") or "")
            == intent.selected_exit_mode
        )

    @staticmethod
    def _identity_revalidation(
        *,
        venue: Phase30BrokerVenue,
        account: dict[str, Any],
        current_broker_name: str | None,
    ) -> tuple[str, ...]:
        if venue is Phase30BrokerVenue.NINJATRADER:
            return ()
        authorized = LondresPhase31UniversalPreSubmitFirewallEngine._normalized_provider(
            account.get("broker_name")
        )
        current = LondresPhase31UniversalPreSubmitFirewallEngine._normalized_provider(
            current_broker_name
        )
        reasons: list[str] = []
        if not LondresPhase31UniversalPreSubmitFirewallEngine._provider_matches(
            venue, current_broker_name
        ):
            reasons.append("CURRENT_BROKER_IDENTITY_DOES_NOT_MATCH_PHASE30_VENUE")
        if not authorized or authorized != current:
            reasons.append("CURRENT_BROKER_IDENTITY_CHANGED_SINCE_PHASE30")
        return tuple(reasons)

    @staticmethod
    def _instrument_revalidation(
        *,
        intent: TradeIntent,
        current_account_currency: str,
        broker_symbol: str,
        instrument: Any,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if canonicalize_symbol(str(instrument.canonical_symbol or "")) != intent.canonical:
            reasons.append("CURRENT_INSTRUMENT_CANONICAL_SYMBOL_MISMATCH")
        if str(instrument.broker_symbol) != broker_symbol:
            reasons.append("CURRENT_INSTRUMENT_BROKER_SYMBOL_CHANGED")
        if instrument.metadata_verified is not True:
            reasons.append("CURRENT_INSTRUMENT_METADATA_MUST_BE_VERIFIED")
        if not math.isfinite(float(instrument.tick_size)) or instrument.tick_size <= 0:
            reasons.append("VALID_CURRENT_TICK_SIZE_REQUIRED")
        tick_value = instrument.tick_value_account_currency
        if tick_value is None or not math.isfinite(float(tick_value)) or tick_value <= 0:
            reasons.append("VALID_CURRENT_ACCOUNT_CURRENCY_TICK_VALUE_REQUIRED")
        tick_currency = str(instrument.tick_value_currency or "").strip().upper()
        account_currency = str(current_account_currency or "").strip().upper()
        if tick_currency and account_currency and tick_currency != account_currency:
            reasons.append("CURRENT_TICK_VALUE_CURRENCY_DOES_NOT_MATCH_ACCOUNT_CURRENCY")
        return tuple(reasons)

    @staticmethod
    def _volume_grid_revalidation(
        *,
        volume: float,
        authorized_unit: str,
        current_unit: str,
        minimum: float,
        maximum: float,
        step: float,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if str(current_unit).strip().lower() != authorized_unit:
            reasons.append("CURRENT_VOLUME_UNIT_DOES_NOT_MATCH_PHASE30")
        if not all(math.isfinite(float(value)) for value in (minimum, maximum, step)):
            reasons.append("CURRENT_BROKER_VOLUME_GRID_MUST_BE_FINITE")
            return tuple(reasons)
        if minimum <= 0 or maximum < minimum or step <= 0:
            reasons.append("CURRENT_BROKER_VOLUME_GRID_INVALID")
            return tuple(reasons)
        if volume < minimum - 1e-12 or volume > maximum + 1e-12:
            reasons.append("PHASE30_VOLUME_OUTSIDE_CURRENT_BROKER_MIN_MAX")
        grid_steps = (volume - minimum) / step
        if not math.isclose(grid_steps, round(grid_steps), rel_tol=0.0, abs_tol=1e-9):
            reasons.append("PHASE30_VOLUME_NO_LONGER_ALIGNS_WITH_CURRENT_BROKER_STEP")
        if authorized_unit == "contracts" and not math.isclose(
            volume, round(volume), rel_tol=0.0, abs_tol=1e-12
        ):
            reasons.append("NINJATRADER_CONTRACT_VOLUME_MUST_REMAIN_INTEGER")
        return tuple(reasons)

    @staticmethod
    def _market_revalidation(
        *,
        intent: TradeIntent,
        bid: float | None,
        ask: float | None,
        tick_size: float,
        policy: Phase31PreSubmitPolicy,
    ) -> dict[str, Any]:
        if bid is None or ask is None:
            return {
                "current_executable_price": None,
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
                "current_executable_price": None,
                "spread_ticks": None,
                "adverse_entry_deviation_ticks": None,
                "reasons": ["VALID_NON_CROSSED_CURRENT_BID_ASK_REQUIRED"],
            }

        reasons: list[str] = []
        spread_ticks = (ask_value - bid_value) / tick_size
        if spread_ticks > policy.max_spread_ticks + 1e-12:
            reasons.append("CURRENT_SPREAD_EXCEEDS_EXPLICIT_PHASE31_LIMIT")

        executable = ask_value if intent.direction == "BULLISH" else bid_value
        if intent.direction == "BULLISH":
            adverse = max(0.0, executable - intent.entry_price) / tick_size
            valid_geometry = intent.stop_price < executable < intent.target_price
        else:
            adverse = max(0.0, intent.entry_price - executable) / tick_size
            valid_geometry = intent.target_price < executable < intent.stop_price
        if adverse > policy.max_adverse_entry_deviation_ticks + 1e-12:
            reasons.append("CURRENT_QUOTE_EXCEEDS_EXPLICIT_PHASE31_ADVERSE_LIMIT")
        if not valid_geometry:
            reasons.append("CURRENT_EXECUTABLE_QUOTE_ALREADY_OUTSIDE_TRADE_GEOMETRY")

        return {
            "current_executable_price": executable,
            "spread_ticks": spread_ticks,
            "adverse_entry_deviation_ticks": adverse,
            "reasons": reasons,
        }

    @staticmethod
    def _risk_revalidation(
        *,
        current_executable_price: float,
        stop_price: float,
        current_equity: float,
        selected_risk_fraction: float,
        prepared_volume: float,
        tick_size: float,
        tick_value: float,
        max_risk_cash: float | None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not math.isfinite(current_equity) or current_equity <= 0:
            reasons.append("POSITIVE_CURRENT_BROKER_EQUITY_REQUIRED")
        if not math.isfinite(tick_size) or tick_size <= 0:
            reasons.append("VALID_CURRENT_TICK_SIZE_REQUIRED")
        if not math.isfinite(tick_value) or tick_value <= 0:
            reasons.append("VALID_CURRENT_TICK_VALUE_REQUIRED")
        if reasons:
            return {
                "projected_cash_risk": None,
                "projected_equity_risk_fraction": None,
                "reasons": reasons,
            }

        stop_distance = abs(current_executable_price - stop_price)
        projected_cash_risk = (
            (stop_distance / tick_size) * tick_value * prepared_volume
        )
        projected_fraction = projected_cash_risk / current_equity
        if projected_fraction > selected_risk_fraction + 1e-12:
            reasons.append("CURRENT_EQUITY_RISK_EXCEEDS_PHASE30_SELECTED_RISK_FRACTION")
        if projected_fraction > MAX_ACCOUNT_RISK_FRACTION + 1e-12:
            reasons.append("CURRENT_EQUITY_RISK_EXCEEDS_LONDRES_HARD_MAXIMUM")
        if max_risk_cash is not None and projected_cash_risk > max_risk_cash + 1e-9:
            reasons.append("CURRENT_PROJECTED_CASH_RISK_EXCEEDS_EXPLICIT_ACCOUNT_CAP")
        return {
            "projected_cash_risk": projected_cash_risk,
            "projected_equity_risk_fraction": projected_fraction,
            "reasons": reasons,
        }

    @staticmethod
    def _revalidate_rollover(
        *,
        venue: Phase30BrokerVenue,
        account: dict[str, Any],
        adapter: BrokerAdapter,
        broker_symbol: str,
    ) -> tuple[str, ...]:
        if venue is not Phase30BrokerVenue.NINJATRADER:
            return ()
        upstream = account.get("upstream_account_plan") or {}
        selected_root = str(upstream.get("selected_root") or "").strip().upper()
        resolver = getattr(adapter, "resolve_active_contract", None)
        if not selected_root or not callable(resolver):
            return ("NINJATRADER_ROOT_AND_ROLLOVER_RESOLVER_REQUIRED",)
        try:
            resolution = resolver(selected_root)
        except Exception:
            return ("CURRENT_NINJATRADER_ROLLOVER_REVALIDATION_FAILED",)
        if not bool(getattr(resolution, "ready", False)):
            return ("CURRENT_NINJATRADER_ROLLOVER_NOT_VERIFIED",)
        current_contract = str(getattr(resolution, "active_contract", "") or "").strip()
        if current_contract != broker_symbol:
            return ("NINJATRADER_ACTIVE_CONTRACT_CHANGED_SINCE_PHASE30",)
        return ()

    @staticmethod
    def _authorized_max_risk_cash(account: dict[str, Any]) -> float | None:
        if account.get("venue") != Phase30BrokerVenue.NINJATRADER.value:
            return None
        upstream = account.get("upstream_account_plan") or {}
        phase26 = upstream.get("phase26_account_plan") or {}
        policy_state = phase26.get("policy_state") or {}
        value = policy_state.get("max_risk_cash")
        if value is None:
            return None
        return LondresPhase31UniversalPreSubmitFirewallEngine._positive_number(value)

    @staticmethod
    def _effective_cash_cap(
        *,
        authorized_cap: float | None,
        binding_cap: float | None,
    ) -> float | None:
        caps = [value for value in (authorized_cap, binding_cap) if value is not None]
        return min(caps) if caps else None

    @staticmethod
    def _venue(value: Any) -> Phase30BrokerVenue | None:
        try:
            return Phase30BrokerVenue(str(value))
        except ValueError:
            return None

    @staticmethod
    def _broker_type(value: Any) -> BrokerType | None:
        try:
            return BrokerType(str(value))
        except ValueError:
            return None

    @staticmethod
    def _expected_broker_type(venue: Phase30BrokerVenue | None) -> BrokerType | None:
        if venue is Phase30BrokerVenue.NINJATRADER:
            return BrokerType.NINJATRADER
        if venue is Phase30BrokerVenue.FP_MARKETS_CTRADER:
            return BrokerType.CTRADER
        if venue is Phase30BrokerVenue.VANTAGE_MT5:
            return BrokerType.MT5
        return None

    @staticmethod
    def _risk_fraction(value: Any) -> float | None:
        number = LondresPhase31UniversalPreSubmitFirewallEngine._positive_number(value)
        if number is None:
            return None
        if not any(
            math.isclose(number, allowed, rel_tol=0.0, abs_tol=1e-12)
            for allowed in ALLOWED_RISK_FRACTIONS
        ):
            return None
        return number

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
    def _valid_fingerprint(value: str) -> bool:
        return len(value) == 64 and all(
            character in "0123456789abcdef" for character in value.lower()
        )

    @staticmethod
    def _normalized_provider(value: Any) -> str:
        return "".join(
            character
            for character in str(value or "").upper()
            if character.isalnum()
        )

    @staticmethod
    def _provider_matches(
        venue: Phase30BrokerVenue,
        broker_name: str | None,
    ) -> bool:
        normalized = LondresPhase31UniversalPreSubmitFirewallEngine._normalized_provider(
            broker_name
        )
        if venue is Phase30BrokerVenue.FP_MARKETS_CTRADER:
            return "FPMARKETS" in normalized
        if venue is Phase30BrokerVenue.VANTAGE_MT5:
            return "VANTAGE" in normalized
        return venue is Phase30BrokerVenue.NINJATRADER

    @staticmethod
    def _snapshot_fingerprint(
        *,
        phase30_fingerprint: str,
        adapter_id: str,
        account_alias: str,
        venue: Phase30BrokerVenue,
        broker_type: BrokerType,
        broker_symbol: str,
        prepared_volume: float,
        volume_unit: str,
        current_equity: float,
        bid: float,
        ask: float,
        quote_timestamp_ms: int,
        tick_size: float,
        tick_value: float,
        tick_value_timestamp_ms: int | None,
        now_ms: int,
    ) -> str:
        payload = {
            "phase30_authorization_fingerprint": phase30_fingerprint,
            "adapter_id": adapter_id,
            "account_alias": account_alias,
            "venue": venue.value,
            "broker_type": broker_type.value,
            "broker_symbol": broker_symbol,
            "prepared_volume": prepared_volume,
            "volume_unit": volume_unit,
            "current_equity": current_equity,
            "bid": bid,
            "ask": ask,
            "quote_timestamp_ms": quote_timestamp_ms,
            "tick_size": tick_size,
            "tick_value_account_currency": tick_value,
            "tick_value_timestamp_ms": tick_value_timestamp_ms,
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
    def _blocked(
        *,
        intent: TradeIntent,
        account: dict[str, Any],
        venue: Phase30BrokerVenue,
        broker_type: BrokerType,
        status: Phase31AccountStatus,
        reasons: tuple[str, ...],
        current_equity: float | None = None,
        current_executable_price: float | None = None,
        projected_cash_risk: float | None = None,
        projected_equity_risk_fraction: float | None = None,
        quote_bid: float | None = None,
        quote_ask: float | None = None,
        quote_timestamp_ms: int | None = None,
        tick_size: float | None = None,
        tick_value_account_currency: float | None = None,
        tick_value_timestamp_ms: int | None = None,
        spread_ticks: float | None = None,
        adverse_entry_deviation_ticks: float | None = None,
    ) -> Phase31AccountRevalidation:
        return Phase31AccountRevalidation(
            account_alias=str(account.get("account_alias") or ""),
            venue=venue,
            broker_type=broker_type,
            status=status,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            broker_symbol=str(account.get("broker_symbol") or "") or None,
            prepared_volume=LondresPhase31UniversalPreSubmitFirewallEngine._positive_number(
                account.get("prepared_volume")
            ),
            volume_unit=str(account.get("volume_unit") or "").strip().lower() or None,
            selected_risk_fraction=(
                LondresPhase31UniversalPreSubmitFirewallEngine._risk_fraction(
                    account.get("selected_risk_fraction")
                )
            ),
            phase30_authorization_fingerprint=(
                str(account.get("authorization_fingerprint") or "") or None
            ),
            pre_submit_snapshot_fingerprint=None,
            current_equity=current_equity,
            current_executable_price=current_executable_price,
            projected_cash_risk=projected_cash_risk,
            projected_equity_risk_fraction=projected_equity_risk_fraction,
            quote_bid=quote_bid,
            quote_ask=quote_ask,
            quote_timestamp_ms=quote_timestamp_ms,
            tick_size=tick_size,
            tick_value_account_currency=tick_value_account_currency,
            tick_value_timestamp_ms=tick_value_timestamp_ms,
            spread_ticks=spread_ticks,
            adverse_entry_deviation_ticks=adverse_entry_deviation_ticks,
            pre_submit_ready=False,
            order_authorized=False,
            reason_codes=reasons,
        )

    @staticmethod
    def _batch(
        *,
        intent: TradeIntent,
        plans: tuple[Phase31AccountRevalidation, ...],
        policy: OrchestrationPolicy,
        global_ready: bool,
        global_reasons: tuple[str, ...],
    ) -> Phase31RevalidationBatch:
        skipped = sum(
            item.status is Phase31AccountStatus.SKIPPED_DISABLED for item in plans
        )
        enabled = len(plans) - skipped
        ready = sum(item.pre_submit_ready for item in plans)
        blocked = enabled - ready

        if enabled == 0:
            status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_SUPPORTED_ACCOUNT_FOR_PHASE31",)
        elif not global_ready:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = global_reasons or ("PHASE30_BATCH_NOT_READY_FOR_PHASE31",)
        elif policy is OrchestrationPolicy.ALL_OR_NONE and blocked:
            plans = tuple(
                LondresPhase31UniversalPreSubmitFirewallEngine._revoke_for_batch_policy(
                    item
                )
                if item.pre_submit_ready
                else item
                for item in plans
            )
            ready = 0
            blocked = enabled
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("ALL_OR_NONE_REQUIRES_EVERY_ENABLED_PHASE31_ACCOUNT_READY",)
        elif ready == enabled:
            status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = ("ALL_ENABLED_BROKER_ACCOUNTS_PASSED_PHASE31",)
        elif ready > 0:
            status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = ("BEST_EFFORT_ISOLATES_ACCOUNTS_BLOCKED_BY_PHASE31",)
        else:
            status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = ("NO_SUPPORTED_ACCOUNT_PASSED_PHASE31_PRE_SUBMIT_FIREWALL",)

        return Phase31RevalidationBatch(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            direction=intent.direction,
            policy=policy,
            status=status,
            accounts=plans,
            enabled_accounts=enabled,
            ready_accounts=ready,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            pre_submit_ready=batch_ready,
            order_authorized=batch_ready,
            reason_codes=(*reasons, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE31"),
        )

    @staticmethod
    def _revoke_for_batch_policy(
        account: Phase31AccountRevalidation,
    ) -> Phase31AccountRevalidation:
        return replace(
            account,
            status=Phase31AccountStatus.BLOCKED_BATCH_POLICY,
            pre_submit_ready=False,
            order_authorized=False,
            pre_submit_snapshot_fingerprint=None,
            reason_codes=(
                "ACCOUNT_PASSED_PHASE31_BUT_ALL_OR_NONE_BATCH_POLICY_BLOCKED_HANDOFF",
            ),
        )
