from __future__ import annotations

from tradingagents.agents.schemas import TraderAction
from tradingagents.agents.utils.londres_stop import (
    LondresTraderProposal,
    TraderRiskLevel,
    TraderStopSource,
    validate_londres_trader_stop,
)
from tradingagents.ict.target_management import TraderExitMode


def _stop_options() -> dict:
    return {
        "direction": "BEARISH",
        "selection_required": True,
        "candidates": [
            {
                "source": "IOF_RANGE",
                "anchor_price": 104.0,
                "placement": "ABOVE_RANGE_HIGH",
                "distance_from_current": 4.0,
                "structural_source": "LATEST_VALID_DIRECTIONAL_IOF_RANGE_AFTER_CSD",
                "valid": True,
            },
            {
                "source": "SMT_PROTECTED",
                "anchor_price": 110.0,
                "placement": "ABOVE_PROTECTED_HIGH",
                "distance_from_current": 10.0,
                "structural_source": "POST_SMT_CSD_PROTECTED_EXTREME",
                "valid": True,
            },
        ],
    }


def _targets() -> dict:
    return {
        "status": "READY",
        "direction": "BEARISH",
        "sd_targets": {
            "-2": {"label": "-2", "multiplier": 2.0, "price": 80.0},
            "-2.5": {"label": "-2.5", "multiplier": 2.5, "price": 75.0},
        },
        "full_exit_options": [
            {"mode": "FULL_AT_SD_2", "label": "-2", "price": 80.0},
            {"mode": "FULL_AT_SD_2_5", "label": "-2.5", "price": 75.0},
        ],
        "hold_htf_liquidity_available": False,
        "htf_runner_target": None,
        "hold_management": {"partial": None, "runner_fraction": 0.0},
    }


def test_phase13_exact_entry_replaces_any_llm_invented_entry() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Use the confirmed post-CSD IOF retracement.",
        entry_price=99.25,
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_3,
        selected_exit_mode=TraderExitMode.FULL_AT_SD_2,
    )
    entry_execution = {
        "status": "ENTRY_TRIGGERED",
        "exact_entry_price": 100.0,
        "entry_rule": "FIRST_RETURN_INTO_CONFIRMED_POST_CSD_IOF_RANGE",
    }

    validated, state = validate_londres_trader_stop(
        proposal,
        _stop_options(),
        _targets(),
        entry_execution,
    )

    assert validated.action == TraderAction.SELL
    assert validated.entry_price == 100.0
    assert state["exact_entry_price"] == 100.0
    assert state["entry_source"] == "FIRST_RETURN_INTO_CONFIRMED_POST_CSD_IOF_RANGE"


def test_phase13_wait_for_retrace_forces_hold_even_if_trader_wants_entry() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Trying to enter before the IOF retracement.",
        entry_price=98.0,
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_5,
        selected_exit_mode=TraderExitMode.FULL_AT_SD_2_5,
    )
    entry_execution = {
        "status": "WAIT_FOR_RETRACE",
        "exact_entry_price": None,
        "entry_rule": "FIRST_RETURN_INTO_CONFIRMED_POST_CSD_IOF_RANGE",
    }

    validated, state = validate_londres_trader_stop(
        proposal,
        _stop_options(),
        _targets(),
        entry_execution,
    )

    assert validated.action == TraderAction.HOLD
    assert validated.entry_price is None
    assert state["reason"] == "WAIT_FOR_POST_CSD_IOF_RANGE_RETRACE_ENTRY"
