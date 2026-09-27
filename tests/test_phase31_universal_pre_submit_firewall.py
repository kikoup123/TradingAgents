from __future__ import annotations

from dataclasses import dataclass

from tradingagents.brokers import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerSupervisionPolicy,
    BrokerType,
    OrchestrationPolicy,
    TradeIntent,
)
from tradingagents.ict.phase31 import (
    LondresPhase31UniversalPreSubmitFirewallEngine,
    Phase31AccountStatus,
    Phase31BrokerBinding,
    Phase31PreSubmitPolicy,
)

NOW_MS = 1_800_000_000_000


@dataclass
class _Resolution:
    ready: bool
    active_contract: str | None


class FakeReadOnlyAdapter:
    def __init__(
        self,
        *,
        adapter_id: str,
        broker_type: BrokerType,
        broker_name: str | None,
        equity: float,
        broker_symbol: str,
        tick_size: float,
        tick_value: float,
        volume_unit: str,
        min_volume: float,
        max_volume: float,
        volume_step: float,
        bid: float = 24_999.9,
        ask: float = 25_000.0,
        quote_timestamp_ms: int = NOW_MS - 500,
        tick_timestamp_ms: int = NOW_MS - 500,
        active_contract: str | None = None,
        execution_enabled: bool = False,
    ) -> None:
        self._adapter_id = adapter_id
        self._broker_type = broker_type
        self.broker_name = broker_name
        self.equity = equity
        self.broker_symbol = broker_symbol
        self.tick_size = tick_size
        self.tick_value = tick_value
        self.volume_unit = volume_unit
        self.min_volume = min_volume
        self.max_volume = max_volume
        self.volume_step = volume_step
        self.bid = bid
        self.ask = ask
        self.quote_timestamp_ms = quote_timestamp_ms
        self.tick_timestamp_ms = tick_timestamp_ms
        self.active_contract = active_contract
        self.execution_enabled = execution_enabled

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return self._broker_type

    def public_status(self) -> dict:
        return {
            "status": "CONNECTED",
            "broker": self.broker_name,
            "read_only": True,
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
            supports_native_oco=False,
            supports_streaming_quotes=True,
            supports_historical_bars=True,
            execution_enabled=self.execution_enabled,
        )

    def account_snapshot(self, account_alias: str) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            account_alias=account_alias,
            broker_type=self.broker_type,
            broker_name=self.broker_name,
            masked_account="••••1234",
            connected=True,
            currency="USD",
            balance=self.equity,
            equity=self.equity,
            used_margin=0.0,
            free_margin=self.equity,
        )

    def instrument_snapshot(
        self,
        *,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
    ) -> BrokerInstrumentSpec:
        del account_alias
        assert broker_symbol == self.broker_symbol
        return BrokerInstrumentSpec(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            tick_size=self.tick_size,
            tick_value_account_currency=self.tick_value,
            tick_value_currency="USD",
            tick_value_source="TEST_VERIFIED",
            tick_value_timestamp_ms=self.tick_timestamp_ms,
            valuation_model="TEST",
            volume_step=self.volume_step,
            min_volume=self.min_volume,
            max_volume=self.max_volume,
            volume_unit=self.volume_unit,
            metadata_verified=True,
        )

    def quote_snapshot(
        self,
        *,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
    ) -> BrokerQuote:
        del account_alias
        return BrokerQuote(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            bid=self.bid,
            ask=self.ask,
            timestamp_ms=self.quote_timestamp_ms,
        )

    def resolve_active_contract(self, root: str) -> _Resolution:
        del root
        return _Resolution(
            ready=self.active_contract is not None,
            active_contract=self.active_contract,
        )


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LONDRES-P31-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25_000.0,
        stop_price=24_990.0,
        target_price=25_050.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _supervision() -> BrokerSupervisionPolicy:
    return BrokerSupervisionPolicy(
        quote_max_age_ms=5_000,
        tick_value_max_age_ms=60_000,
        max_reconnect_attempts=0,
    )


def _market_policy(
    *,
    max_spread_ticks: int = 2,
    max_adverse_entry_deviation_ticks: int = 20,
) -> Phase31PreSubmitPolicy:
    return Phase31PreSubmitPolicy(
        max_spread_ticks=max_spread_ticks,
        max_adverse_entry_deviation_ticks=max_adverse_entry_deviation_ticks,
    )


