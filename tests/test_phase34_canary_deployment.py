from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tradingagents.brokers.contracts import BrokerType, OrchestrationPolicy, TradeIntent
from tradingagents.brokers.execution import (
    BrokerExecutionCapabilities,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)
from tradingagents.ict.phase32 import ExecutionCommand, SQLiteExecutionAuthorizationLedger
from tradingagents.ict.phase34 import (
    LondresPhase34CanaryDeploymentEngine,
    Phase34AttestationState,
    Phase34CanaryBinding,
    Phase34CanaryLedger,
    Phase34CanaryPolicy,
    Phase34CanaryStatus,
)


class FakeCanaryExecutionAdapter:
    def __init__(
        self,
        *,
        execution_enabled: bool,
        reconcile_outcome: BrokerExecutionOutcome = BrokerExecutionOutcome.NOT_FOUND,
        submit_outcome: BrokerExecutionOutcome = BrokerExecutionOutcome.ACKNOWLEDGED,
        adapter_id: str = "fp-canary-execution",
    ) -> None:
        self._execution_enabled = execution_enabled
        self._reconcile_outcome = reconcile_outcome
        self._submit_outcome = submit_outcome
        self._adapter_id = adapter_id
        self.reconcile_calls = 0
        self.submit_calls = 0

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.CTRADER

    @property
    def venue(self) -> str:
        return "FP_MARKETS_CTRADER"

    def execution_capabilities(self) -> BrokerExecutionCapabilities:
        return BrokerExecutionCapabilities(
            broker_type=self.broker_type,
            venue=self.venue,
            execution_enabled=self._execution_enabled,
            supports_market_orders=True,
            supports_server_side_stop=True,
            supports_server_side_target=True,
            supports_reconciliation=True,
        )

    def reconcile(
        self,
        command: dict[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        self.reconcile_calls += 1
        return self._receipt(command, outcome=self._reconcile_outcome, now_ms=now_ms)

    def submit_market(
        self,
        command: dict[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        self.submit_calls += 1
        acknowledged = self._submit_outcome is BrokerExecutionOutcome.ACKNOWLEDGED
        return self._receipt(
            command,
            outcome=self._submit_outcome,
            now_ms=now_ms,
            filled_volume=float(command["exact_volume"]) if acknowledged else None,
            stop_active=acknowledged,
            target_active=acknowledged,
            definite_no_fill=self._submit_outcome is BrokerExecutionOutcome.REJECTED,
        )

    def _receipt(
        self,
        command: dict[str, Any],
        *,
        outcome: BrokerExecutionOutcome,
        now_ms: int,
        filled_volume: float | None = None,
        stop_active: bool = False,
        target_active: bool = False,
        definite_no_fill: bool = False,
    ) -> BrokerExecutionReceipt:
        return BrokerExecutionReceipt(
            command_id=str(command["command_id"]),
            account_alias=str(command["account_alias"]),
            venue=self.venue,
            broker_type=self.broker_type,
            client_order_label=str(command["client_order_label"]),
            outcome=outcome,
            broker_order_id=("ORDER-1" if outcome is BrokerExecutionOutcome.ACKNOWLEDGED else None),
            broker_position_id=(
                "POSITION-1" if outcome is BrokerExecutionOutcome.ACKNOWLEDGED else None
            ),
            filled_volume=filled_volume,
            average_fill_price=(float(command["current_executable_price"]) if filled_volume else None),
            stop_protection_active=stop_active,
            target_protection_active=target_active,
            provider_code=f"FAKE_{outcome.value}",
            submitted_at_ms=now_ms,
            acknowledged_at_ms=(now_ms if outcome is BrokerExecutionOutcome.ACKNOWLEDGED else None),
            definite_no_fill=definite_no_fill,
        )


def _command(*, alias: str, risk: float = 0.03, quote_timestamp_ms: int = 1_000_000) -> ExecutionCommand:
    intent = TradeIntent(
        trade_id="LONDRES-P34-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25_000.0,
        stop_price=24_990.0,
        target_price=25_050.0,
        selected_exit_mode="FULL_AT_SD_2",
    )
    return ExecutionCommand.build(
        intent=intent,
        account={
            "account_alias": alias,
            "venue": "FP_MARKETS_CTRADER",
            "broker_type": "CTRADER",
            "broker_symbol": "US100",
            "prepared_volume": 40.0,
            "volume_unit": "units",
            "selected_risk_fraction": risk,
            "current_executable_price": 25_001.0,
            "phase30_authorization_fingerprint": "a" * 64,
            "pre_submit_snapshot_fingerprint": "b" * 64,
            "quote_timestamp_ms": quote_timestamp_ms,
        },
    )


def _plan_and_ledger(
    tmp_path: Path,
    *,
    risk: float = 0.03,
    quote_timestamp_ms: int = 1_000_000,
    second_account: bool = False,
) -> tuple[dict[str, Any], SQLiteExecutionAuthorizationLedger, ExecutionCommand]:
    command = _command(alias="FP-CANARY", risk=risk, quote_timestamp_ms=quote_timestamp_ms)
    ledger = SQLiteExecutionAuthorizationLedger(tmp_path / "phase34.sqlite")
    reservations = ledger.reserve_shadow(
        commands=(command,),
        policy=OrchestrationPolicy.BEST_EFFORT,
        now_ms=quote_timestamp_ms,
    )
    assert reservations[0].ledger_mutated is True
    accounts = [
        {
            "account_alias": command.account_alias,
            "venue": command.venue,
            "broker_type": command.broker_type,
            "status": "SHADOW_READY",
            "command": command.to_dict(),
            "phase30_authorization_fingerprint": command.phase30_authorization_fingerprint,
            "phase31_pre_submit_fingerprint": command.phase31_pre_submit_fingerprint,
            "ledger_state": "SHADOW_READY",
            "authorization_reserved": True,
            "authorization_consumed": False,
            "shadow_ready": True,
            "execution_handoff_ready": False,
            "execution_enabled": False,
            "order_submission_enabled": False,
            "broker_order_placed": False,
            "reason_codes": [],
        }
    ]
    if second_account:
        accounts.append(
            {
                "account_alias": "OTHER-ACCOUNT",
                "venue": "VANTAGE_MT5",
                "broker_type": "MT5",
                "status": "SKIPPED_DISABLED",
                "command": None,
                "authorization_reserved": False,
                "shadow_ready": False,
            }
        )
    plan = {
        "phase": "LONDRES_PHASE32_EXACTLY_ONCE_SHADOW_EXECUTION_COMMANDS",
        "trade_id": command.trade_id,
        "canonical_symbol": command.canonical_symbol,
        "direction": command.direction,
        "policy": "BEST_EFFORT",
        "status": "READY",
        "accounts": accounts,
        "enabled_accounts": 1,
        "shadow_ready_accounts": 1,
        "blocked_accounts": 0,
        "skipped_accounts": 1 if second_account else 0,
        "shadow_ready": True,
        "authorization_consumed": False,
        "execution_handoff_ready": False,
        "order_authorized": False,
        "execution_enabled": False,
        "order_submission_enabled": False,
        "broker_order_placed": False,
        "reason_codes": [],
    }
    return plan, ledger, command


def _policy(*, execution_enabled: bool, max_risk: float = 0.03) -> Phase34CanaryPolicy:
    return Phase34CanaryPolicy(
        target_account_alias="FP-CANARY",
        max_phase31_snapshot_age_ms=10_000,
        observation_ttl_ms=60_000,
        max_canary_risk_fraction=max_risk,
        execution_enabled=execution_enabled,
    )


def test_observe_is_execution_disabled_and_persists_one_use_attestation(tmp_path: Path) -> None:
    plan, ledger, command = _plan_and_ledger(tmp_path)
    adapter = FakeCanaryExecutionAdapter(execution_enabled=False)
    result = LondresPhase34CanaryDeploymentEngine().observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=adapter),
        policy=_policy(execution_enabled=False),
        now_ms=1_005_000,
    )

    assert result["status"] == Phase34CanaryStatus.READY_FOR_CANARY_ARMING.value
    assert result["command_id"] == command.command_id
    assert result["execution_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["replication_expansion_enabled"] is False
    assert adapter.reconcile_calls == 0
    assert adapter.submit_calls == 0
    stored = Phase34CanaryLedger(ledger).inspect(result["canary_token"])
    assert stored is not None
    assert stored["state"] == Phase34AttestationState.OBSERVED.value
    assert stored["integrity_valid"] is True
    assert "DEMO" not in json.dumps(result).upper()
    assert "LIVE" not in json.dumps(result).upper()


def test_observe_blocks_if_execution_adapter_is_already_armed(tmp_path: Path) -> None:
    plan, ledger, _ = _plan_and_ledger(tmp_path)
    adapter = FakeCanaryExecutionAdapter(execution_enabled=True)
    result = LondresPhase34CanaryDeploymentEngine().observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=adapter),
        policy=_policy(execution_enabled=False),
        now_ms=1_005_000,
    )
    assert result["status"] == Phase34CanaryStatus.BLOCKED_BINDING.value
    assert "CANARY_OBSERVATION_REQUIRES_EXECUTION_ADAPTER_DISABLED" in result["reason_codes"]
    assert adapter.submit_calls == 0


def test_canary_activation_probes_then_submits_exactly_once(tmp_path: Path) -> None:
    plan, ledger, command = _plan_and_ledger(tmp_path, second_account=True)
    observe_adapter = FakeCanaryExecutionAdapter(execution_enabled=False)
    engine = LondresPhase34CanaryDeploymentEngine()
    observed = engine.observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=observe_adapter),
        policy=_policy(execution_enabled=False),
        now_ms=1_004_000,
    )

    armed_adapter = FakeCanaryExecutionAdapter(execution_enabled=True)
    activated = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=armed_adapter),
        policy=_policy(execution_enabled=True),
        canary_token=observed["canary_token"],
        now_ms=1_005_000,
    )

    assert activated["status"] == Phase34CanaryStatus.ACTIVATION_ACKNOWLEDGED.value
    assert activated["canary_acknowledged"] is True
    assert activated["replication_expansion_enabled"] is False
    assert armed_adapter.reconcile_calls == 1
    assert armed_adapter.submit_calls == 1
    assert ledger.inspect(command.command_id)["state"] == "ACKNOWLEDGED"
    stored = Phase34CanaryLedger(ledger).inspect(observed["canary_token"])
    assert stored["state"] == Phase34AttestationState.COMPLETED.value

    repeated = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=armed_adapter),
        policy=_policy(execution_enabled=True),
        canary_token=observed["canary_token"],
        now_ms=1_006_000,
    )
    assert repeated["canary_acknowledged"] is False
    assert armed_adapter.submit_calls == 1


