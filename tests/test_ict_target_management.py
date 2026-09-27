from __future__ import annotations

import pytest

from tradingagents.ict.target_management import (
    CSDTargetEngine,
    RunnerAction,
    TargetManagementStatus,
    TraderExitMode,
    manage_runner,
    select_target_management,
)


def bearish_context() -> dict:
    return {
        "validation_csd": {
            "direction": "BEARISH",
            "threshold_open": 100.0,
            "protected_extreme": 110.0,
        },
        "execution_gate": {"direction": "BEARISH"},
        "liquidity": {
            "timeframe": "4H",
            "sell_side": [
                {
                    "price": 74.0,
                    "side": "SELL_SIDE",
                    "liquidity_class": "INTERNAL",
                    "status": "ACTIVE",
                    "source_kind": "SWING_LOW",
                    "source_time": "2026-09-15T08:00:00",
                },
                {
                    "price": 70.0,
                    "side": "SELL_SIDE",
                    "liquidity_class": "EXTERNAL",
                    "status": "ACTIVE",
                    "source_kind": "SWING_LOW",
                    "source_time": "2026-09-14T08:00:00",
                },
            ],
            "buy_side": [],
        },
    }


def bullish_context() -> dict:
    return {
        "validation_csd": {
            "direction": "BULLISH",
            "threshold_open": 100.0,
            "protected_extreme": 90.0,
        },
        "execution_gate": {"direction": "BULLISH"},
        "liquidity": {
            "timeframe": "4H",
            "buy_side": [
                {
                    "price": 130.0,
                    "side": "BUY_SIDE",
                    "liquidity_class": "EXTERNAL",
                    "status": "ACTIVE",
                    "source_kind": "SWING_HIGH",
                    "source_time": "2026-09-14T08:00:00",
                }
            ],
            "sell_side": [],
        },
    }


def test_bearish_csd_range_projects_minus_2_and_minus_2_5() -> None:
    result = CSDTargetEngine().analyze(bearish_context()).to_dict()

    assert result["status"] == TargetManagementStatus.READY.value
    assert result["range_size"] == 10.0
    assert result["sd_targets"]["-2"]["price"] == 80.0
    assert result["sd_targets"]["-2.5"]["price"] == 75.0
    assert result["hold_htf_liquidity_available"] is True
    assert result["htf_runner_target"]["price"] == 70.0
    assert result["htf_runner_target"]["side"] == "SELL_SIDE"


def test_bullish_geometry_is_mirrored_while_labels_remain_minus_2_convention() -> None:
    result = CSDTargetEngine().analyze(bullish_context()).to_dict()

    assert result["status"] == TargetManagementStatus.READY.value
    assert result["range_size"] == 10.0
    assert result["sd_targets"]["-2"]["price"] == 120.0
    assert result["sd_targets"]["-2.5"]["price"] == 125.0
    assert result["htf_runner_target"]["price"] == 130.0
    assert result["htf_runner_target"]["side"] == "BUY_SIDE"


def test_hold_mode_forces_60_percent_partial_at_minus_2_5_and_40_percent_runner() -> None:
    plan = CSDTargetEngine().analyze(bearish_context()).to_dict()
    selection = select_target_management(plan, TraderExitMode.HOLD_HTF_LIQUIDITY)

    assert selection["selected_sd_target"]["label"] == "-2.5"
    assert selection["partial_trigger"]["trigger_price"] == 75.0
    assert selection["partial_fraction"] == pytest.approx(0.60)
    assert selection["runner_fraction"] == pytest.approx(0.40)
    assert selection["runner_target"]["price"] == 70.0
    assert selection["original_target"]["price"] == 70.0


def test_standard_exit_can_close_all_at_minus_2_or_minus_2_5() -> None:
    plan = CSDTargetEngine().analyze(bearish_context()).to_dict()

    tp1 = select_target_management(plan, TraderExitMode.FULL_AT_SD_2)
    tp2 = select_target_management(plan, TraderExitMode.FULL_AT_SD_2_5)

    assert tp1["original_target"]["price"] == 80.0
    assert tp1["partial_fraction"] == 0.0
    assert tp2["original_target"]["price"] == 75.0
    assert tp2["runner_fraction"] == 0.0


def test_hold_mode_is_unavailable_without_external_liquidity_beyond_minus_2_5() -> None:
    context = bearish_context()
    context["liquidity"]["sell_side"] = [
        {
            "price": 78.0,
            "side": "SELL_SIDE",
            "liquidity_class": "EXTERNAL",
            "status": "ACTIVE",
            "source_kind": "SWING_LOW",
            "source_time": "2026-09-15T08:00:00",
        }
    ]
    plan = CSDTargetEngine().analyze(context).to_dict()

    assert plan["hold_htf_liquidity_available"] is False
    with pytest.raises(ValueError, match="HTF liquidity hold mode is unavailable"):
        select_target_management(plan, TraderExitMode.HOLD_HTF_LIQUIDITY)


def test_invalid_csd_protected_extreme_geometry_fails_closed() -> None:
    context = bearish_context()
    context["validation_csd"]["protected_extreme"] = 95.0
    result = CSDTargetEngine().analyze(context)

    assert result.status == TargetManagementStatus.INVALID_CSD_RANGE
    assert result.sd_2 is None
    assert result.hold_available is False


def test_early_runner_close_records_reason_without_rewriting_original_target() -> None:
    plan = CSDTargetEngine().analyze(bearish_context()).to_dict()
    selection = select_target_management(plan, TraderExitMode.HOLD_HTF_LIQUIDITY)
    original = dict(selection["original_target"])

    managed = manage_runner(
        selection,
        RunnerAction.CLOSE_EARLY,
        reason="Opposing CSD and IOF failure developed before HTF SSL.",
    )

    assert managed["original_target"] == original
    assert managed["early_exit_reason"].startswith("Opposing CSD")
    assert managed["runner_status"] == "EARLY_CLOSE_REQUESTED"


def test_early_runner_close_requires_reason() -> None:
    plan = CSDTargetEngine().analyze(bearish_context()).to_dict()
    selection = select_target_management(plan, TraderExitMode.HOLD_HTF_LIQUIDITY)

    with pytest.raises(ValueError, match="requires a recorded reason"):
        manage_runner(selection, RunnerAction.CLOSE_EARLY)