def _phase30_account(
    *,
    alias: str,
    venue: str,
    broker_type: str,
    broker_symbol: str,
    broker_name: str | None,
    volume: float,
    unit: str,
    equity: float,
    cash_risk: float,
    fingerprint_character: str,
    selected_root: str | None = None,
) -> dict:
    upstream: dict = {}
    if selected_root is not None:
        upstream = {
            "selected_root": selected_root,
            "phase26_account_plan": {
                "policy_state": {
                    "risk_fraction": 0.03,
                    "max_risk_cash": None,
                }
            },
        }
    return {
        "account_alias": alias,
        "venue": venue,
        "broker_type": broker_type,
        "status": "AUTHORIZED_FOR_UNIVERSAL_HANDOFF",
        "trade_id": "LONDRES-P31-001",
        "canonical_symbol": "NASDAQ",
        "broker_symbol": broker_symbol,
        "broker_name": broker_name,
        "direction": "BULLISH",
        "prepared_volume": volume,
        "volume_unit": unit,
        "selected_risk_fraction": 0.03,
        "account_equity": equity,
        "projected_cash_risk": cash_risk,
        "projected_equity_risk_fraction": cash_risk / equity,
        "entry_price": 25_000.0,
        "stop_price": 24_990.0,
        "target_price": 25_050.0,
        "selected_exit_mode": "FULL_AT_SD_2",
        "execution_handoff_ready": True,
        "order_authorized": True,
        "authorization_fingerprint": fingerprint_character * 64,
        "upstream_authorization_fingerprint": (
            "f" * 64 if venue == "NINJATRADER" else None
        ),
        "source_phase": "PHASE27" if venue == "NINJATRADER" else "PHASE29",
        "upstream_account_plan": upstream,
    }


def _phase30_plan(
    *accounts: dict,
    policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
) -> dict:
    return {
        "phase": "LONDRES_PHASE30_UNIVERSAL_EXECUTION_AUTHORIZATION",
        "trade_id": "LONDRES-P31-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "policy": policy.value,
        "status": "READY",
        "execution_handoff_ready": True,
        "order_authorized": True,
        "order_submission_enabled": False,
        "accounts": list(accounts),
    }


def _ninja_account() -> dict:
    return _phase30_account(
        alias="NT-ALPHA",
        venue="NINJATRADER",
        broker_type="NINJATRADER",
        broker_symbol="NQ 12-26",
        broker_name=None,
        volume=2.0,
        unit="contracts",
        equity=20_000.0,
        cash_risk=400.0,
        fingerprint_character="a",
        selected_root="NQ",
    )


def _fp_account() -> dict:
    return _phase30_account(
        alias="FP-LIVE",
        venue="FP_MARKETS_CTRADER",
        broker_type="CTRADER",
        broker_symbol="US100",
        broker_name="FP Markets",
        volume=40.0,
        unit="units",
        equity=20_000.0,
        cash_risk=400.0,
        fingerprint_character="b",
    )


def _vantage_account() -> dict:
    return _phase30_account(
        alias="MT5-BETA",
        venue="VANTAGE_MT5",
        broker_type="MT5",
        broker_symbol="NAS100",
        broker_name="Vantage Global Prime",
        volume=20.0,
        unit="lots",
        equity=10_000.0,
        cash_risk=200.0,
        fingerprint_character="c",
    )


def _ninja_adapter(**overrides) -> FakeReadOnlyAdapter:
    values = {
        "adapter_id": "ninjatrader-readonly",
        "broker_type": BrokerType.NINJATRADER,
        "broker_name": "NinjaTrader Brokerage",
        "equity": 20_000.0,
        "broker_symbol": "NQ 12-26",
        "tick_size": 0.25,
        "tick_value": 5.0,
        "volume_unit": "contracts",
        "min_volume": 1.0,
        "max_volume": 10.0,
        "volume_step": 1.0,
        "active_contract": "NQ 12-26",
    }
    values.update(overrides)
    return FakeReadOnlyAdapter(**values)


