from __future__ import annotations

import json
import sqlite3

from tradingagents.brokers import OrchestrationPolicy, TradeIntent
from tradingagents.ict.phase32 import (
    LondresPhase32ExactlyOnceShadowCommandEngine,
    Phase32AccountStatus,
    SQLiteExecutionAuthorizationLedger,
)

NOW_MS = 1_800_000_000_000


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LONDRES-P32-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25_000.0,
        stop_price=24_990.0,
        target_price=25_050.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _account(
    *,
    alias: str,
    venue: str,
    broker_type: str,
    broker_symbol: str,
    volume: float,
    unit: str,
    phase30_char: str,
    phase31_char: str,
    executable: float = 25_000.0,
) -> dict:
    return {
        "account_alias": alias,
        "venue": venue,
        "broker_type": broker_type,
        "status": "READY_FOR_EXECUTION_ADAPTER_HANDOFF",
        "trade_id": "LONDRES-P32-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "broker_symbol": broker_symbol,
        "prepared_volume": volume,
        "volume_unit": unit,
        "selected_risk_fraction": 0.03,
        "phase30_authorization_fingerprint": phase30_char * 64,
        "pre_submit_snapshot_fingerprint": phase31_char * 64,
        "current_executable_price": executable,
        "quote_timestamp_ms": NOW_MS - 500,
        "pre_submit_ready": True,
        "order_authorized": True,
    }


def _ninja(**overrides) -> dict:
    values = {
        "alias": "NT-ALPHA",
        "venue": "NINJATRADER",
        "broker_type": "NINJATRADER",
        "broker_symbol": "NQ 12-26",
        "volume": 2.0,
        "unit": "contracts",
        "phase30_char": "a",
        "phase31_char": "d",
    }
    values.update(overrides)
    return _account(**values)


def _fp(**overrides) -> dict:
    values = {
        "alias": "FP-ALPHA",
        "venue": "FP_MARKETS_CTRADER",
        "broker_type": "CTRADER",
        "broker_symbol": "US100",
        "volume": 40.0,
        "unit": "units",
        "phase30_char": "b",
        "phase31_char": "e",
    }
    values.update(overrides)
    return _account(**values)


def _vantage(**overrides) -> dict:
    values = {
        "alias": "MT5-ALPHA",
        "venue": "VANTAGE_MT5",
        "broker_type": "MT5",
        "broker_symbol": "NAS100",
        "volume": 20.0,
        "unit": "lots",
        "phase30_char": "c",
        "phase31_char": "f",
    }
    values.update(overrides)
    return _account(**values)


def _plan(
    *accounts: dict,
    policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
) -> dict:
    return {
        "phase": "LONDRES_PHASE31_UNIVERSAL_PRE_SUBMIT_FIREWALL",
        "trade_id": "LONDRES-P32-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "policy": policy.value,
        "status": "READY",
        "pre_submit_ready": True,
        "order_authorized": True,
        "order_submission_enabled": False,
        "accounts": list(accounts),
    }


def _ledger(tmp_path, name: str = "phase32.sqlite") -> SQLiteExecutionAuthorizationLedger:
    return SQLiteExecutionAuthorizationLedger(tmp_path / name)


def test_phase32_reserves_three_venues_without_submission(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    result = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja(), _fp(), _vantage()),
        ledger=ledger,
        now_ms=NOW_MS,
    )

    assert result["status"] == "READY"
    assert result["shadow_ready_accounts"] == 3
    assert result["ledger_backend"] == "SQLITE_DURABLE"
    assert result["ledger_mutated"] is True
    assert result["shadow_ready"] is True
    assert result["authorization_consumed"] is False
    assert result["execution_handoff_ready"] is False
    assert result["order_authorized"] is False
    assert result["execution_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
    assert ledger.count() == 3

    by_venue = {item["venue"]: item for item in result["accounts"]}
    assert by_venue["NINJATRADER"]["command"]["exact_volume"] == 2.0
    assert by_venue["FP_MARKETS_CTRADER"]["command"]["exact_volume"] == 40.0
    assert by_venue["VANTAGE_MT5"]["command"]["exact_volume"] == 20.0
    assert all(item["authorization_reserved"] for item in result["accounts"])
    assert all(item["authorized_volume_resized"] is False for item in result["accounts"])


def test_identical_retry_is_idempotent_and_command_id_is_deterministic(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    engine = LondresPhase32ExactlyOnceShadowCommandEngine()
    first = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja()),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    second = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja()),
        ledger=ledger,
        now_ms=NOW_MS + 5_000,
    )

    first_account = first["accounts"][0]
    second_account = second["accounts"][0]
    assert first_account["command"]["command_id"] == second_account["command"]["command_id"]
    assert second_account["status"] == "IDEMPOTENT_SHADOW_READY"
    assert second_account["authorization_reserved"] is True
    assert second["ledger_mutated"] is False
    assert ledger.count() == 1


def test_ledger_survives_process_style_reopen(tmp_path) -> None:
    path = tmp_path / "durable.sqlite"
    first_ledger = SQLiteExecutionAuthorizationLedger(path)
    result = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja()),
        ledger=first_ledger,
        now_ms=NOW_MS,
    )
    command_id = result["accounts"][0]["command"]["command_id"]

    reopened = SQLiteExecutionAuthorizationLedger(path)
    inspected = reopened.inspect(command_id)
    assert reopened.count() == 1
    assert inspected is not None
    assert inspected["integrity_valid"] is True
    assert inspected["state"] == "SHADOW_READY"
    assert inspected["command"]["command_id"] == command_id


