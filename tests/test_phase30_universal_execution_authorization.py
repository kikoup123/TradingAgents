from copy import deepcopy

from tradingagents.brokers import OrchestrationPolicy, TradeIntent
from tradingagents.ict.phase30 import (
    LondresPhase30UniversalExecutionAuthorizationEngine,
    Phase30AccountStatus,
)


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LONDRES-UNIVERSAL-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25_000.0,
        stop_price=24_990.0,
        target_price=25_050.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _strategy_context(**gate_overrides) -> dict:
    gate = {
        "smt_detected": True,
        "csd_confirmed_after_smt": True,
        "post_csd_iofc_confirmed": True,
        "smt_validated": True,
        "direction": "BULLISH",
    }
    gate.update(gate_overrides)
    return {
        "execution_gate": gate,
        "entry_execution": {
            "status": "ENTRY_TRIGGERED",
            "direction": "BULLISH",
            "exact_entry_price": 25_000.0,
        },
        "executable_stop": {
            "status": "READY",
            "direction": "BULLISH",
            "executable_stop_price": 24_990.0,
        },
        "trade_calculation": {
            "status": "READY",
            "direction": "BULLISH",
            "symbol": "NQ",
            "selected_exit_mode": "FULL_AT_SD_2",
            "selected_target_price": 25_050.0,
            "position_volume": 999.0,
        },
        "pre_broker_validation": {
            "status": "AUTHORIZED",
            "direction": "BULLISH",
            "symbol": "NQ",
            "order_authorized": True,
        },
    }


def _phase27_account() -> dict:
    return {
        "account_alias": "NT-ALPHA",
        "status": "AUTHORIZED_FOR_EXECUTION_HANDOFF",
        "trade_id": "LONDRES-UNIVERSAL-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "selected_root": "NQ",
        "active_contract": "NQ 12-26",
        "contract_quantity": 2,
        "entry_price": 25_000.0,
        "stop_price": 24_990.0,
        "target_price": 25_050.0,
        "selected_exit_mode": "FULL_AT_SD_2",
        "execution_handoff_ready": True,
        "order_authorized": True,
        "authorization_fingerprint": "a" * 64,
        "phase26_account_plan": {
            "policy_state": {"risk_fraction": 0.03},
            "phase24_account_plan": {
                "phase23_account_plan": {
                    "account_equity": 30_000.0,
                    "projected_cash_risk": 600.0,
                    "projected_equity_risk_fraction": 0.02,
                }
            },
        },
    }


def _phase27_plan(*accounts: dict) -> dict:
    return {
        "phase": "LONDRES_PHASE27_LIVE_ACCOUNT_EXECUTION_AUTHORIZATION",
        "trade_id": "LONDRES-UNIVERSAL-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "policy": "BEST_EFFORT",
        "status": "READY",
        "execution_handoff_ready": True,
        "order_authorized": True,
        "accounts": list(accounts),
    }


def _phase23_account(
    *,
    alias: str,
    broker_symbol: str,
    equity: float,
    volume: float,
    cash_risk: float,
    risk_fraction: float = 0.03,
) -> dict:
    return {
        "account_alias": alias,
        "broker_type": "CTRADER" if broker_symbol == "US100" else "MT5",
        "status": "READY_FOR_EXECUTION_ADAPTER",
        "trade_id": "LONDRES-UNIVERSAL-001",
        "canonical_symbol": "NASDAQ",
        "broker_symbol": broker_symbol,
        "account_currency": "USD",
        "account_equity": equity,
        "selected_risk_fraction": risk_fraction,
        "intended_entry_price": 25_000.0,
        "intended_stop_price": 24_990.0,
        "selected_target_price": 25_050.0,
        "selected_exit_mode": "FULL_AT_SD_2",
        "prepared_volume": volume,
        "volume_unit": "lots",
        "projected_cash_risk": cash_risk,
        "projected_equity_risk_fraction": cash_risk / equity,
        "preparation_ready": True,
        "order_authorized": False,
        "broker_order_placed": False,
    }


