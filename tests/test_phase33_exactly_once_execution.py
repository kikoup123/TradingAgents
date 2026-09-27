from __future__ import annotations

import json

from tradingagents.brokers.contracts import BrokerType, OrchestrationPolicy, TradeIntent
from tradingagents.brokers.execution import (
    BrokerExecutionCapabilities,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)
from tradingagents.ict.phase32 import (
    LondresPhase32ExactlyOnceShadowCommandEngine,
    SQLiteExecutionAuthorizationLedger,
)
from tradingagents.ict.phase33 import (
    LondresPhase33ExactlyOnceBrokerExecutionEngine,
    Phase33BrokerBinding,
    Phase33ExecutionLedger,
    Phase33ExecutionPolicy,
)

NOW_MS = 1_800_000_000_000


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LONDRES-P33-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25_000.0,
        stop_price=24_990.0,
        target_price=25_050.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _phase31_account(
    *,
    alias: str = "NT-ALPHA",
    venue: str = "NINJATRADER",
    broker_type: str = "NINJATRADER",
    broker_symbol: str = "NQ 12-26",
    volume: float = 2.0,
    unit: str = "contracts",
    phase30_char: str = "a",
    phase31_char: str = "d",
    quote_timestamp_ms: int = NOW_MS - 500,
) -> dict:
    return {
        "account_alias": alias,
        "venue": venue,
        "broker_type": broker_type,
        "status": "READY_FOR_EXECUTION_ADAPTER_HANDOFF",
        "trade_id": "LONDRES-P33-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "broker_symbol": broker_symbol,
        "prepared_volume": volume,
        "volume_unit": unit,
        "selected_risk_fraction": 0.03,
        "phase30_authorization_fingerprint": phase30_char * 64,
        "pre_submit_snapshot_fingerprint": phase31_char * 64,
        "current_executable_price": 25_000.0,
        "quote_timestamp_ms": quote_timestamp_ms,
        "pre_submit_ready": True,
        "order_authorized": True,
    }


def _phase31_plan(
    *accounts: dict,
    policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
) -> dict:
    return {
        "phase": "LONDRES_PHASE31_UNIVERSAL_PRE_SUBMIT_FIREWALL",
        "trade_id": "LONDRES-P33-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "policy": policy.value,
        "status": "READY",
        "pre_submit_ready": True,
        "order_authorized": True,
        "order_submission_enabled": False,
        "accounts": list(accounts),
    }


def _phase32(tmp_path, *accounts: dict, policy=OrchestrationPolicy.BEST_EFFORT):
    ledger = SQLiteExecutionAuthorizationLedger(tmp_path / "phase33.sqlite")
    plan = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_phase31_plan(*accounts, policy=policy),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    return ledger, plan


class FakeExecutionAdapter:
    def __init__(
        self,
        *,
        account_alias: str,
        venue: str = "NINJATRADER",
        broker_type: BrokerType = BrokerType.NINJATRADER,
        submit_outcome: BrokerExecutionOutcome = BrokerExecutionOutcome.ACKNOWLEDGED,
        reconcile_outcome: BrokerExecutionOutcome = BrokerExecutionOutcome.ACKNOWLEDGED,
        execution_enabled: bool = True,
        stop_active: bool = True,
        target_active: bool = True,
        definite_no_fill: bool = False,
    ) -> None:
        self.account_alias = account_alias
        self._venue = venue
        self._broker_type = broker_type
        self.submit_outcome = submit_outcome
        self.reconcile_outcome = reconcile_outcome
        self.execution_enabled = execution_enabled
        self.stop_active = stop_active
        self.target_active = target_active
        self.definite_no_fill = definite_no_fill
        self.submit_calls = 0
        self.reconcile_calls = 0

    @property
    def adapter_id(self) -> str:
        return f"fake-{self._venue.lower()}"

    @property
    def broker_type(self) -> BrokerType:
        return self._broker_type

    @property
    def venue(self) -> str:
        return self._venue

    def execution_capabilities(self) -> BrokerExecutionCapabilities:
        return BrokerExecutionCapabilities(
            broker_type=self.broker_type,
            venue=self.venue,
            execution_enabled=self.execution_enabled,
            supports_market_orders=True,
            supports_server_side_stop=True,
            supports_server_side_target=True,
            supports_reconciliation=True,
        )

    def submit_market(self, command, *, now_ms: int) -> BrokerExecutionReceipt:
        self.submit_calls += 1
        return self._receipt(command, outcome=self.submit_outcome, now_ms=now_ms)

    def reconcile(self, command, *, now_ms: int) -> BrokerExecutionReceipt:
        self.reconcile_calls += 1
        return self._receipt(command, outcome=self.reconcile_outcome, now_ms=now_ms)

    def _receipt(self, command, *, outcome, now_ms) -> BrokerExecutionReceipt:
        acknowledged = outcome is BrokerExecutionOutcome.ACKNOWLEDGED
        rejected = outcome is BrokerExecutionOutcome.REJECTED
        return BrokerExecutionReceipt(
            command_id=command["command_id"],
            account_alias=self.account_alias,
            venue=self.venue,
            broker_type=self.broker_type,
            client_order_label=command["client_order_label"],
            outcome=outcome,
            broker_order_id="ORDER-1" if acknowledged else None,
            broker_position_id="POSITION-1" if acknowledged else None,
            filled_volume=(command["exact_volume"] if acknowledged else 0.0 if rejected else None),
            average_fill_price=(25_000.25 if acknowledged else None),
            stop_protection_active=self.stop_active if acknowledged else False,
            target_protection_active=self.target_active if acknowledged else False,
            provider_code=f"FAKE_{outcome.value}",
            submitted_at_ms=now_ms,
            acknowledged_at_ms=now_ms if acknowledged else None,
            definite_no_fill=self.definite_no_fill if rejected else False,
        )


