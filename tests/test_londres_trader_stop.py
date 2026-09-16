from __future__ import annotations

from tradingagents.agents.schemas import TraderAction
from tradingagents.agents.utils.londres_stop import (
    LondresTraderProposal,
    TraderRiskLevel,
    TraderStopSource,
    validate_londres_trader_stop,
)
from tradingagents.ict.target_management import TraderExitMode


def stop_options(*, two=True) -> dict:
    candidates = [
        {
            "source": "IOF_RANGE",
            "anchor_price": 13.2,
            "placement": "ABOVE_RANGE_HIGH",
            "distance_from_current": 1.2,
            "structural_source": "LATEST_VALID_DIRECTIONAL_IOF_RANGE_AFTER_CSD",
            "valid": True,
        }
    ]
    if two:
        candidates.append(
            {
                "source": "SMT_PROTECTED",
                "anchor_price": 16.0,
                "placement": "ABOVE_PROTECTED_HIGH",
                "distance_from_current": 4.0,
                "structural_source": "POST_SMT_CSD_PROTECTED_EXTREME",
                "valid": True,
            }
        )
    return {
        "direction": "BEARISH",
        "candidates": candidates,
        "selection_required": True,
    }


def target_management(*, hold_available=True) -> dict:
    runner = (
        {
            "price": 70.0,
            "side": "SELL_SIDE",
            "liquidity_class": "EXTERNAL",
            "timeframe": "4H",
            "source": "HTF_EXTERNAL_LIQUIDITY",
        }
        if hold_available
        else None
    )
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
        "hold_htf_liquidity_available": hold_available,
        "htf_runner_target": runner,
        "hold_management": {
            "partial": {
                "trigger_label": "-2.5",
                "trigger_price": 75.0,
                "close_fraction": 0.60,
                "runner_fraction": 0.40,
                "automatic_if_hold_mode": True,
            },
            "runner_fraction": 0.40 if hold_available else 0.0,
        },
        "selection_required": True,
        "order_authorized": False,
    }


def test_trader_can_choose_smt_protected_anchor_and_three_percent_risk() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="The wider protected swing better fits the current MMXM structure.",
        stop_loss=999.0,
        position_sizing="25 contracts",
        selected_stop_source=TraderStopSource.SMT_PROTECTED,
        selected_risk_level=TraderRiskLevel.RISK_3,
        stop_anchor_price=123.0,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.SELL
    assert validated.selected_stop_source == TraderStopSource.SMT_PROTECTED
    assert validated.selected_risk_level == TraderRiskLevel.RISK_3
    assert validated.stop_anchor_price == 16.0
    assert validated.stop_loss is None
    assert validated.position_sizing is None
    assert state["selected_source"] == "SMT_PROTECTED"
    assert state["selected_risk_fraction"] == 0.03
    assert state["hard_risk_ceiling_fraction"] == 0.10
    assert state["manual_position_size_allowed"] is False
    assert state["position_sizing_authority"] == "DETERMINISTIC_RISK_ENGINE"
    assert state["order_authorized"] is False


def test_trader_can_choose_iof_range_anchor_and_five_percent_risk() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="The most recent valid bearish IOF range is the intended structural invalidation.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_5,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.selected_stop_source == TraderStopSource.IOF_RANGE
    assert validated.selected_risk_level == TraderRiskLevel.RISK_5
    assert validated.stop_anchor_price == 13.2
    assert state["placement"] == "ABOVE_RANGE_HIGH"
    assert state["selected_risk_fraction"] == 0.05


def test_trader_can_choose_ten_percent_but_not_more() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Use the highest approved risk tier for this validated setup.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_10,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.SELL
    assert state["selected_risk_fraction"] == 0.10
    assert state["hard_risk_ceiling_fraction"] == 0.10
    assert state["allowed_risk_levels"] == ["3%", "5%", "10%"]


def test_active_trade_without_approved_risk_tier_fails_closed_to_hold() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="A risk tier was not selected.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.NONE,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.HOLD
    assert validated.selected_risk_level == TraderRiskLevel.NONE
    assert state["reason"] == "ACTIVE_TRADE_REQUIRES_3_5_OR_10_PERCENT_RISK_TIER"