def _phase29_account(
    *,
    alias: str,
    venue: str,
    broker_type: str,
    broker_symbol: str,
    broker_name: str,
    equity: float,
    volume: float,
    cash_risk: float,
) -> dict:
    return {
        "account_alias": alias,
        "venue": venue,
        "broker_type": broker_type,
        "status": "READY",
        "trade_id": "LONDRES-UNIVERSAL-001",
        "canonical_symbol": "NASDAQ",
        "broker_symbol": broker_symbol,
        "broker_name": broker_name,
        "supervision_state": {
            "status": "HEALTHY",
            "account_alias": alias,
            "canonical_symbol": "NASDAQ",
            "broker_symbol": broker_symbol,
            "connected": True,
            "execution_data_ready": True,
        },
        "phase23_account_plan": _phase23_account(
            alias=alias,
            broker_symbol=broker_symbol,
            equity=equity,
            volume=volume,
            cash_risk=cash_risk,
        ),
        "preparation_ready": True,
    }


def _phase29_plan(*accounts: dict) -> dict:
    return {
        "phase": "LONDRES_PHASE29_FP_VANTAGE_READ_ONLY_BROKER_PARITY",
        "trade_id": "LONDRES-UNIVERSAL-001",
        "canonical_symbol": "NASDAQ",
        "policy": "BEST_EFFORT",
        "status": "READY",
        "batch_ready_for_future_execution": True,
        "accounts": list(accounts),
    }


def _fp_account() -> dict:
    return _phase29_account(
        alias="FP-LIVE",
        venue="FP_MARKETS_CTRADER",
        broker_type="CTRADER",
        broker_symbol="US100",
        broker_name="FP Markets",
        equity=20_000.0,
        volume=60.0,
        cash_risk=600.0,
    )


def _vantage_account() -> dict:
    return _phase29_account(
        alias="MT5-BETA",
        venue="VANTAGE_MT5",
        broker_type="MT5",
        broker_symbol="NAS100",
        broker_name="Vantage Global Prime",
        equity=10_000.0,
        volume=30.0,
        cash_risk=300.0,
    )


def test_phase30_authorizes_ninja_fp_and_vantage_under_one_batch() -> None:
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase27_plan=_phase27_plan(_phase27_account()),
        phase29_plan=_phase29_plan(_fp_account(), _vantage_account()),
    )

    assert result["status"] == "READY"
    assert result["authorized_accounts"] == 3
    assert result["execution_handoff_ready"] is True
    assert result["order_authorized"] is True
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
    assert result["strategy_gate"]["required_sequence"] == "SMT_DETECTED+CSD_CONFIRMED+IOF_ALIGNED"

    by_venue = {account["venue"]: account for account in result["accounts"]}
    assert by_venue["NINJATRADER"]["prepared_volume"] == 2.0
    assert by_venue["FP_MARKETS_CTRADER"]["prepared_volume"] == 60.0
    assert by_venue["VANTAGE_MT5"]["prepared_volume"] == 30.0
    assert by_venue["FP_MARKETS_CTRADER"]["selected_risk_fraction"] == 0.03
    assert by_venue["VANTAGE_MT5"]["selected_risk_fraction"] == 0.03
    fingerprints = [account["authorization_fingerprint"] for account in result["accounts"]]
    assert all(len(value) == 64 for value in fingerprints)
    assert len(set(fingerprints)) == 3
    assert by_venue["NINJATRADER"]["upstream_authorization_fingerprint"] == "a" * 64


def test_phase30_rejects_smt_without_csd_and_iof() -> None:
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(
            csd_confirmed_after_smt=False,
            post_csd_iofc_confirmed=False,
            smt_validated=False,
        ),
        phase29_plan=_phase29_plan(_fp_account()),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase30AccountStatus.BLOCKED_STRATEGY_GATE.value
    assert "CSD_CONFIRMED_AFTER_SMT_REQUIRED" in account["reason_codes"]
    assert "POST_CSD_IOF_ALIGNMENT_REQUIRED" in account["reason_codes"]
    assert account["authorization_fingerprint"] is None


def test_phase30_blocks_tampered_cfd_volume_or_geometry() -> None:
    fp = _fp_account()
    fp["phase23_account_plan"]["prepared_volume"] = 61.0
    fp["phase23_account_plan"]["intended_stop_price"] = 24_989.0
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(fp),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase30AccountStatus.BLOCKED_BROKER_ENVELOPE.value
    assert "PHASE23_STOP_DOES_NOT_MATCH_INTENT" in account["reason_codes"]
    assert account["authorization_fingerprint"] is None


