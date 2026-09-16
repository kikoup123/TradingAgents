from __future__ import annotations

import pytest

from tradingagents.brokers import (
    AccountClassification,
    AccountClassificationSource,
    AccountRiskBaseResolver,
    AccountRiskBaseStatus,
    AccountRiskProfile,
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerSymbolMap,
    BrokerType,
    OrchestrationPolicy,
    PropFirmRiskLimits,
    TradeIntent,
)
from tradingagents.ict import (
    ClassifiedManagedAccount,
    LondresPhase23MixedAccountRiskEngine,
    ManagedBrokerAccount,
    MixedAccountExecutionManager,
    MultiAccountBatchStatus,
    Phase23AccountStatus,
)


class NinjaStubAdapter:
    def __init__(self, equities: dict[str, float]) -> None:
        self._equities = dict(equities)

    @property
    def adapter_id(self) -> str:
        return "ninja-stub"

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.NINJATRADER

    def public_status(self) -> dict:
        return {
            "status": "CONNECTED",
            "account_environment": "HIDDEN_INTERNAL",
            "order_submission_enabled": False,
        }

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            supports_market_orders=True,
            supports_limit_orders=True,
            supports_stop_orders=True,
            supports_server_side_sl=True,
            supports_server_side_tp=True,
            supports_stop_amendment=True,
            supports_partial_close=True,
            supports_native_oco=True,
            supports_streaming_quotes=True,
            supports_historical_bars=True,
            execution_enabled=False,
        )

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        equity = self._equities[account_alias]
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=BrokerType.NINJATRADER,
            broker_name="Ninja Stub",
            masked_account=f"••••{account_alias[-4:]}",
            connected=True,
            currency="USD",
            balance=equity,
            equity=equity,
            used_margin=0.0,
            free_margin=equity,
        )

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec:
        del account_alias
        return BrokerInstrumentSpec(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            tick_size=0.25,
            tick_value_account_currency=5.0,
            tick_value_currency="USD",
            tick_value_source="EXCHANGE_CONTRACT_SPEC",
            tick_value_timestamp_ms=None,
            valuation_model="STATIC_EXCHANGE_FUTURES_CONTRACT",
            volume_step=1.0,
            min_volume=1.0,
            max_volume=100.0,
            volume_unit="contract",
            metadata_verified=True,
        )

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote:
        del account_alias
        return BrokerQuote(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            bid=21999.75,
            ask=22000.0,
            timestamp_ms=1_800_000_000_000,
        )


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LDN-NQ-PROP-001",
        canonical_symbol="NASDAQ",
        direction="BEARISH",
        entry_price=22000.0,
        stop_price=22001.0,
        target_price=21990.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _managed(adapter: NinjaStubAdapter, alias: str) -> ManagedBrokerAccount:
    return ManagedBrokerAccount(
        account_alias=alias,
        adapter=adapter,
        symbol_map=BrokerSymbolMap(
            broker_type=BrokerType.NINJATRADER,
            mapping={"NASDAQ": "NQ DEC26"},
        ),
        risk_fraction=0.03,
    )


def _personal(adapter: NinjaStubAdapter, alias: str) -> ClassifiedManagedAccount:
    return ClassifiedManagedAccount(
        managed_account=_managed(adapter, alias),
        risk_profile=AccountRiskProfile(
            account_alias=alias,
            configured_classification=AccountClassification.PERSONAL,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
        ),
    )


def _prop(adapter: NinjaStubAdapter, alias: str) -> ClassifiedManagedAccount:
    return ClassifiedManagedAccount(
        managed_account=_managed(adapter, alias),
        risk_profile=AccountRiskProfile(
            account_alias=alias,
            configured_classification=AccountClassification.PROP_FIRM,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
            prop_limits=PropFirmRiskLimits(
                daily_loss_limit=3000.0,
                daily_loss_used=500.0,
                remaining_drawdown_buffer=1500.0,
                nominal_account_size=100000.0,
            ),
        ),
    )


def test_two_personal_and_one_prop_use_three_independent_risk_bases() -> None:
    adapter = NinjaStubAdapter(
        {
            "real-a": 10000.0,
            "real-b": 20000.0,
            "prop-c": 100000.0,
        }
    )
    result = MixedAccountExecutionManager().prepare(
        intent=_intent(),
        accounts=(
            _personal(adapter, "real-a"),
            _personal(adapter, "real-b"),
            _prop(adapter, "prop-c"),
        ),
    )

    assert result.status is MultiAccountBatchStatus.READY
    assert result.ready_accounts == 3
    plans = {plan.account_alias: plan for plan in result.accounts}

    assert plans["real-a"].risk_base == pytest.approx(10000.0)
    assert plans["real-b"].risk_base == pytest.approx(20000.0)
    assert plans["prop-c"].actual_account_equity == pytest.approx(100000.0)
    assert plans["prop-c"].nominal_account_size == pytest.approx(100000.0)
    assert plans["prop-c"].remaining_daily_loss_buffer == pytest.approx(2500.0)
    assert plans["prop-c"].remaining_drawdown_buffer == pytest.approx(1500.0)
    assert plans["prop-c"].risk_base == pytest.approx(1500.0)
    assert plans["prop-c"].risk_base_source == "MIN_ACTIVE_PROP_LOSS_BUFFER"

    assert plans["real-a"].phase20_account_plan["prepared_volume"] == pytest.approx(15.0)
    assert plans["real-b"].phase20_account_plan["prepared_volume"] == pytest.approx(30.0)
    assert plans["prop-c"].phase20_account_plan["prepared_volume"] == pytest.approx(2.0)
    assert plans["prop-c"].phase20_account_plan["account_equity"] == pytest.approx(1500.0)
    assert plans["prop-c"].risk_cash_budget == pytest.approx(45.0)
    assert plans["prop-c"].order_authorized is False
    assert plans["prop-c"].broker_order_placed is False