def test_activation_blocks_when_reconciliation_finds_existing_broker_evidence(tmp_path: Path) -> None:
    plan, ledger, _ = _plan_and_ledger(tmp_path)
    engine = LondresPhase34CanaryDeploymentEngine()
    observed = engine.observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(
            account_alias="FP-CANARY",
            adapter=FakeCanaryExecutionAdapter(execution_enabled=False),
        ),
        policy=_policy(execution_enabled=False),
        now_ms=1_004_000,
    )
    armed = FakeCanaryExecutionAdapter(
        execution_enabled=True,
        reconcile_outcome=BrokerExecutionOutcome.ACKNOWLEDGED,
    )
    result = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=armed),
        policy=_policy(execution_enabled=True),
        canary_token=observed["canary_token"],
        now_ms=1_005_000,
    )
    assert result["status"] == Phase34CanaryStatus.BLOCKED_RECONCILIATION_PROBE.value
    assert armed.reconcile_calls == 1
    assert armed.submit_calls == 0
    assert Phase34CanaryLedger(ledger).inspect(observed["canary_token"])["state"] == "OBSERVED"


def test_observation_blocks_risk_above_canary_ceiling_without_resizing(tmp_path: Path) -> None:
    plan, ledger, command = _plan_and_ledger(tmp_path, risk=0.05)
    adapter = FakeCanaryExecutionAdapter(execution_enabled=False)
    result = LondresPhase34CanaryDeploymentEngine().observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=adapter),
        policy=_policy(execution_enabled=False, max_risk=0.03),
        now_ms=1_005_000,
    )
    assert result["status"] == Phase34CanaryStatus.BLOCKED_RISK.value
    assert command.exact_volume == 40.0
    assert adapter.submit_calls == 0