def _policy(enabled: bool = True, max_age: int = 5_000) -> Phase33ExecutionPolicy:
    return Phase33ExecutionPolicy(
        execution_enabled=enabled,
        max_phase31_snapshot_age_ms=max_age,
    )


def test_acknowledged_command_submits_once_and_becomes_consumed(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    adapter = FakeExecutionAdapter(account_alias="NT-ALPHA")
    engine = LondresPhase33ExactlyOnceBrokerExecutionEngine()

    first = engine.execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    second = engine.execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS + 1_000,
    )

    assert first["status"] == "READY"
    assert first["accounts"][0]["status"] == "ACKNOWLEDGED"
    assert first["accounts"][0]["authorization_consumed"] is True
    assert first["accounts"][0]["broker_order_placed"] is True
    assert second["accounts"][0]["status"] == "IDEMPOTENT_ACKNOWLEDGED"
    assert adapter.submit_calls == 1
    assert adapter.reconcile_calls == 0
    command_id = first["accounts"][0]["command_id"]
    assert Phase33ExecutionLedger(ledger).event_count(command_id) == 1


def test_execution_disabled_does_not_claim_or_submit(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    adapter = FakeExecutionAdapter(account_alias="NT-ALPHA")
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(enabled=False),
        now_ms=NOW_MS,
    )
    command_id = plan["accounts"][0]["command"]["command_id"]
    assert result["accounts"][0]["status"] == "BLOCKED_EXECUTION_DISABLED"
    assert adapter.submit_calls == 0
    assert ledger.inspect(command_id)["state"] == "SHADOW_READY"


def test_ambiguous_submit_reconciles_on_retry_without_resubmission(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    adapter = FakeExecutionAdapter(
        account_alias="NT-ALPHA",
        submit_outcome=BrokerExecutionOutcome.AMBIGUOUS,
        reconcile_outcome=BrokerExecutionOutcome.ACKNOWLEDGED,
    )
    engine = LondresPhase33ExactlyOnceBrokerExecutionEngine()
    first = engine.execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    second = engine.execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS + 1_000,
    )

    assert first["accounts"][0]["status"] == "AMBIGUOUS"
    assert first["accounts"][0]["authorization_locked_against_retry"] is True
    assert second["accounts"][0]["status"] == "RECOVERED_ACKNOWLEDGED"
    assert adapter.submit_calls == 1
    assert adapter.reconcile_calls == 1


def test_crash_after_claim_reconciles_instead_of_submitting_again(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    command_dict = plan["accounts"][0]["command"]
    from tradingagents.ict.phase32 import ExecutionCommand

    command = ExecutionCommand(**command_dict)
    claimed = Phase33ExecutionLedger(ledger).claim(command=command, now_ms=NOW_MS)
    assert claimed.action.value == "SUBMIT"

    adapter = FakeExecutionAdapter(account_alias="NT-ALPHA")
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS + 500,
    )
    assert result["accounts"][0]["status"] == "RECOVERED_ACKNOWLEDGED"
    assert adapter.submit_calls == 0
    assert adapter.reconcile_calls == 1


def test_definitive_rejection_is_terminal_failed_safe(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    adapter = FakeExecutionAdapter(
        account_alias="NT-ALPHA",
        submit_outcome=BrokerExecutionOutcome.REJECTED,
        definite_no_fill=True,
    )
    engine = LondresPhase33ExactlyOnceBrokerExecutionEngine()
    first = engine.execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    second = engine.execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS + 500,
    )
    assert first["accounts"][0]["status"] == "FAILED_SAFE"
    assert second["accounts"][0]["status"] == "FAILED_SAFE"
    assert adapter.submit_calls == 1
    assert adapter.reconcile_calls == 0


