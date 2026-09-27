from __future__ import annotations

import pytest

from tradingagents.brokers import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerSymbolMap,
    BrokerType,
    OrchestrationPolicy,
    TradeIntent,
    canonicalize_symbol,
)
from tradingagents.ict.multi_account import (
    AccountPreparationStatus,
    ManagedBrokerAccount,
    MultiAccountBatchStatus,
    MultiAccountExecutionManager,
)


class StubBrokerAdapter:
    def __init__(
        self,
        *,
        adapter_id: str,
        broker_type: BrokerType,
        equity: float,
        instrument: BrokerInstrumentSpec,
        capabilities: BrokerCapabilities | None = None,
        connected: bool = True,
    ) -> None:
        self._adapter_id = adapter_id
        self._broker_type = broker_type
        self._equity = equity
        self._instrument = instrument
        self._connected = connected
        self._environment = "LIVE"  # Internal only; public orchestration must never expose it.
        self._capabilities = capabilities or BrokerCapabilities(
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

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return self._broker_type

    def public_status(self) -> dict:
        return {
            "status": "CONNECTED" if self._connected else "DISCONNECTED",
            "account_environment": "HIDDEN_INTERNAL",
            "order_submission_enabled": False,
        }

    def capabilities(self) -> BrokerCapabilities:
        return self._capabilities

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=self._broker_type,
            broker_name=f"{self._broker_type.value} broker",
            masked_account="••••1234",
            connected=self._connected,
            currency="USD",
            balance=self._equity,
            equity=self._equity,
            used_margin=0.0,
            free_margin=self._equity,
        )

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec:
        del account_alias, canonical_symbol
        assert broker_symbol == self._instrument.broker_symbol
        return self._instrument

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote:
        del account_alias
        return BrokerQuote(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            bid=21899.75,
            ask=21900.25,
            timestamp_ms=1_700_000_000_000,
        )


def _capabilities(**overrides: bool) -> BrokerCapabilities:
    values = {
        "supports_market_orders": True,
        "supports_limit_orders": True,
        "supports_stop_orders": True,
        "supports_server_side_sl": True,
        "supports_server_side_tp": True,
        "supports_stop_amendment": True,
        "supports_partial_close": True,
        "supports_native_oco": True,
        "supports_streaming_quotes": True,
        "supports_historical_bars": True,
        "execution_enabled": False,
    }
    values.update(overrides)
    return BrokerCapabilities(**values)


def _cfd_spec(symbol: str = "NAS100") -> BrokerInstrumentSpec:
    return BrokerInstrumentSpec(
        canonical_symbol="NASDAQ",
        broker_symbol=symbol,
        tick_size=1.0,
        tick_value_account_currency=1.0,
        volume_step=0.1,
        min_volume=0.1,
        max_volume=1000.0,
        volume_unit="lot",
        pip_size=1.0,
    )


def _futures_spec(symbol: str = "NQ DEC26") -> BrokerInstrumentSpec:
    return BrokerInstrumentSpec(
        canonical_symbol="NASDAQ",
        broker_symbol=symbol,
        tick_size=0.25,
        tick_value_account_currency=5.0,
        volume_step=1.0,
        min_volume=1.0,
        max_volume=100.0,
        volume_unit="contract",
        pip_size=None,
    )