def test_observation_blocks_stale_phase31_snapshot(tmp_path: Path) -> None:
    plan, ledger, _ = _plan_and_ledger(tmp_path, quote_timestamp_ms=900_000)
    result = LondresPhase34CanaryDeploymentEngine().observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(
            account_alias="FP-CANARY",
            adapter=FakeCanaryExecutionAdapter(execution_enabled=False),
        ),
        policy=_policy(execution_enabled=False),
        now_ms=1_005_000,
    )
    assert result["status"] == Phase34CanaryStatus.BLOCKED_STALE_COMMAND.value


def test_activation_requires_matching_adapter_identity_and_explicit_enablement(tmp_path: Path) -> None:
    plan, ledger, _ = _plan_and_ledger(tmp_path)
    engine = LondresPhase34CanaryDeploymentEngine()
    observed = engine.observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(
            account_alias="FP-CANARY",
            adapter=FakeCanaryExecutionAdapter(execution_enabled=False),
        ),
        policy=_policy(execution_enabled=False),
        now_ms=1_004_000,
    )

    disabled = FakeCanaryExecutionAdapter(execution_enabled=False)
    disabled_result = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=disabled),
        policy=_policy(execution_enabled=False),
        canary_token=observed["canary_token"],
        now_ms=1_005_000,
    )
    assert "PHASE34_EXPLICIT_EXECUTION_ENABLEMENT_REQUIRED" in disabled_result["reason_codes"]
    assert disabled.submit_calls == 0

    wrong = FakeCanaryExecutionAdapter(execution_enabled=True, adapter_id="different-adapter")
    wrong_result = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=wrong),
        policy=_policy(execution_enabled=True),
        canary_token=observed["canary_token"],
        now_ms=1_005_000,
    )
    assert any("ADAPTER_ID" in reason for reason in wrong_result["reason_codes"])
    assert wrong.submit_calls == 0


