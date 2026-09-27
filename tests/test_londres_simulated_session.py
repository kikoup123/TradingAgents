"""Offline integration of real signal detection and the Phase 30–34 handoff.

Entry/stop/target selection and the Phase 29 account plan are supplied fixtures:
this is not a complete raw-market-data-to-order or broker-connectivity test.
All broker interfaces are in-memory fakes; only the temporary SQLite ledger is real.
"""

from dataclasses import replace

import pytest

from tests.test_ict_csd import _bullish_csd_bars
from tests.test_phase30_universal_execution_authorization import (
    _fp_account,
    _intent,
    _phase29_plan,
    _strategy_context,
)
from tests.test_phase31_universal_pre_submit_firewall import (
    NOW_MS,
    _fp_adapter,
    _market_policy,
    _supervision,
)
from tests.test_phase34_canary_deployment import FakeCanaryExecutionAdapter
from tradingagents.brokers.execution import BrokerExecutionOutcome
from tradingagents.ict import CSDEngine, LondresPhase6Engine, SMTEngine
from tradingagents.ict.phase30 import LondresPhase30UniversalExecutionAuthorizationEngine
from tradingagents.ict.phase31 import (
    LondresPhase31UniversalPreSubmitFirewallEngine,
    Phase31BrokerBinding,
)
from tradingagents.ict.phase32 import (
    LondresPhase32ExactlyOnceShadowCommandEngine,
    SQLiteExecutionAuthorizationLedger,
)
from tradingagents.ict.phase34 import (
    LondresPhase34CanaryDeploymentEngine,
    Phase34CanaryBinding,
    Phase34CanaryLedger,
    Phase34CanaryPolicy,
)


def _signal(end):
    bars = _bullish_csd_bars(with_iofc=True)
    peer = bars.copy()
    # NQ raids the low while ES/YM hold it; CSD and a NEW IOFC follow.
    peer.iloc[4:6, peer.columns.get_loc("low")] = 8.2
    engine = LondresPhase6Engine()
    engine.csd = CSDEngine(pivot_span=1)
    engine.smt = SMTEngine(pivot_span=1)
    return engine.analyze(
        timeframe_bars={"1D": bars, "4H": bars, "5m": bars},
        intraday_bars=bars,
        minute_bars=bars,
        csd_bars=bars,
        csd_timeframe="5m",
        smt_bars={"NQ": bars, "ES": peer, "YM": peer},
        smt_group="US_INDEX",
        smt_timeframe="5m",
        as_of=bars.index[end - 1],
    )["execution_gate"]


def _prepare(tmp_path, *, end=9, **market_changes):
    intent = _intent()
    strategy = _strategy_context()
    strategy["execution_gate"] = _signal(end)
    account = _fp_account()
    account["phase23_account_plan"]["volume_unit"] = "units"
    authorization = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=intent, strategy_context=strategy, phase29_plan=_phase29_plan(account)
    )
    firewall = LondresPhase31UniversalPreSubmitFirewallEngine().revalidate(
        intent=intent,
        phase30_plan=authorization,
        bindings=(Phase31BrokerBinding("FP-LIVE", _fp_adapter(**market_changes)),),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )
    ledger = SQLiteExecutionAuthorizationLedger(tmp_path / "session.sqlite")
    shadow = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=intent, phase31_plan=firewall, ledger=ledger, now_ms=NOW_MS
    )
    return strategy, authorization, firewall, shadow, ledger


@pytest.mark.parametrize(
    ("end", "state"), [(5, "SMT_DETECTED_WAIT_CSD"), (7, "SMT_DETECTED_WAIT_IOF")]
)
def test_incomplete_signal_cannot_reserve_an_execution_command(tmp_path, end, state):
    strategy, authorization, firewall, shadow, ledger = _prepare(tmp_path, end=end)
    assert strategy["execution_gate"]["state"] == state
    assert authorization["order_authorized"] is False
    assert firewall["pre_submit_ready"] is False
    assert shadow["shadow_ready_accounts"] == 0
    assert ledger.count() == 0


@pytest.mark.parametrize("market_changes", [
    {"quote_timestamp_ms": NOW_MS - 60_000},
    {"equity": 10_000.0},  # unchanged 600 cash risk would now exceed 3%
])
def test_changed_market_or_equity_blocks_before_reservation(tmp_path, market_changes):
    _, authorization, firewall, shadow, ledger = _prepare(tmp_path, **market_changes)
    assert authorization["order_authorized"] is True
    assert firewall["pre_submit_ready"] is False
    assert shadow["shadow_ready_accounts"] == 0
    assert ledger.count() == 0


@pytest.mark.parametrize("outcome", [
    BrokerExecutionOutcome.ACKNOWLEDGED,
    BrokerExecutionOutcome.AMBIGUOUS,
])
def test_valid_signal_flows_through_risk_ledger_and_one_use_canary(tmp_path, outcome):
    strategy, authorization, firewall, shadow, ledger = _prepare(tmp_path)
    assert strategy["execution_gate"]["state"] == "SMT_VALIDATED"
    assert authorization["order_authorized"] is True
    assert firewall["pre_submit_ready"] is True, firewall
    assert shadow["shadow_ready_accounts"] == 1, shadow
    command = shadow["accounts"][0]["command"]
    assert command["exact_volume"] == 60.0
    assert command["phase30_authorization_fingerprint"] == (
        authorization["accounts"][0]["authorization_fingerprint"]
    )
    engine = LondresPhase34CanaryDeploymentEngine()
    disabled = FakeCanaryExecutionAdapter(execution_enabled=False)
    policy = Phase34CanaryPolicy(
        target_account_alias="FP-LIVE", execution_enabled=False,
        max_phase31_snapshot_age_ms=5_000,
    )
    observed = engine.observe(
        phase32_plan=shadow, phase32_ledger=ledger,
        binding=Phase34CanaryBinding("FP-LIVE", disabled),
        policy=policy, now_ms=NOW_MS + 100,
    )
    assert observed["status"] == "READY_FOR_CANARY_ARMING", observed
    assert disabled.submit_calls == disabled.reconcile_calls == 0

    # Reopen the durable ledger, as a restarted process would.
    reopened = SQLiteExecutionAuthorizationLedger(tmp_path / "session.sqlite")
    armed = FakeCanaryExecutionAdapter(execution_enabled=True, submit_outcome=outcome)
    inputs = {
        "phase32_plan": shadow, "phase32_ledger": reopened,
        "binding": Phase34CanaryBinding("FP-LIVE", armed),
        "policy": replace(policy, execution_enabled=True),
        "canary_token": observed["canary_token"],
    }
    result = engine.activate(**inputs, now_ms=NOW_MS + 200)
    acknowledged = outcome is BrokerExecutionOutcome.ACKNOWLEDGED
    assert result["canary_acknowledged"] is acknowledged, result
    assert result["replication_expansion_enabled"] is False
    assert armed.submit_calls == armed.reconcile_calls == 1
    state = Phase34CanaryLedger(reopened).inspect(observed["canary_token"])["state"]
    assert state == ("COMPLETED" if acknowledged else "RECONCILIATION_REQUIRED")
    repeated = engine.activate(**inputs, now_ms=NOW_MS + 300)
    assert repeated["canary_acknowledged"] is False
    assert armed.submit_calls == 1