def test_phase30_authorization_cannot_be_reused_for_different_command(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    engine = LondresPhase32ExactlyOnceShadowCommandEngine()
    first = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja()),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    changed = _ninja(phase31_char="e", executable=25_000.25)
    second = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(changed),
        ledger=ledger,
        now_ms=NOW_MS + 1_000,
    )

    assert first["status"] == "READY"
    assert second["status"] == "BLOCKED"
    assert second["accounts"][0]["status"] == "BLOCKED_LEDGER_CONFLICT"
    assert (
        "AUTHORIZATION_OR_SNAPSHOT_RESERVED_BY_DIFFERENT_COMMAND"
        in second["accounts"][0]["reason_codes"]
    )
    assert ledger.count() == 1


def test_best_effort_reserves_unaffected_account_when_other_conflicts(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    engine = LondresPhase32ExactlyOnceShadowCommandEngine()
    seed = _fp(alias="FP-SEED", phase31_char="c")
    engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(seed),
        ledger=ledger,
        now_ms=NOW_MS,
    )

    result = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja(), _fp(phase31_char="e")),
        ledger=ledger,
        now_ms=NOW_MS + 1_000,
    )
    by_alias = {item["account_alias"]: item for item in result["accounts"]}
    assert result["status"] == "PARTIAL_READY"
    assert by_alias["NT-ALPHA"]["status"] == "SHADOW_READY"
    assert by_alias["FP-ALPHA"]["status"] == "BLOCKED_LEDGER_CONFLICT"
    assert ledger.count() == 2


def test_all_or_none_conflict_rolls_back_new_reservations(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    engine = LondresPhase32ExactlyOnceShadowCommandEngine()
    seed = _fp(alias="FP-SEED", phase31_char="c")
    engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(seed),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    assert ledger.count() == 1

    result = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(
            _ninja(),
            _fp(phase31_char="e"),
            policy=OrchestrationPolicy.ALL_OR_NONE,
        ),
        ledger=ledger,
        now_ms=NOW_MS + 1_000,
    )
    by_alias = {item["account_alias"]: item for item in result["accounts"]}
    assert result["status"] == "BLOCKED"
    assert result["ledger_mutated"] is False
    assert by_alias["NT-ALPHA"]["status"] == "BLOCKED_BATCH_POLICY"
    assert by_alias["FP-ALPHA"]["status"] == "BLOCKED_LEDGER_CONFLICT"
    assert ledger.count() == 1


def test_invalid_phase31_volume_blocks_before_ledger_mutation(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    bad = _ninja(volume=0.0)
    result = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_plan(bad),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == "BLOCKED_PHASE31"
    assert "POSITIVE_EXACT_PHASE31_VOLUME_REQUIRED" in account["reason_codes"]
    assert ledger.count() == 0


def test_public_command_keeps_demo_live_environment_private(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    result = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja(), _vantage()),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    serialized = json.dumps(result, sort_keys=True)
    assert result["account_environment"] == "HIDDEN_INTERNAL"
    assert all(item["account_environment"] == "HIDDEN_INTERNAL" for item in result["accounts"])
    assert "trade_mode" not in serialized
    assert "server" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert '"DEMO"' not in serialized
    assert '"LIVE"' not in serialized
    assert '"REAL"' not in serialized


def test_ledger_inspection_detects_payload_corruption(tmp_path) -> None:
    path = tmp_path / "corrupt.sqlite"
    ledger = SQLiteExecutionAuthorizationLedger(path)
    result = LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja()),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    command_id = result["accounts"][0]["command"]["command_id"]

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE execution_commands SET command_json = command_json || ' ' WHERE command_id = ?",
            (command_id,),
        )

    inspected = ledger.inspect(command_id)
    assert inspected is not None
    assert inspected["integrity_valid"] is False
    assert inspected["command"] is None
    assert "RECONCILIATION_REQUIRED" in inspected["reason"]


def test_reserved_phase30_fingerprints_can_feed_phase31_replay_defense(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    LondresPhase32ExactlyOnceShadowCommandEngine().prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja(), _fp()),
        ledger=ledger,
        now_ms=NOW_MS,
    )
    assert ledger.reserved_phase30_fingerprints() == {"a" * 64, "b" * 64}


def test_phase32_has_no_execution_surface(tmp_path) -> None:
    engine = LondresPhase32ExactlyOnceShadowCommandEngine()
    for name in (
        "place_order",
        "submit_order",
        "order_send",
        "amend_order",
        "cancel_order",
        "close_position",
        "flatten",
    ):
        assert not hasattr(engine, name)

    result = engine.prepare(
        intent=_intent(),
        phase31_plan=_plan(_ninja()),
        ledger=_ledger(tmp_path),
        now_ms=NOW_MS,
    )
    assert result["execution_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
    assert result["authorization_consumed"] is False
    assert result["accounts"][0]["status"] in {
        Phase32AccountStatus.SHADOW_READY.value,
        Phase32AccountStatus.IDEMPOTENT_SHADOW_READY.value,
    }
