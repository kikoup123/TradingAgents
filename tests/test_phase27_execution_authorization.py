from tradingagents.brokers import OrchestrationPolicy, TradeIntent
from tradingagents.ict.phase27 import (
    LondresPhase27ExecutionAuthorizationEngine,
    Phase27AccountStatus,
)


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LONDRES-NQ-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25000.0,
        stop_price=24990.0,
        target_price=25050.0,
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
            "exact_entry_price": 25000.0,
        },
        "executable_stop": {
            "status": "READY",
            "direction": "BULLISH",
            "executable_stop_price": 24990.0,
        },
        "trade_calculation": {
            "status": "READY",
            "direction": "BULLISH",
            "symbol": "NQ",
            "selected_exit_mode": "FULL_AT_SD_2",
            "selected_target_price": 25050.0,
            "position_volume": 99.0,
        },
        "pre_broker_validation": {
            "status": "AUTHORIZED",
            "direction": "BULLISH",
            "symbol": "NQ",
            "order_authorized": True,
        },
    }


def _account(
    alias: str,
    *,
    status: str = "READY",
    preparation_ready: bool = True,
    canonical_symbol: str = "NASDAQ",
    root: str = "NQ",
    active_contract: str = "NQ 12-26",
    quantity: float | None = 2.0,
    max_contracts: int = 5,
) -> dict:
    return {
        "account_alias": alias,
        "status": status,
        "canonical_symbol": canonical_symbol,
        "selected_root": root,
        "max_contracts": max_contracts,
        "prepared_contracts": quantity,
        "preparation_ready": preparation_ready,
        "phase24_account_plan": {
            "status": "READY",
            "canonical_symbol": canonical_symbol,
            "selected_root": root,
            "active_contract": active_contract,
            "preparation_ready": True,
            "phase23_account_plan": {
                "prepared_volume": quantity,
            },
        },
    }


def _phase26_plan(
    *accounts: dict,
    policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
) -> dict:
    return {
        "trade_id": "LONDRES-NQ-001",
        "canonical_symbol": "NASDAQ",
        "policy": policy.value,
        "accounts": list(accounts),
    }


def test_phase27_authorizes_exact_account_handoff_and_uses_phase26_quantity() -> None:
    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(_account("NT-ALPHA", quantity=2.0)),
    )

    account = result["accounts"][0]
    assert result["status"] == "READY"
    assert result["execution_handoff_ready"] is True
    assert result["order_authorized"] is True
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
    assert (
        result["strategy_gate"]["required_sequence"]
        == "SMT_DETECTED+CSD_CONFIRMED+IOF_ALIGNED"
    )
    assert result["strategy_gate"]["phase17_volume_used"] is False
    assert (
        account["status"]
        == Phase27AccountStatus.AUTHORIZED_FOR_EXECUTION_HANDOFF.value
    )
    assert account["active_contract"] == "NQ 12-26"
    assert account["contract_quantity"] == 2
    assert account["contract_quantity"] != 99
    assert account["order_authorized"] is True
    assert len(account["authorization_fingerprint"]) == 64


def test_phase27_rejects_smt_without_csd_and_iof_confirmation() -> None:
    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(
            csd_confirmed_after_smt=False,
            post_csd_iofc_confirmed=False,
            smt_validated=False,
        ),
        phase26_plan=_phase26_plan(_account("NT-ALPHA")),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert result["order_authorized"] is False
    assert account["status"] == Phase27AccountStatus.BLOCKED_STRATEGY_GATE.value
    assert account["order_authorized"] is False
    assert "CSD_CONFIRMED_AFTER_SMT_REQUIRED" in account["reason_codes"]
    assert "POST_CSD_IOF_ALIGNMENT_REQUIRED" in account["reason_codes"]


def test_phase27_blocks_when_strategy_geometry_does_not_match_trade_intent() -> None:
    context = _strategy_context()
    context["entry_execution"]["exact_entry_price"] = 25001.0

    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=context,
        phase26_plan=_phase26_plan(_account("NT-ALPHA")),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase27AccountStatus.BLOCKED_INTENT_MISMATCH.value
    assert "ENTRY_PRICE_DOES_NOT_MATCH_TRADE_INTENT" in account["reason_codes"]