def _fp_adapter(**overrides) -> FakeReadOnlyAdapter:
    values = {
        "adapter_id": "fpmarkets-ctrader",
        "broker_type": BrokerType.CTRADER,
        "broker_name": "FP Markets",
        "equity": 20_000.0,
        "broker_symbol": "US100",
        "tick_size": 0.1,
        "tick_value": 0.1,
        "volume_unit": "units",
        "min_volume": 1.0,
        "max_volume": 1_000.0,
        "volume_step": 1.0,
    }
    values.update(overrides)
    return FakeReadOnlyAdapter(**values)


def _vantage_adapter(**overrides) -> FakeReadOnlyAdapter:
    values = {
        "adapter_id": "vantage-mt5",
        "broker_type": BrokerType.MT5,
        "broker_name": "Vantage Global Prime",
        "equity": 10_000.0,
        "broker_symbol": "NAS100",
        "tick_size": 0.1,
        "tick_value": 0.1,
        "volume_unit": "lots",
        "min_volume": 0.01,
        "max_volume": 100.0,
        "volume_step": 0.01,
    }
    values.update(overrides)
    return FakeReadOnlyAdapter(**values)


def _bindings(
    *,
    ninja: FakeReadOnlyAdapter | None = None,
    fp: FakeReadOnlyAdapter | None = None,
    vantage: FakeReadOnlyAdapter | None = None,
) -> tuple[Phase31BrokerBinding, ...]:
    return (
        Phase31BrokerBinding("NT-ALPHA", ninja or _ninja_adapter()),
        Phase31BrokerBinding("FP-LIVE", fp or _fp_adapter()),
        Phase31BrokerBinding("MT5-BETA", vantage or _vantage_adapter()),
    )


def test_phase31_three_venues_pass_same_immediate_pre_submit_firewall() -> None:
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(
            _ninja_account(),
            _fp_account(),
            _vantage_account(),
        ),
        bindings=_bindings(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    assert result["pre_submit_ready"] is True
    assert result["order_authorized"] is True
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
    assert result["fingerprint_registry_mutated"] is False
    for account in result["accounts"]:
        assert account["status"] == "READY_FOR_EXECUTION_ADAPTER_HANDOFF"
        assert account["prepared_volume"] in {2.0, 40.0, 20.0}
        assert account["authorized_volume_resized"] is False
        assert len(account["pre_submit_snapshot_fingerprint"]) == 64
        assert account["fingerprint_consumed"] is False


def test_phase31_stale_vantage_quote_isolated_in_best_effort() -> None:
    stale = _vantage_adapter(quote_timestamp_ms=NOW_MS - 100_000)
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_fp_account(), _vantage_account()),
        bindings=(
            Phase31BrokerBinding("FP-LIVE", _fp_adapter()),
            Phase31BrokerBinding("MT5-BETA", stale),
        ),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    assert result["status"] == "PARTIAL_READY"
    assert result["ready_accounts"] == 1
    vantage = result["accounts"][1]
    assert vantage["status"] == Phase31AccountStatus.BLOCKED_SUPERVISION.value
    assert "BROKER_QUOTE_EXCEEDS_EXPLICIT_MAX_AGE" in vantage["reason_codes"]