def _intent(*, hold: bool = False) -> TradeIntent:
    if hold:
        return TradeIntent(
            trade_id="LDN-NQ-001",
            canonical_symbol="NQ",
            direction="BEARISH",
            entry_price=21900.0,
            stop_price=21920.0,
            target_price=21860.0,
            selected_exit_mode="HOLD_HTF_LIQUIDITY",
            partial_fraction=0.60,
            partial_trigger_price=21850.0,
            runner_fraction=0.40,
            runner_target_price=21800.0,
        )
    return TradeIntent(
        trade_id="LDN-NQ-001",
        canonical_symbol="NAS100",
        direction="BEARISH",
        entry_price=21900.0,
        stop_price=21920.0,
        target_price=21860.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _managed(
    alias: str,
    *,
    broker_type: BrokerType,
    equity: float,
    risk: float,
    broker_symbol: str,
    instrument: BrokerInstrumentSpec,
    enabled: bool = True,
    capabilities: BrokerCapabilities | None = None,
) -> ManagedBrokerAccount:
    adapter = StubBrokerAdapter(
        adapter_id=f"{alias}-adapter",
        broker_type=broker_type,
        equity=equity,
        instrument=instrument,
        capabilities=capabilities,
    )
    return ManagedBrokerAccount(
        account_alias=alias,
        adapter=adapter,
        symbol_map=BrokerSymbolMap(broker_type, {"NASDAQ": broker_symbol}),
        risk_fraction=risk,
        enabled=enabled,
    )


def test_canonical_aliases_normalize_nasdaq_without_broker_guessing() -> None:
    assert canonicalize_symbol("NQ") == "NASDAQ"
    assert canonicalize_symbol("NAS100") == "NASDAQ"
    mapper = BrokerSymbolMap(BrokerType.NINJATRADER, {"NASDAQ": "NQ DEC26"})
    assert mapper.resolve("US100") == "NQ DEC26"
    with pytest.raises(ValueError, match="No explicit"):
        mapper.resolve("GOLD")


def test_same_trade_is_sized_independently_per_account_equity() -> None:
    accounts = [
        _managed(
            "small",
            broker_type=BrokerType.MT5,
            equity=10_000.0,
            risk=0.03,
            broker_symbol="NAS100",
            instrument=_cfd_spec(),
        ),
        _managed(
            "large",
            broker_type=BrokerType.CTRADER,
            equity=25_000.0,
            risk=0.03,
            broker_symbol="US100",
            instrument=_cfd_spec("US100"),
        ),
    ]
    result = MultiAccountExecutionManager().prepare(intent=_intent(), accounts=accounts)
    assert result.status == MultiAccountBatchStatus.READY
    assert result.ready_accounts == 2
    small, large = result.accounts
    assert small.prepared_volume == pytest.approx(15.0)
    assert large.prepared_volume == pytest.approx(37.5)
    assert small.projected_cash_risk == pytest.approx(300.0)
    assert large.projected_cash_risk == pytest.approx(750.0)
    assert small.order_authorized is False
    assert large.broker_order_placed is False


def test_each_account_can_use_its_own_allowed_risk_tier() -> None:
    accounts = [
        _managed(
            "risk3",
            broker_type=BrokerType.MT5,
            equity=10_000.0,
            risk=0.03,
            broker_symbol="NAS100",
            instrument=_cfd_spec(),
        ),
        _managed(
            "risk5",
            broker_type=BrokerType.CTRADER,
            equity=10_000.0,
            risk=0.05,
            broker_symbol="US100",
            instrument=_cfd_spec("US100"),
        ),
    ]
    result = MultiAccountExecutionManager().prepare(intent=_intent(), accounts=accounts)
    assert result.accounts[0].prepared_volume == pytest.approx(15.0)
    assert result.accounts[1].prepared_volume == pytest.approx(25.0)


def test_hold_requires_exact_60_40_on_each_accounts_broker_grid() -> None:
    cfd = _managed(
        "cfd",
        broker_type=BrokerType.CTRADER,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="US100",
        instrument=_cfd_spec("US100"),
    )
    futures = _managed(
        "futures",
        broker_type=BrokerType.NINJATRADER,
        equity=100_000.0,
        risk=0.03,
        broker_symbol="NQ DEC26",
        instrument=_futures_spec(),
    )
    result = MultiAccountExecutionManager().prepare(
        intent=_intent(hold=True),
        accounts=[cfd, futures],
        policy=OrchestrationPolicy.BEST_EFFORT,
    )
    assert result.status == MultiAccountBatchStatus.PARTIAL_READY
    assert result.accounts[0].status == AccountPreparationStatus.READY_FOR_EXECUTION_ADAPTER
    assert result.accounts[0].partial_volume == pytest.approx(9.0)
    assert result.accounts[0].runner_volume == pytest.approx(6.0)
    assert result.accounts[1].status == AccountPreparationStatus.BLOCKED_HOLD_SPLIT
    assert "EXACT_60_40" in result.accounts[1].reason_codes[0]


def test_all_or_none_blocks_batch_if_one_account_cannot_follow_contract() -> None:
    cfd = _managed(
        "cfd",
        broker_type=BrokerType.CTRADER,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="US100",
        instrument=_cfd_spec("US100"),
    )
    futures = _managed(
        "futures",
        broker_type=BrokerType.NINJATRADER,
        equity=100_000.0,
        risk=0.03,
        broker_symbol="NQ DEC26",
        instrument=_futures_spec(),
    )
    result = MultiAccountExecutionManager().prepare(
        intent=_intent(hold=True),
        accounts=[cfd, futures],
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    assert result.status == MultiAccountBatchStatus.BLOCKED
    assert result.batch_ready_for_future_execution is False
    assert result.ready_accounts == 1
    assert result.blocked_accounts == 1


def test_disabled_account_is_skipped_without_blocking_best_effort() -> None:
    enabled = _managed(
        "enabled",
        broker_type=BrokerType.MT5,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="NAS100",
        instrument=_cfd_spec(),
    )
    disabled = _managed(
        "disabled",
        broker_type=BrokerType.NINJATRADER,
        equity=100_000.0,
        risk=0.03,
        broker_symbol="NQ DEC26",
        instrument=_futures_spec(),
        enabled=False,
    )
    result = MultiAccountExecutionManager().prepare(intent=_intent(), accounts=[enabled, disabled])
    assert result.status == MultiAccountBatchStatus.READY
    assert result.enabled_accounts == 1
    assert result.skipped_accounts == 1
    assert result.accounts[1].status == AccountPreparationStatus.SKIPPED_DISABLED


def test_capability_mismatch_fails_closed_for_only_that_account() -> None:
    incapable = _managed(
        "no-partials",
        broker_type=BrokerType.CUSTOM,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="NAS",
        instrument=_cfd_spec("NAS"),
        capabilities=_capabilities(supports_partial_close=False),
    )
    result = MultiAccountExecutionManager().prepare(intent=_intent(hold=True), accounts=[incapable])
    assert result.status == MultiAccountBatchStatus.BLOCKED
    assert result.accounts[0].status == AccountPreparationStatus.BLOCKED_CAPABILITY_MISMATCH
    assert "PARTIAL_CLOSE" in result.accounts[0].reason_codes[0]


def test_missing_verified_tick_value_waits_instead_of_guessing() -> None:
    instrument = BrokerInstrumentSpec(
        canonical_symbol="NASDAQ",
        broker_symbol="US100",
        tick_size=0.1,
        tick_value_account_currency=None,
        volume_step=0.1,
        min_volume=0.1,
        max_volume=100.0,
        volume_unit="lot",
    )
    account = _managed(
        "ctrader",
        broker_type=BrokerType.CTRADER,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="US100",
        instrument=instrument,
    )
    result = MultiAccountExecutionManager().prepare(intent=_intent(), accounts=[account])
    assert result.accounts[0].status == AccountPreparationStatus.WAIT_FOR_VERIFIED_TICK_VALUE
    assert result.accounts[0].prepared_volume is None


def test_fill_aware_management_uses_future_actual_account_fill() -> None:
    account = _managed(
        "a",
        broker_type=BrokerType.MT5,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="NAS100",
        instrument=_cfd_spec(),
    )
    result = MultiAccountExecutionManager().prepare(intent=_intent(), accounts=[account])
    plan = result.accounts[0]
    assert plan.actual_fill_price is None
    assert plan.break_even_price_source == "ACTUAL_ACCOUNT_FILL_PRICE"
    assert plan.post_fill_risk_revalidation_required is True


def test_public_result_never_exposes_internal_demo_live_classification() -> None:
    account = _managed(
        "private",
        broker_type=BrokerType.MT5,
        equity=10_000.0,
        risk=0.03,
        broker_symbol="NAS100",
        instrument=_cfd_spec(),
    )
    rendered = str(
        MultiAccountExecutionManager().prepare(intent=_intent(), accounts=[account]).to_dict()
    )
    assert "HIDDEN_INTERNAL" in rendered
    assert "'LIVE'" not in rendered
    assert "'DEMO'" not in rendered
    assert "broker_order_placed': False" in rendered