def test_single_valid_candidate_is_forced_but_risk_tier_is_retained() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Only one structural choice remains valid.",
        selected_stop_source=TraderStopSource.NONE,
        selected_risk_level=TraderRiskLevel.RISK_3,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options(two=False))

    assert validated.action == TraderAction.SELL
    assert validated.selected_stop_source == TraderStopSource.IOF_RANGE
    assert validated.selected_risk_level == TraderRiskLevel.RISK_3
    assert validated.stop_anchor_price == 13.2
    assert state["reason"] == "ONLY_VALID_STRUCTURAL_STOP_SELECTED"


def test_invented_or_none_source_with_two_options_fails_closed_to_hold() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Attempting to avoid both deterministic anchors.",
        selected_stop_source=TraderStopSource.NONE,
        selected_risk_level=TraderRiskLevel.RISK_3,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.HOLD
    assert validated.selected_stop_source == TraderStopSource.NONE
    assert validated.selected_risk_level == TraderRiskLevel.NONE
    assert validated.stop_anchor_price is None
    assert state["valid"] is False
    assert state["reason"] == "STOP_SOURCE_NOT_IN_DETERMINISTIC_OPTIONS"


def test_direction_conflict_fails_closed_to_hold() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.BUY,
        reasoning="This conflicts with the bearish Londres gate.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_5,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.HOLD
    assert state["reason"] == "TRADER_DIRECTION_CONFLICTS_WITH_LONDRES_GATE"


def test_phase12_full_exit_at_minus_2_is_hard_validated() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Take the standard -2 objective.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_3,
        selected_exit_mode=TraderExitMode.FULL_AT_SD_2,
    )

    validated, state = validate_londres_trader_stop(
        proposal,
        stop_options(),
        target_management(),
    )

    assert validated.action == TraderAction.SELL
    assert validated.selected_exit_mode == TraderExitMode.FULL_AT_SD_2
    assert state["selected_exit_mode"] == "FULL_AT_SD_2"
    assert state["target_management"]["original_target"]["price"] == 80.0
    assert state["target_management"]["hold_for_htf_liquidity"] is False


def test_phase12_hold_mode_encodes_automatic_60_40_management() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Hold the runner for the available 4H sell-side liquidity.",
        selected_stop_source=TraderStopSource.SMT_PROTECTED,
        selected_risk_level=TraderRiskLevel.RISK_5,
        selected_exit_mode=TraderExitMode.HOLD_HTF_LIQUIDITY,
    )

    _, state = validate_londres_trader_stop(
        proposal,
        stop_options(),
        target_management(),
    )
    management = state["target_management"]

    assert management["partial_fraction"] == 0.60
    assert management["runner_fraction"] == 0.40
    assert management["partial_trigger"]["trigger_price"] == 75.0
    assert management["runner_target"]["price"] == 70.0


def test_phase12_hold_mode_fails_closed_when_htf_runner_is_unavailable() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Attempting unavailable runner hold.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_3,
        selected_exit_mode=TraderExitMode.HOLD_HTF_LIQUIDITY,
    )

    validated, state = validate_londres_trader_stop(
        proposal,
        stop_options(),
        target_management(hold_available=False),
    )

    assert validated.action == TraderAction.HOLD
    assert state["reason"] == "EXIT_MODE_NOT_IN_DETERMINISTIC_TARGET_OPTIONS"


def test_phase12_active_trade_requires_deterministic_exit_mode() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="No target choice was supplied.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
        selected_risk_level=TraderRiskLevel.RISK_3,
        selected_exit_mode=TraderExitMode.NONE,
    )

    validated, state = validate_londres_trader_stop(
        proposal,
        stop_options(),
        target_management(),
    )

    assert validated.action == TraderAction.HOLD
    assert state["reason"] == "ACTIVE_TRADE_REQUIRES_DETERMINISTIC_EXIT_MODE"