def test_ack_without_both_protections_requires_reconciliation(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    adapter = FakeExecutionAdapter(account_alias="NT-ALPHA", stop_active=False)
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    assert result["accounts"][0]["status"] == "RECONCILIATION_REQUIRED"
    assert result["accounts"][0]["authorization_consumed"] is False
    assert result["accounts"][0]["automatic_retry_allowed"] is False


def test_stale_phase31_snapshot_blocks_before_ledger_claim(tmp_path) -> None:
    ledger, plan = _phase32(
        tmp_path,
        _phase31_account(quote_timestamp_ms=NOW_MS - 50_000),
    )
    adapter = FakeExecutionAdapter(account_alias="NT-ALPHA")
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(max_age=1_000),
        now_ms=NOW_MS,
    )
    command_id = plan["accounts"][0]["command"]["command_id"]
    assert result["accounts"][0]["status"] == "BLOCKED_STALE_COMMAND"
    assert adapter.submit_calls == 0
    assert ledger.inspect(command_id)["state"] == "SHADOW_READY"


def test_multi_account_all_or_none_live_submission_is_rejected_as_false_guarantee(tmp_path) -> None:
    second = _phase31_account(
        alias="MT5-BETA",
        venue="VANTAGE_MT5",
        broker_type="MT5",
        broker_symbol="NAS100",
        volume=1.0,
        unit="lots",
        phase30_char="b",
        phase31_char="e",
    )
    ledger, plan = _phase32(
        tmp_path,
        _phase31_account(),
        second,
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    ninja = FakeExecutionAdapter(account_alias="NT-ALPHA")
    mt5 = FakeExecutionAdapter(
        account_alias="MT5-BETA",
        venue="VANTAGE_MT5",
        broker_type=BrokerType.MT5,
    )
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[
            Phase33BrokerBinding("NT-ALPHA", ninja),
            Phase33BrokerBinding("MT5-BETA", mt5),
        ],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    assert result["status"] == "BLOCKED"
    assert result["distributed_all_or_none_guaranteed"] is False
    assert all(item["status"] == "BLOCKED_POLICY" for item in result["accounts"])
    assert ninja.submit_calls == 0
    assert mt5.submit_calls == 0


def test_best_effort_can_ack_one_account_and_fail_safe_another(tmp_path) -> None:
    second = _phase31_account(
        alias="MT5-BETA",
        venue="VANTAGE_MT5",
        broker_type="MT5",
        broker_symbol="NAS100",
        volume=1.0,
        unit="lots",
        phase30_char="b",
        phase31_char="e",
    )
    ledger, plan = _phase32(tmp_path, _phase31_account(), second)
    ninja = FakeExecutionAdapter(account_alias="NT-ALPHA")
    mt5 = FakeExecutionAdapter(
        account_alias="MT5-BETA",
        venue="VANTAGE_MT5",
        broker_type=BrokerType.MT5,
        submit_outcome=BrokerExecutionOutcome.REJECTED,
        definite_no_fill=True,
    )
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[
            Phase33BrokerBinding("NT-ALPHA", ninja),
            Phase33BrokerBinding("MT5-BETA", mt5),
        ],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    assert result["status"] == "PARTIAL_READY"
    by_alias = {item["account_alias"]: item for item in result["accounts"]}
    assert by_alias["NT-ALPHA"]["status"] == "ACKNOWLEDGED"
    assert by_alias["MT5-BETA"]["status"] == "FAILED_SAFE"


def test_phase33_public_state_does_not_reveal_demo_live_environment(tmp_path) -> None:
    ledger, plan = _phase32(tmp_path, _phase31_account())
    adapter = FakeExecutionAdapter(account_alias="NT-ALPHA")
    result = LondresPhase33ExactlyOnceBrokerExecutionEngine().execute(
        phase32_plan=plan,
        phase32_ledger=ledger,
        bindings=[Phase33BrokerBinding("NT-ALPHA", adapter)],
        execution_policy=_policy(),
        now_ms=NOW_MS,
    )
    serialized = json.dumps(result, sort_keys=True)
    assert result["account_environment"] == "HIDDEN_INTERNAL"
    assert result["accounts"][0]["account_environment"] == "HIDDEN_INTERNAL"
    assert '"DEMO"' not in serialized
    assert '"LIVE"' not in serialized
    assert '"REAL"' not in serialized
