from __future__ import annotations

from tradingagents.agents.schemas import TraderAction
from tradingagents.agents.utils.londres_stop import (
    LondresTraderProposal,
    TraderRiskLevel,
    TraderStopSource,
    validate_londres_trader_stop,
)


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
