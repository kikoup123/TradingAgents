from __future__ import annotations

from tradingagents.agents.schemas import TraderAction
from tradingagents.agents.utils.londres_stop import (
    LondresTraderProposal,
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


def test_trader_can_choose_smt_protected_anchor_and_llm_risk_fields_are_cleared() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="The wider protected swing better fits the current MMXM structure.",
        stop_loss=999.0,
        position_sizing="25 contracts",
        selected_stop_source=TraderStopSource.SMT_PROTECTED,
        stop_anchor_price=123.0,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.SELL
    assert validated.selected_stop_source == TraderStopSource.SMT_PROTECTED
    assert validated.stop_anchor_price == 16.0
    assert validated.stop_loss is None
    assert validated.position_sizing is None
    assert state["selected_source"] == "SMT_PROTECTED"
    assert state["manual_position_size_allowed"] is False
    assert state["position_sizing_authority"] == "DETERMINISTIC_RISK_ENGINE"
    assert state["order_authorized"] is False


def test_trader_can_choose_iof_range_anchor() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="The most recent valid bearish IOF range is the intended structural invalidation.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.selected_stop_source == TraderStopSource.IOF_RANGE
    assert validated.stop_anchor_price == 13.2
    assert state["placement"] == "ABOVE_RANGE_HIGH"


def test_single_valid_candidate_is_forced_even_if_trader_requests_none() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Only one structural choice remains valid.",
        selected_stop_source=TraderStopSource.NONE,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options(two=False))

    assert validated.action == TraderAction.SELL
    assert validated.selected_stop_source == TraderStopSource.IOF_RANGE
    assert validated.stop_anchor_price == 13.2
    assert state["reason"] == "ONLY_VALID_STRUCTURAL_STOP_SELECTED"


def test_invented_or_none_source_with_two_options_fails_closed_to_hold() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.SELL,
        reasoning="Attempting to avoid both deterministic anchors.",
        selected_stop_source=TraderStopSource.NONE,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.HOLD
    assert validated.selected_stop_source == TraderStopSource.NONE
    assert validated.stop_anchor_price is None
    assert state["valid"] is False
    assert state["reason"] == "STOP_SOURCE_NOT_IN_DETERMINISTIC_OPTIONS"


def test_direction_conflict_fails_closed_to_hold() -> None:
    proposal = LondresTraderProposal(
        action=TraderAction.BUY,
        reasoning="This conflicts with the bearish Londres gate.",
        selected_stop_source=TraderStopSource.IOF_RANGE,
    )

    validated, state = validate_londres_trader_stop(proposal, stop_options())

    assert validated.action == TraderAction.HOLD
    assert state["reason"] == "TRADER_DIRECTION_CONFLICTS_WITH_LONDRES_GATE"