def test_phase27_best_effort_isolates_phase26_blocked_account() -> None:
    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(
            _account("NT-ALPHA", quantity=2.0),
            _account(
                "NT-BRAVO",
                status="BLOCKED_MAX_CONTRACTS",
                preparation_ready=False,
                quantity=8.0,
            ),
        ),
    )

    first, second = result["accounts"]
    assert result["status"] == "PARTIAL_READY"
    assert result["authorized_accounts"] == 1
    assert result["blocked_accounts"] == 1
    assert first["order_authorized"] is True
    assert second["status"] == Phase27AccountStatus.BLOCKED_PHASE26.value
    assert second["order_authorized"] is False


def test_phase27_all_or_none_revokes_otherwise_ready_account() -> None:
    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(
            _account("NT-ALPHA", quantity=2.0),
            _account(
                "NT-BRAVO",
                status="BLOCKED_MAX_CONTRACTS",
                preparation_ready=False,
                quantity=8.0,
            ),
            policy=OrchestrationPolicy.ALL_OR_NONE,
        ),
    )

    first, second = result["accounts"]
    assert result["status"] == "BLOCKED"
    assert result["authorized_accounts"] == 0
    assert result["order_authorized"] is False
    assert first["status"] == Phase27AccountStatus.BLOCKED_BATCH_POLICY.value
    assert first["authorization_fingerprint"] is None
    assert second["status"] == Phase27AccountStatus.BLOCKED_PHASE26.value


def test_phase27_requires_exact_integer_futures_quantity() -> None:
    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(_account("NT-ALPHA", quantity=1.5)),
    )

    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert (
        account["status"]
        == Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE.value
    )
    assert account["contract_quantity"] is None
    assert account["order_authorized"] is False


def test_phase27_rejects_account_symbol_or_root_not_bound_to_intent() -> None:
    wrong_symbol = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(
            _account(
                "NT-ALPHA",
                canonical_symbol="SP500",
                root="ES",
                active_contract="ES 12-26",
            )
        ),
    )
    account = wrong_symbol["accounts"][0]
    assert account["status"] == Phase27AccountStatus.BLOCKED_INTENT_MISMATCH.value
    assert "PHASE26_ACCOUNT_SYMBOL_DOES_NOT_MATCH_INTENT" in account["reason_codes"]

    wrong_root = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(
            _account("NT-ALPHA", root="ES", active_contract="ES 12-26")
        ),
    )
    account = wrong_root["accounts"][0]
    assert (
        account["status"]
        == Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE.value
    )
    assert "SELECTED_ROOT_DOES_NOT_MATCH_TRADE_INTENT" in account["reason_codes"]


def test_phase27_rejects_invalid_contract_and_phase26_cap_bypass() -> None:
    invalid_contract = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(
            _account("NT-ALPHA", active_contract="NQ 11-26")
        ),
    )
    account = invalid_contract["accounts"][0]
    assert (
        account["status"]
        == Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE.value
    )
    assert "ACTIVE_CONTRACT_FORMAT_OR_QUARTER_INVALID" in account["reason_codes"]

    cap_bypass = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(
            _account("NT-ALPHA", quantity=6.0, max_contracts=5)
        ),
    )
    account = cap_bypass["accounts"][0]
    assert (
        account["status"]
        == Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE.value
    )
    assert "PREPARED_QUANTITY_EXCEEDS_PHASE26_MAX_CONTRACTS" in account["reason_codes"]


def test_phase27_rejects_phase24_or_phase23_quantity_inconsistency() -> None:
    account_plan = _account("NT-ALPHA", quantity=2.0)
    account_plan["phase24_account_plan"]["phase23_account_plan"]["prepared_volume"] = 3.0
    result = LondresPhase27ExecutionAuthorizationEngine().authorize(
        intent=_intent(),
        strategy_context=_strategy_context(),
        phase26_plan=_phase26_plan(account_plan),
    )
    account = result["accounts"][0]
    assert (
        account["status"]
        == Phase27AccountStatus.BLOCKED_EXECUTION_ENVELOPE.value
    )
    assert "PHASE24_PHASE23_QUANTITY_DOES_NOT_MATCH_PHASE26" in account["reason_codes"]
