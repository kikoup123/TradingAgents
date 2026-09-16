from __future__ import annotations

from tradingagents.brokers.contracts import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerType,
)
from tradingagents.brokers.supervision import (
    BrokerConnectionSupervisor,
    BrokerSupervisionPolicy,
    BrokerSupervisionStatus,
)
from tradingagents.ict.phase22 import LondresPhase22BrokerSupervisionEngine


NOW_MS = 1_800_000_000_000


class SupervisedStubAdapter:
    def __init__(
        self,
        *,
        connected: bool = True,
        quote_timestamp_ms: int | None = NOW_MS - 100,
        tick_timestamp_ms: int | None = NOW_MS - 100,
        tick_value: float | None = 5.0,
        reconnect_succeeds: bool = True,
        fail_reads: bool = False,
    ) -> None:
        self.connected = connected
        self.quote_timestamp_ms = quote_timestamp_ms
        self.tick_timestamp_ms = tick_timestamp_ms
        self.tick_value = tick_value
        self.reconnect_succeeds = reconnect_succeeds
        self.fail_reads = fail_reads
        self.reconnect_calls = 0

    @property
    def adapter_id(self) -> str:
        return "stub-supervised"

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.NINJATRADER

    def public_status(self) -> dict:
        return {
            "status": "CONNECTED" if self.connected else "DISCONNECTED",
            "account_environment": "HIDDEN_INTERNAL",
            "order_submission_enabled": False,
        }

    def reconnect(self) -> dict:
        self.reconnect_calls += 1
        if self.reconnect_succeeds:
            self.connected = True
        return self.public_status()

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
        if self.fail_reads:
            raise RuntimeError("read failed")
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=self.broker_type,
            broker_name="Stub Futures",
            masked_account="••••4242",
            connected=self.connected,
            currency="USD",
            balance=100_000.0,
            equity=100_000.0,
            used_margin=0.0,
            free_margin=100_000.0,
        )

    def instrument_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerInstrumentSpec:
        del account_alias
        if self.fail_reads:
            raise RuntimeError("read failed")
        return BrokerInstrumentSpec(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            tick_size=0.25,
            tick_value_account_currency=self.tick_value,
            tick_value_currency="USD" if self.tick_value is not None else None,
            tick_value_source="EXCHANGE_CONTRACT_SPEC",
            tick_value_timestamp_ms=self.tick_timestamp_ms,
            valuation_model="BROKER_NATIVE",
            volume_step=1.0,
            min_volume=1.0,
            max_volume=100.0,
            volume_unit="contract",
        )

    def quote_snapshot(
        self, *, account_alias: str, canonical_symbol: str, broker_symbol: str
    ) -> BrokerQuote:
        del account_alias
        if self.fail_reads:
            raise RuntimeError("read failed")
        return BrokerQuote(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            bid=21900.0,
            ask=21900.25,
            timestamp_ms=self.quote_timestamp_ms,
        )


def _policy() -> BrokerSupervisionPolicy:
    return BrokerSupervisionPolicy(
        quote_max_age_ms=1_000,
        tick_value_max_age_ms=5_000,
        max_reconnect_attempts=2,
        future_timestamp_tolerance_ms=50,
    )


def test_fresh_connected_broker_is_execution_data_ready() -> None:
    result = BrokerConnectionSupervisor().check(
        adapter=SupervisedStubAdapter(),
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.HEALTHY
    assert result.execution_data_ready is True
    assert result.heartbeat_ok is True
    assert result.quote_age_ms == 100
    assert result.tick_value_age_ms == 100
    public = result.to_dict()
    assert public["account_environment"] == "HIDDEN_INTERNAL"
    assert public["order_submission_enabled"] is False
    assert public["order_authorized"] is False
    assert public["broker_order_placed"] is False


def test_disconnected_adapter_reconnects_before_data_is_accepted() -> None:
    adapter = SupervisedStubAdapter(connected=False, reconnect_succeeds=True)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.RECONNECTED
    assert result.reconnect_attempted is True
    assert result.reconnect_attempts == 1
    assert result.reconnect_succeeded is True
    assert result.execution_data_ready is True


def test_reconnect_failure_is_fail_closed() -> None:
    adapter = SupervisedStubAdapter(connected=False, reconnect_succeeds=False)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.DISCONNECTED
    assert result.reconnect_attempts == 2
    assert result.execution_data_ready is False


def test_stale_quote_blocks_execution_data_readiness() -> None:
    adapter = SupervisedStubAdapter(quote_timestamp_ms=NOW_MS - 1_001)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.STALE_QUOTE
    assert result.quote_age_ms == 1_001
    assert result.execution_data_ready is False


def test_stale_market_dependent_tick_value_blocks_sizing_data() -> None:
    adapter = SupervisedStubAdapter(tick_timestamp_ms=NOW_MS - 5_001)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.STALE_TICK_VALUE
    assert result.tick_value_age_ms == 5_001
    assert result.execution_data_ready is False


def test_static_verified_tick_value_needs_no_market_timestamp() -> None:
    adapter = SupervisedStubAdapter(tick_timestamp_ms=None)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.HEALTHY
    assert result.tick_value_age_ms is None
    assert result.execution_data_ready is True
    assert "TICK_VALUE_HAS_NO_MARKET_DEPENDENT_TIMESTAMP" in result.reason_codes


def test_unresolved_tick_value_fails_closed() -> None:
    adapter = SupervisedStubAdapter(tick_value=None, tick_timestamp_ms=None)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.WAIT_FOR_VERIFIED_TICK_VALUE
    assert result.execution_data_ready is False


def test_future_timestamp_beyond_explicit_tolerance_is_rejected() -> None:
    adapter = SupervisedStubAdapter(quote_timestamp_ms=NOW_MS + 51)
    result = BrokerConnectionSupervisor().check(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert result.status == BrokerSupervisionStatus.INVALID_TIMESTAMP
    assert result.execution_data_ready is False


def test_phase22_wrapper_keeps_broker_submission_disabled() -> None:
    context = LondresPhase22BrokerSupervisionEngine().analyze(
        adapter=SupervisedStubAdapter(),
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="NQ DEC26",
        now_ms=NOW_MS,
        policy=_policy(),
    )

    assert context["execution_data_ready"] is True
    assert context["order_submission_enabled"] is False
    assert context["order_authorized"] is False
    assert context["broker_order_placed"] is False
    assert LondresPhase22BrokerSupervisionEngine.state_update(context) == {
        "broker_supervision_state": context
    }