def test_phase31_blocks_used_phase30_authorization_fingerprint() -> None:
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_fp_account()),
        bindings=(Phase31BrokerBinding("FP-LIVE", _fp_adapter()),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=("b" * 64,),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase31AccountStatus.BLOCKED_DUPLICATE_AUTHORIZATION.value
    assert account["pre_submit_snapshot_fingerprint"] is None


def test_phase31_blocks_ninjatrader_rollover_change() -> None:
    changed = _ninja_adapter(active_contract="NQ 03-27")
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_ninja_account()),
        bindings=(Phase31BrokerBinding("NT-ALPHA", changed),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase31AccountStatus.BLOCKED_CONTRACT_REVALIDATION.value
    assert "NINJATRADER_ACTIVE_CONTRACT_CHANGED_SINCE_PHASE30" in account["reason_codes"]


def test_phase31_blocks_authorized_volume_when_broker_grid_changes() -> None:
    changed = _vantage_adapter(max_volume=10.0)
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_vantage_account()),
        bindings=(Phase31BrokerBinding("MT5-BETA", changed),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase31AccountStatus.BLOCKED_VOLUME_GRID.value
    assert "PHASE30_VOLUME_OUTSIDE_CURRENT_BROKER_MIN_MAX" in account["reason_codes"]
    assert account["prepared_volume"] == 20.0
    assert account["authorized_volume_resized"] is False


def test_phase31_recalculates_risk_from_current_quote_and_equity() -> None:
    changed = _fp_adapter(equity=10_000.0, bid=25_000.9, ask=25_001.0)
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_fp_account()),
        bindings=(Phase31BrokerBinding("FP-LIVE", changed),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase31AccountStatus.BLOCKED_RISK_REVALIDATION.value
    assert account["current_executable_price"] == 25_001.0
    assert account["projected_cash_risk"] == 440.0
    assert account["projected_equity_risk_fraction"] == 0.044
    assert (
        "CURRENT_EQUITY_RISK_EXCEEDS_PHASE30_SELECTED_RISK_FRACTION"
        in account["reason_codes"]
    )


def test_phase31_blocks_current_spread_beyond_explicit_limit() -> None:
    wide = _fp_adapter(bid=24_999.0, ask=25_000.0)
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_fp_account()),
        bindings=(Phase31BrokerBinding("FP-LIVE", wide),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(max_spread_ticks=2),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase31AccountStatus.BLOCKED_MARKET_REVALIDATION.value
    assert "CURRENT_SPREAD_EXCEEDS_EXPLICIT_PHASE31_LIMIT" in account["reason_codes"]


def test_phase31_reverifies_current_broker_identity() -> None:
    changed = _vantage_adapter(broker_name="Other Broker Ltd")
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_vantage_account()),
        bindings=(Phase31BrokerBinding("MT5-BETA", changed),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase31AccountStatus.BLOCKED_BROKER_IDENTITY.value
    assert "CURRENT_BROKER_IDENTITY_DOES_NOT_MATCH_PHASE30_VENUE" in account["reason_codes"]
    assert "CURRENT_BROKER_IDENTITY_CHANGED_SINCE_PHASE30" in account["reason_codes"]


def test_phase31_applies_explicit_account_cash_cap_without_resizing() -> None:
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_fp_account()),
        bindings=(
            Phase31BrokerBinding(
                "FP-LIVE",
                _fp_adapter(),
                max_risk_cash=350.0,
            ),
        ),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase31AccountStatus.BLOCKED_RISK_REVALIDATION.value
    assert "CURRENT_PROJECTED_CASH_RISK_EXCEEDS_EXPLICIT_ACCOUNT_CAP" in account["reason_codes"]
    assert account["prepared_volume"] == 40.0
    assert account["authorized_volume_resized"] is False


def test_phase31_all_or_none_revokes_otherwise_ready_venues() -> None:
    stale = _vantage_adapter(quote_timestamp_ms=NOW_MS - 100_000)
    result = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(
            _ninja_account(),
            _fp_account(),
            _vantage_account(),
            policy=OrchestrationPolicy.ALL_OR_NONE,
        ),
        bindings=_bindings(vantage=stale),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    assert result["status"] == "BLOCKED"
    assert result["ready_accounts"] == 0
    assert result["order_authorized"] is False
    assert result["accounts"][0]["status"] == Phase31AccountStatus.BLOCKED_BATCH_POLICY.value
    assert result["accounts"][1]["status"] == Phase31AccountStatus.BLOCKED_BATCH_POLICY.value
    assert result["accounts"][2]["status"] == Phase31AccountStatus.BLOCKED_SUPERVISION.value
    assert result["accounts"][0]["pre_submit_snapshot_fingerprint"] is None


def test_phase31_has_no_execution_surface() -> None:
    engine = LondresPhase31UniversalPreSubmitFirewallEngine()
    assert not hasattr(engine, "place_order")
    assert not hasattr(engine, "submit_order")
    assert not hasattr(engine, "amend_order")
    assert not hasattr(engine, "cancel_order")
    assert not hasattr(engine, "flatten")
    result = engine.revalidate(
        intent=_intent(),
        phase30_plan=_phase30_plan(_fp_account()),
        bindings=(Phase31BrokerBinding("FP-LIVE", _fp_adapter()),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )
    assert result["execution_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
