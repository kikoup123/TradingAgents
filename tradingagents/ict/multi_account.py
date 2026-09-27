"""Broker-agnostic multi-account preparation for one validated Londres setup.

Phase 20 replicates *trade intent*, never a raw lot/contract quantity. Each
account is independently normalized and risk-sized from its own equity and
broker instrument contract. No broker order is submitted by this module.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tradingagents.brokers.contracts import (
    BrokerAdapter,
    BrokerInstrumentSpec,
    BrokerType,
    OrchestrationPolicy,
    TradeIntent,
)
from tradingagents.brokers.symbols import BrokerSymbolMap, SymbolMappingError

from .risk_sizing import (
    ALLOWED_RISK_FRACTIONS,
    AccountRiskPolicy,
    InstrumentRiskSpec,
    RiskSizingEngine,
    RiskSizingStatus,
)


class AccountPreparationStatus(str, Enum):
    READY_FOR_EXECUTION_ADAPTER = "READY_FOR_EXECUTION_ADAPTER"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    BLOCKED_NOT_CONNECTED = "BLOCKED_NOT_CONNECTED"
    BLOCKED_SYMBOL_MAPPING = "BLOCKED_SYMBOL_MAPPING"
    BLOCKED_CAPABILITY_MISMATCH = "BLOCKED_CAPABILITY_MISMATCH"
    BLOCKED_ACCOUNT_POLICY = "BLOCKED_ACCOUNT_POLICY"
    BLOCKED_INSTRUMENT_METADATA = "BLOCKED_INSTRUMENT_METADATA"
    WAIT_FOR_VERIFIED_TICK_VALUE = "WAIT_FOR_VERIFIED_TICK_VALUE"
    BLOCKED_RISK_SIZING = "BLOCKED_RISK_SIZING"
    BLOCKED_TARGET_GEOMETRY = "BLOCKED_TARGET_GEOMETRY"
    BLOCKED_HOLD_SPLIT = "BLOCKED_HOLD_SPLIT"
    BLOCKED_ADAPTER_ERROR = "BLOCKED_ADAPTER_ERROR"


class MultiAccountBatchStatus(str, Enum):
    READY = "READY"
    PARTIAL_READY = "PARTIAL_READY"
    BLOCKED = "BLOCKED"
    NO_ENABLED_ACCOUNTS = "NO_ENABLED_ACCOUNTS"


@dataclass(frozen=True)
class ManagedBrokerAccount:
    account_alias: str
    adapter: BrokerAdapter
    symbol_map: BrokerSymbolMap
    risk_fraction: float
    enabled: bool = True
    max_risk_cash: float | None = None

    def __post_init__(self) -> None:
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if not any(
            math.isclose(self.risk_fraction, allowed, abs_tol=1e-12)
            for allowed in ALLOWED_RISK_FRACTIONS
        ):
            raise ValueError("risk_fraction must be exactly 0.03, 0.05, or 0.10")
        if self.max_risk_cash is not None and self.max_risk_cash <= 0:
            raise ValueError("max_risk_cash must be > 0 when supplied")
        if self.symbol_map.broker_type != self.adapter.broker_type:
            raise ValueError("symbol map broker type must match adapter broker type")

    def public_dict(self) -> dict[str, Any]:
        return {
            "account_alias": self.account_alias,
            "broker_type": self.adapter.broker_type.value,
            "adapter_id": self.adapter.adapter_id,
            "enabled": self.enabled,
            "risk_fraction": self.risk_fraction,
            "max_risk_cash": self.max_risk_cash,
            "account_environment": "HIDDEN_INTERNAL",
            "symbol_map": self.symbol_map.public_dict(),
        }


@dataclass(frozen=True)
class AccountExecutionPlan:
    account_alias: str
    broker_type: BrokerType
    adapter_id: str
    status: AccountPreparationStatus
    trade_id: str
    canonical_symbol: str
    broker_symbol: str | None
    masked_account: str | None
    account_currency: str | None
    account_equity: float | None
    selected_risk_fraction: float
    intended_entry_price: float
    intended_stop_price: float
    selected_target_price: float
    selected_exit_mode: str
    prepared_volume: float | None
    volume_unit: str | None
    projected_cash_risk: float | None
    projected_equity_risk_fraction: float | None
    partial_volume: float | None
    runner_volume: float | None
    actual_fill_price: float | None
    break_even_price_source: str
    post_fill_risk_revalidation_required: bool
    preparation_ready: bool
    order_authorized: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["broker_type"] = self.broker_type.value
        payload["status"] = self.status.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


@dataclass(frozen=True)
class MultiAccountExecutionPlan:
    trade_id: str
    canonical_symbol: str
    policy: OrchestrationPolicy
    status: MultiAccountBatchStatus
    accounts: tuple[AccountExecutionPlan, ...]
    enabled_accounts: int
    ready_accounts: int
    blocked_accounts: int
    skipped_accounts: int
    batch_ready_for_future_execution: bool
    order_authorized: bool
    broker_order_placed: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "canonical_symbol": self.canonical_symbol,
            "policy": self.policy.value,
            "status": self.status.value,
            "accounts": [account.to_dict() for account in self.accounts],
            "enabled_accounts": self.enabled_accounts,
            "ready_accounts": self.ready_accounts,
            "blocked_accounts": self.blocked_accounts,
            "skipped_accounts": self.skipped_accounts,
            "batch_ready_for_future_execution": self.batch_ready_for_future_execution,
            "order_authorized": False,
            "broker_order_placed": False,
            "replication_authority": "LONDRES_MULTI_ACCOUNT_TRADE_INTENT_ORCHESTRATOR",
            "position_copy_mode": "INDEPENDENT_PER_ACCOUNT_RISK_SIZING",
            "account_environment": "HIDDEN_INTERNAL",
            "reason_codes": list(self.reason_codes),
        }


class MultiAccountExecutionManager:
    """Prepare one Londres trade for many accounts without sending any order."""

    def __init__(self) -> None:
        self._risk = RiskSizingEngine()

    def prepare(
        self,
        *,
        intent: TradeIntent,
        accounts: list[ManagedBrokerAccount] | tuple[ManagedBrokerAccount, ...],
        policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
    ) -> MultiAccountExecutionPlan:
        plans = tuple(self._prepare_account(intent=intent, account=account) for account in accounts)
        enabled = sum(plan.status != AccountPreparationStatus.SKIPPED_DISABLED for plan in plans)
        ready = sum(plan.preparation_ready for plan in plans)
        skipped = sum(plan.status == AccountPreparationStatus.SKIPPED_DISABLED for plan in plans)
        blocked = enabled - ready

        if enabled == 0:
            batch_status = MultiAccountBatchStatus.NO_ENABLED_ACCOUNTS
            batch_ready = False
            reasons = ("NO_ENABLED_ACCOUNTS_AVAILABLE_FOR_REPLICATION",)
        elif policy == OrchestrationPolicy.ALL_OR_NONE:
            batch_ready = ready == enabled
            batch_status = (
                MultiAccountBatchStatus.READY if batch_ready else MultiAccountBatchStatus.BLOCKED
            )
            reasons = (
                "ALL_OR_NONE_REQUIRES_EVERY_ENABLED_ACCOUNT_TO_BE_PREPARATION_READY",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE20",
            )
        elif ready == enabled:
            batch_status = MultiAccountBatchStatus.READY
            batch_ready = True
            reasons = (
                "ALL_ENABLED_ACCOUNTS_PREPARED_INDEPENDENTLY",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE20",
            )
        elif ready > 0:
            batch_status = MultiAccountBatchStatus.PARTIAL_READY
            batch_ready = True
            reasons = (
                "BEST_EFFORT_ISOLATES_BLOCKED_ACCOUNTS_FROM_READY_ACCOUNTS",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE20",
            )
        else:
            batch_status = MultiAccountBatchStatus.BLOCKED
            batch_ready = False
            reasons = (
                "NO_ENABLED_ACCOUNT_PASSED_PREPARATION",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE20",
            )

        return MultiAccountExecutionPlan(
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            policy=policy,
            status=batch_status,
            accounts=plans,
            enabled_accounts=enabled,
            ready_accounts=ready,
            blocked_accounts=blocked,
            skipped_accounts=skipped,
            batch_ready_for_future_execution=batch_ready,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=reasons,
        )

    def _prepare_account(
        self, *, intent: TradeIntent, account: ManagedBrokerAccount
    ) -> AccountExecutionPlan:
        if not account.enabled:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.SKIPPED_DISABLED,
                reason="ACCOUNT_DISABLED_BY_USER_CONFIGURATION",
            )

        try:
            broker_symbol = account.symbol_map.resolve(intent.canonical)
        except SymbolMappingError:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_SYMBOL_MAPPING,
                reason="EXPLICIT_BROKER_SYMBOL_MAPPING_REQUIRED_NO_GUESSING",
            )

        try:
            snapshot = account.adapter.account_snapshot(account.account_alias)
            capabilities = account.adapter.capabilities()
        except Exception:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_ADAPTER_ERROR,
                broker_symbol=broker_symbol,
                reason="BROKER_ADAPTER_ACCOUNT_READ_FAILED",
            )

        if not snapshot.connected:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_NOT_CONNECTED,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason="BROKER_ACCOUNT_NOT_CONNECTED",
            )

        capability_failure = self._capability_failure(intent=intent, capabilities=capabilities)
        if capability_failure is not None:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_CAPABILITY_MISMATCH,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason=capability_failure,
            )

        try:
            broker_instrument = account.adapter.instrument_snapshot(
                account_alias=account.account_alias,
                canonical_symbol=intent.canonical,
                broker_symbol=broker_symbol,
            )
        except Exception:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_INSTRUMENT_METADATA,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason="BROKER_INSTRUMENT_METADATA_UNAVAILABLE",
            )

        if broker_instrument.broker_symbol != broker_symbol:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_INSTRUMENT_METADATA,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason="BROKER_INSTRUMENT_SYMBOL_DOES_NOT_MATCH_EXPLICIT_MAPPING",
            )
        if not broker_instrument.risk_metadata_ready:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.WAIT_FOR_VERIFIED_TICK_VALUE,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason="VERIFIED_TICK_VALUE_IN_ACCOUNT_CURRENCY_REQUIRED",
            )

        risk_spec = self._to_risk_spec(broker_instrument)
        policy = AccountRiskPolicy(
            account_equity=snapshot.equity,
            risk_fraction=account.risk_fraction,
            max_risk_cash=account.max_risk_cash,
        )
        risk = self._risk.calculate(
            direction=intent.direction,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
            instrument=risk_spec,
            policy=policy,
        )
        if risk.status != RiskSizingStatus.READY or risk.final_volume is None:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_RISK_SIZING,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason=f"ACCOUNT_RISK_SIZING_NOT_READY_{risk.status.value}",
            )

        target_failure = self._target_failure(intent)
        if target_failure is not None:
            return self._blocked(
                intent=intent,
                account=account,
                status=AccountPreparationStatus.BLOCKED_TARGET_GEOMETRY,
                broker_symbol=broker_symbol,
                masked_account=snapshot.masked_account,
                currency=snapshot.currency,
                equity=snapshot.equity,
                reason=target_failure,
            )

        partial_volume = None
        runner_volume = None
        if intent.selected_exit_mode == "HOLD_HTF_LIQUIDITY":
            split = self._exact_split(
                total=float(risk.final_volume),
                instrument=broker_instrument,
                partial_fraction=float(intent.partial_fraction or 0.0),
                runner_fraction=float(intent.runner_fraction or 0.0),
            )
            if split is None:
                return self._blocked(
                    intent=intent,
                    account=account,
                    status=AccountPreparationStatus.BLOCKED_HOLD_SPLIT,
                    broker_symbol=broker_symbol,
                    masked_account=snapshot.masked_account,
                    currency=snapshot.currency,
                    equity=snapshot.equity,
                    reason="EXACT_60_40_SPLIT_NOT_EXECUTABLE_ON_ACCOUNT_VOLUME_GRID",
                )
            partial_volume, runner_volume = split

        return AccountExecutionPlan(
            account_alias=account.account_alias,
            broker_type=account.adapter.broker_type,
            adapter_id=account.adapter.adapter_id,
            status=AccountPreparationStatus.READY_FOR_EXECUTION_ADAPTER,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            masked_account=snapshot.masked_account,
            account_currency=snapshot.currency,
            account_equity=snapshot.equity,
            selected_risk_fraction=account.risk_fraction,
            intended_entry_price=intent.entry_price,
            intended_stop_price=intent.stop_price,
            selected_target_price=intent.target_price,
            selected_exit_mode=intent.selected_exit_mode,
            prepared_volume=risk.final_volume,
            volume_unit=risk.volume_unit,
            projected_cash_risk=risk.projected_cash_risk,
            projected_equity_risk_fraction=risk.projected_equity_risk_fraction,
            partial_volume=partial_volume,
            runner_volume=runner_volume,
            actual_fill_price=None,
            break_even_price_source="ACTUAL_ACCOUNT_FILL_PRICE",
            post_fill_risk_revalidation_required=True,
            preparation_ready=True,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(
                "TRADE_INTENT_REPLICATED_NOT_RAW_VOLUME_COPIED",
                "POSITION_SIZE_CALCULATED_FROM_THIS_ACCOUNT_EQUITY_AND_STOP_DISTANCE",
                "BROKER_VOLUME_GRID_VALIDATED",
                "BREAK_EVEN_WILL_USE_THIS_ACCOUNTS_ACTUAL_FILL_PRICE",
                "POST_FILL_RISK_REVALIDATION_REQUIRED_BEFORE_MANAGEMENT",
                "NO_BROKER_ORDER_SUBMISSION_IN_PHASE20",
            ),
        )

    @staticmethod
    def _capability_failure(*, intent: TradeIntent, capabilities: Any) -> str | None:
        if intent.execution_style == "MARKET_ON_SIGNAL" and not capabilities.supports_market_orders:
            return "BROKER_PLATFORM_DOES_NOT_SUPPORT_REQUIRED_MARKET_ORDER_STYLE"
        if not capabilities.supports_server_side_sl:
            return "BROKER_PLATFORM_DOES_NOT_SUPPORT_REQUIRED_SERVER_SIDE_STOP"
        if not capabilities.supports_server_side_tp:
            return "BROKER_PLATFORM_DOES_NOT_SUPPORT_REQUIRED_SERVER_SIDE_TARGET"
        if intent.structural_break_even and not capabilities.supports_stop_amendment:
            return "BROKER_PLATFORM_CANNOT_AMEND_STOP_FOR_STRUCTURAL_BREAK_EVEN"
        if (
            intent.selected_exit_mode == "HOLD_HTF_LIQUIDITY"
            and not capabilities.supports_partial_close
        ):
            return "BROKER_PLATFORM_CANNOT_EXECUTE_REQUIRED_60_40_PARTIAL_CLOSE"
        return None

    @staticmethod
    def _target_failure(intent: TradeIntent) -> str | None:
        if intent.direction == "BEARISH" and intent.target_price >= intent.entry_price:
            return "BEARISH_TARGET_MUST_BE_BELOW_ENTRY"
        if intent.direction == "BULLISH" and intent.target_price <= intent.entry_price:
            return "BULLISH_TARGET_MUST_BE_ABOVE_ENTRY"
        if intent.selected_exit_mode != "HOLD_HTF_LIQUIDITY":
            return None
        partial = float(intent.partial_trigger_price or 0.0)
        runner = float(intent.runner_target_price or 0.0)
        if intent.direction == "BEARISH" and not runner < partial < intent.entry_price:
            return "BEARISH_RUNNER_MUST_BE_BEYOND_60_PERCENT_PARTIAL_TARGET"
        if intent.direction == "BULLISH" and not runner > partial > intent.entry_price:
            return "BULLISH_RUNNER_MUST_BE_BEYOND_60_PERCENT_PARTIAL_TARGET"
        return None

    @staticmethod
    def _to_risk_spec(spec: BrokerInstrumentSpec) -> InstrumentRiskSpec:
        if spec.tick_value_account_currency is None:
            raise ValueError("verified tick value required")
        return InstrumentRiskSpec(
            symbol=spec.broker_symbol,
            tick_size=spec.tick_size,
            tick_value_per_volume_unit=spec.tick_value_account_currency,
            volume_step=spec.volume_step,
            min_volume=spec.min_volume,
            max_volume=spec.max_volume,
            volume_unit=spec.volume_unit,
            pip_size=spec.pip_size,
        )

    @staticmethod
    def _exact_split(
        *,
        total: float,
        instrument: BrokerInstrumentSpec,
        partial_fraction: float,
        runner_fraction: float,
    ) -> tuple[float, float] | None:
        partial = total * partial_fraction
        runner = total * runner_fraction
        step = instrument.volume_step

        def on_grid(value: float) -> bool:
            steps = round(value / step)
            return math.isclose(steps * step, value, rel_tol=0.0, abs_tol=1e-9)

        if not on_grid(partial) or not on_grid(runner):
            return None
        if partial + 1e-12 < instrument.min_volume or runner + 1e-12 < instrument.min_volume:
            return None
        if not math.isclose(partial + runner, total, rel_tol=0.0, abs_tol=1e-9):
            return None
        return partial, runner

    @staticmethod
    def _blocked(
        *,
        intent: TradeIntent,
        account: ManagedBrokerAccount,
        status: AccountPreparationStatus,
        reason: str,
        broker_symbol: str | None = None,
        masked_account: str | None = None,
        currency: str | None = None,
        equity: float | None = None,
    ) -> AccountExecutionPlan:
        return AccountExecutionPlan(
            account_alias=account.account_alias,
            broker_type=account.adapter.broker_type,
            adapter_id=account.adapter.adapter_id,
            status=status,
            trade_id=intent.trade_id,
            canonical_symbol=intent.canonical,
            broker_symbol=broker_symbol,
            masked_account=masked_account,
            account_currency=currency,
            account_equity=equity,
            selected_risk_fraction=account.risk_fraction,
            intended_entry_price=intent.entry_price,
            intended_stop_price=intent.stop_price,
            selected_target_price=intent.target_price,
            selected_exit_mode=intent.selected_exit_mode,
            prepared_volume=None,
            volume_unit=None,
            projected_cash_risk=None,
            projected_equity_risk_fraction=None,
            partial_volume=None,
            runner_volume=None,
            actual_fill_price=None,
            break_even_price_source="ACTUAL_ACCOUNT_FILL_PRICE",
            post_fill_risk_revalidation_required=True,
            preparation_ready=False,
            order_authorized=False,
            broker_order_placed=False,
            reason_codes=(reason, "NO_BROKER_ORDER_SUBMISSION_IN_PHASE20"),
        )
