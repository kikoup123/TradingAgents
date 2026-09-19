from __future__ import annotations

import tradingagents.dataflows.ctrader_master_setup as master


def _csd_result(timeframe: str) -> dict:
    if timeframe in {"H4", "H1"}:
        return {
            "current_orderflow_control": "bearish_control",
        }

    assert timeframe == "M15"
    return {
        "current_orderflow_control": "bearish_control",
        "active_csd": {
            "confirmed": True,
            "direction": "bearish",
            "liquidity": "buy_side_raid",
            "raid_time": "2026-09-19T14:00:00+00:00",
            "csd_threshold": 100.0,
            "confirmation_time": "2026-09-19T14:15:00+00:00",
        },
        "post_csd_iof": {
            "confirmed": True,
            "still_holding": True,
            "source_time": "2026-09-19T14:15:00+00:00",
            "confirmation_time": "2026-09-19T14:30:00+00:00",
        },
    }


def _patch_core(monkeypatch, *, ltf_pass: bool) -> None:
    monkeypatch.setattr(
        master,
        "analyze_csd_orderflow",
        lambda *, timeframe, **kwargs: _csd_result(timeframe),
    )
    monkeypatch.setattr(
        master,
        "detect_smt",
        lambda **kwargs: {
            "bullish_smt": {"detected": False},
            "bearish_smt": {"detected": True},
        },
    )
    monkeypatch.setattr(
        master,
        "evaluate_ltf_continuation",
        lambda **kwargs: {
            "ltf_gate_passed": ltf_pass,
            "status": "VALID_CONTINUATION" if ltf_pass else "WAIT",
            "direction": "bearish",
            "protected_swing_intact": ltf_pass,
            "reason": "synthetic test",
        },
    )


def test_active_positional_entry_bypasses_unicorn_ltf_continuation_gate(
    monkeypatch,
) -> None:
    _patch_core(monkeypatch, ltf_pass=False)

    monkeypatch.setattr(
        master,
        "evaluate_positional_entry",
        lambda **kwargs: {
            "status": "POSITIONAL_ACTIVE",
            "entry_model": "POSITIONAL",
            "entry_model_confirmed": True,
            "entry_gate_passed": True,
            "fallback_to_unicorn": False,
            "exact_order_price": 20000.0,
            "exact_order_price_defined": True,
            "stop_reference": 20020.0,
            "target": {"price": 19900.0, "label": "STD_-2"},
            "execution_allowed": False,
        },
    )

    def unicorn_must_not_run(**kwargs):
        raise AssertionError("Unicorn must not run after a valid positional entry")

    monkeypatch.setattr(
        master,
        "evaluate_unicorn_entry",
        unicorn_must_not_run,
    )

    result = master.evaluate_master_setup()

    assert result["setup_status"] == "ENTRY_MODEL_CONFIRMED"
    assert result["selected_entry_model"] == "POSITIONAL"
    assert result["entry_signal_active"] is True
    assert result["gates"]["POSITIONAL"] == "PASS"
    assert result["gates"]["LTF"] == "BYPASSED_POSITIONAL"
    assert result["gates"]["ENTRY"] == "PASS"
    assert result["entry_allowed"] is False


def test_positional_wait_falls_back_to_existing_unicorn_housing_engine(
    monkeypatch,
) -> None:
    _patch_core(monkeypatch, ltf_pass=True)

    positional = {
        "status": "WAIT_VALID_PROTECTED_SWING",
        "entry_model": "POSITIONAL",
        "entry_model_confirmed": False,
        "entry_gate_passed": False,
        "fallback_to_unicorn": True,
        "execution_allowed": False,
    }
    monkeypatch.setattr(
        master,
        "evaluate_positional_entry",
        lambda **kwargs: positional,
    )
    monkeypatch.setattr(
        master,
        "evaluate_unicorn_entry",
        lambda **kwargs: {
            "status": "ENTRY_MODEL_CONFIRMED",
            "entry_gate_passed": True,
            "entry_model_invalidated": False,
            "exact_order_price_defined": False,
            "execution_allowed": False,
        },
    )

    result = master.evaluate_master_setup()

    assert result["setup_status"] == "ENTRY_MODEL_CONFIRMED"
    assert result["selected_entry_model"] == "UNICORN_HOUSING"
    assert result["POSITIONAL_detail"] == positional
    assert result["gates"]["LTF"] == "PASS"
    assert result["gates"]["ENTRY"] == "PASS"
    assert result["entry_allowed"] is False


def test_stopped_positional_sequence_does_not_reenter_same_sequence_via_unicorn(
    monkeypatch,
) -> None:
    _patch_core(monkeypatch, ltf_pass=True)

    monkeypatch.setattr(
        master,
        "evaluate_positional_entry",
        lambda **kwargs: {
            "status": "POSITIONAL_STOPPED",
            "entry_model": "POSITIONAL",
            "entry_model_confirmed": True,
            "entry_gate_passed": False,
            "entry_model_invalidated": True,
            "fallback_to_unicorn": False,
            "execution_allowed": False,
        },
    )

    def unicorn_must_not_run(**kwargs):
        raise AssertionError("Stopped positional sequence requires a new setup")

    monkeypatch.setattr(
        master,
        "evaluate_unicorn_entry",
        unicorn_must_not_run,
    )

    result = master.evaluate_master_setup()

    assert result["setup_status"] == "BLOCKED"
    assert result["selected_entry_model"] == "POSITIONAL"
    assert result["gates"]["POSITIONAL"] == "COMPLETE"
    assert result["gates"]["ENTRY"] == "COMPLETE"
    assert result["entry_signal_active"] is False
    assert result["entry_allowed"] is False