def test_ambiguous_canary_is_locked_for_reconciliation_not_retried(tmp_path: Path) -> None:
    plan, ledger, _ = _plan_and_ledger(tmp_path)
    engine = LondresPhase34CanaryDeploymentEngine()
    observed = engine.observe(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(
            account_alias="FP-CANARY",
            adapter=FakeCanaryExecutionAdapter(execution_enabled=False),
        ),
        policy=_policy(execution_enabled=False),
        now_ms=1_004_000,
    )
    armed = FakeCanaryExecutionAdapter(
        execution_enabled=True,
        submit_outcome=BrokerExecutionOutcome.AMBIGUOUS,
    )
    result = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=armed),
        policy=_policy(execution_enabled=True),
        canary_token=observed["canary_token"],
        now_ms=1_005_000,
    )
    assert result["status"] == Phase34CanaryStatus.ACTIVATION_RECONCILIATION_REQUIRED.value
    assert armed.submit_calls == 1
    assert Phase34CanaryLedger(ledger).inspect(observed["canary_token"])["state"] == (
        Phase34AttestationState.RECONCILIATION_REQUIRED.value
    )

    repeated = engine.activate(
        phase32_plan=plan,
        phase32_ledger=ledger,
        binding=Phase34CanaryBinding(account_alias="FP-CANARY", adapter=armed),
        policy=_policy(execution_enabled=True),
        canary_token=observed["canary_token"],
        now_ms=1_006_000,
    )
    assert armed.submit_calls == 1
    assert repeated["canary_acknowledged"] is False


def test_policy_accepts_only_londres_risk_tiers() -> None:
    with pytest.raises(ValueError, match="3/5/10"):
        Phase34CanaryPolicy(
            target_account_alias="FP-CANARY",
            max_phase31_snapshot_age_ms=10_000,
            max_canary_risk_fraction=0.04,
        )