def test_phase30_blocks_invalid_cfd_risk_tier_or_risk_overrun() -> None:
    vantage = _vantage_account()
    vantage["phase23_account_plan"]["selected_risk_fraction"] = 0.04
    vantage["phase23_account_plan"]["projected_equity_risk_fraction"] = 0.11
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(vantage),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert "PHASE23_RISK_FRACTION_MUST_BE_EXACTLY_3_5_OR_10_PERCENT" in account["reason_codes"]
    assert "PROJECTED_RISK_EXCEEDS_SELECTED_ACCOUNT_RISK_FRACTION" in account["reason_codes"]
    assert "PROJECTED_RISK_EXCEEDS_LONDRES_HARD_ACCOUNT_LIMIT" in account["reason_codes"]


def test_phase30_rejects_broker_identity_or_type_tamper() -> None:
    fp = _fp_account()
    fp["broker_name"] = "Other Broker"
    fp["broker_type"] = "MT5"
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(fp),
    )

    account = result["accounts"][0]
    assert account["status"] == Phase30AccountStatus.BLOCKED_BROKER_ENVELOPE.value
    assert "FP_MARKETS_PHASE30_REQUIRES_CTRADER" in account["reason_codes"]
    assert "PHASE30_BROKER_IDENTITY_REVERIFICATION_FAILED" in account["reason_codes"]


def test_phase30_rejects_malformed_phase27_fingerprint() -> None:
    ninja = _phase27_account()
    ninja["authorization_fingerprint"] = "not-a-sha256"
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase27_plan=_phase27_plan(ninja),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase30AccountStatus.BLOCKED_UPSTREAM_PLAN.value
    assert "VALID_PHASE27_AUTHORIZATION_FINGERPRINT_REQUIRED" in account["reason_codes"]


def test_phase30_best_effort_isolates_one_bad_broker_account() -> None:
    vantage = _vantage_account()
    vantage["supervision_state"]["execution_data_ready"] = False
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(_fp_account(), vantage),
    )

    assert result["status"] == "PARTIAL_READY"
    assert result["authorized_accounts"] == 1
    assert result["blocked_accounts"] == 1
    fp, blocked = result["accounts"]
    assert fp["order_authorized"] is True
    assert blocked["order_authorized"] is False


def test_phase30_all_or_none_revokes_every_otherwise_authorized_venue() -> None:
    vantage = _vantage_account()
    vantage["supervision_state"]["execution_data_ready"] = False
    result = LondresPhase30UniversalExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase27_plan=_phase27_plan(_phase27_account()),
        phase29_plan=_phase29_plan(_fp_account(), vantage),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )

    assert result["status"] == "BLOCKED"
    assert result["authorized_accounts"] == 0
    assert result["order_authorized"] is False
    assert result["accounts"][0]["status"] == Phase30AccountStatus.BLOCKED_BATCH_POLICY.value
    assert result["accounts"][1]["status"] == Phase30AccountStatus.BLOCKED_BATCH_POLICY.value
    assert result["accounts"][0]["authorization_fingerprint"] is None
    assert result["accounts"][1]["authorization_fingerprint"] is None


def test_phase30_fingerprint_changes_when_exact_account_volume_changes() -> None:
    engine = LondresPhase30UniversalExecutionAuthorizationEngine()
    first = engine.authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(_fp_account()),
    )
    changed = _fp_account()
    changed["phase23_account_plan"]["prepared_volume"] = 59.0
    second = engine.authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(changed),
    )
    assert first["accounts"][0]["authorization_fingerprint"] != second["accounts"][0]["authorization_fingerprint"]


def test_phase30_has_no_execution_surface() -> None:
    engine = LondresPhase30UniversalExecutionAuthorizationEngine()
    assert not hasattr(engine, "place_order")
    assert not hasattr(engine, "submit_order")
    assert not hasattr(engine, "amend_order")
    assert not hasattr(engine, "cancel_order")
    assert not hasattr(engine, "flatten")
    result = engine.authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase29_plan=_phase29_plan(_fp_account()),
    )
    assert result["execution_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