def test_prop_nominal_account_size_is_never_used_for_position_sizing() -> None:
    result = AccountRiskBaseResolver().resolve(
        profile=AccountRiskProfile(
            account_alias="prop",
            configured_classification=AccountClassification.PROP_FIRM,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
            prop_limits=PropFirmRiskLimits(
                daily_loss_limit=2000.0,
                daily_loss_used=250.0,
                remaining_drawdown_buffer=900.0,
                nominal_account_size=150000.0,
            ),
        ),
        actual_account_equity=151250.0,
    )

    assert result.status is AccountRiskBaseStatus.READY
    assert result.actual_account_equity == pytest.approx(151250.0)
    assert result.nominal_account_size == pytest.approx(150000.0)
    assert result.risk_base == pytest.approx(900.0)
    assert result.to_dict()["nominal_account_size_used_for_sizing"] is False


def test_prop_without_secondary_drawdown_uses_remaining_daily_loss_buffer() -> None:
    result = AccountRiskBaseResolver().resolve(
        profile=AccountRiskProfile(
            account_alias="prop",
            configured_classification=AccountClassification.PROP_FIRM,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
            prop_limits=PropFirmRiskLimits(
                daily_loss_limit=2500.0,
                daily_loss_used=400.0,
                nominal_account_size=50000.0,
            ),
        ),
        actual_account_equity=51000.0,
    )

    assert result.status is AccountRiskBaseStatus.READY
    assert result.risk_base == pytest.approx(2100.0)


def test_unknown_classification_fails_closed_but_best_effort_keeps_other_account_ready() -> None:
    adapter = NinjaStubAdapter({"real-a": 10000.0, "unknown-b": 20000.0})
    unknown = ClassifiedManagedAccount(
        managed_account=_managed(adapter, "unknown-b"),
        risk_profile=AccountRiskProfile(account_alias="unknown-b"),
    )
    result = MixedAccountExecutionManager().prepare(
        intent=_intent(),
        accounts=(_personal(adapter, "real-a"), unknown),
        policy=OrchestrationPolicy.BEST_EFFORT,
    )

    assert result.status is MultiAccountBatchStatus.PARTIAL_READY
    assert result.ready_accounts == 1
    assert result.blocked_accounts == 1
    blocked = next(plan for plan in result.accounts if plan.account_alias == "unknown-b")
    assert blocked.status is Phase23AccountStatus.BLOCKED_ACCOUNT_RISK_BASE
    assert "ACCOUNT_CLASSIFICATION_MUST_BE_VERIFIED_PER_ACCOUNT_NO_GUESSING" in blocked.reason_codes


def test_all_or_none_blocks_batch_when_one_account_classification_is_unknown() -> None:
    adapter = NinjaStubAdapter({"real-a": 10000.0, "unknown-b": 20000.0})
    unknown = ClassifiedManagedAccount(
        managed_account=_managed(adapter, "unknown-b"),
        risk_profile=AccountRiskProfile(account_alias="unknown-b"),
    )
    result = MixedAccountExecutionManager().prepare(
        intent=_intent(),
        accounts=(_personal(adapter, "real-a"), unknown),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )

    assert result.status is MultiAccountBatchStatus.BLOCKED
    assert result.batch_ready_for_future_execution is False


def test_detected_prop_conflicting_with_personal_configuration_fails_closed() -> None:
    result = AccountRiskBaseResolver().resolve(
        profile=AccountRiskProfile(
            account_alias="mixed",
            configured_classification=AccountClassification.PERSONAL,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
            detected_classification=AccountClassification.PROP_FIRM,
        ),
        actual_account_equity=50000.0,
    )

    assert result.status is AccountRiskBaseStatus.CLASSIFICATION_CONFLICT
    assert result.risk_base is None


def test_exhausted_prop_daily_or_drawdown_buffer_fails_closed() -> None:
    resolver = AccountRiskBaseResolver()
    daily = resolver.resolve(
        profile=AccountRiskProfile(
            account_alias="prop-daily",
            configured_classification=AccountClassification.PROP_FIRM,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
            prop_limits=PropFirmRiskLimits(
                daily_loss_limit=2000.0,
                daily_loss_used=2000.0,
                remaining_drawdown_buffer=1000.0,
            ),
        ),
        actual_account_equity=50000.0,
    )
    drawdown = resolver.resolve(
        profile=AccountRiskProfile(
            account_alias="prop-dd",
            configured_classification=AccountClassification.PROP_FIRM,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
            prop_limits=PropFirmRiskLimits(
                daily_loss_limit=2000.0,
                daily_loss_used=0.0,
                remaining_drawdown_buffer=0.0,
            ),
        ),
        actual_account_equity=50000.0,
    )

    assert daily.status is AccountRiskBaseStatus.PROP_DAILY_LOSS_EXHAUSTED
    assert drawdown.status is AccountRiskBaseStatus.PROP_DRAWDOWN_EXHAUSTED


def test_phase23_wrapper_keeps_execution_disabled() -> None:
    adapter = NinjaStubAdapter({"real-a": 10000.0})
    context = LondresPhase23MixedAccountRiskEngine().prepare(
        intent=_intent(),
        accounts=(_personal(adapter, "real-a"),),
    )

    assert context["status"] == "READY"
    assert context["execution_enabled"] is False
    assert context["order_submission_enabled"] is False
    assert context["order_authorized"] is False
    assert context["broker_order_placed"] is False
    assert LondresPhase23MixedAccountRiskEngine.state_update(context) == {
        "mixed_account_risk_state": context
    }
