"""Structured Londres stop-source choice and hard validation for the Trader."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from tradingagents.agents.schemas import TraderAction, TraderProposal


class TraderStopSource(str, Enum):
    IOF_RANGE = "IOF_RANGE"
    SMT_PROTECTED = "SMT_PROTECTED"
    NONE = "NONE"


class LondresTraderProposal(TraderProposal):
    """Trader proposal with a constrained structural-stop choice.

    ``stop_anchor_price`` is not the executable broker stop. It is the exact
    deterministic structural boundary selected by the Trader. A later risk
    layer applies the instrument-specific tick/buffer beyond that anchor and
    derives volume from the resulting entry-to-stop range. The Trader is never
    the sizing authority on the Londres path.
    """

    selected_stop_source: TraderStopSource = Field(
        default=TraderStopSource.NONE,
        description=(
            "Choose exactly one deterministic structural stop source supplied in "
            "the prompt: IOF_RANGE or SMT_PROTECTED. Use NONE only for Hold/Wait."
        ),
    )
    stop_anchor_price: float | None = Field(
        default=None,
        description=(
            "Structural stop anchor selected from the deterministic options. Do not "
            "invent this value; the hard validator replaces it with the engine value."
        ),
    )
    stop_selection_reason: str | None = Field(
        default=None,
        description=(
            "Briefly explain why the selected structural anchor fits the current "
            "MMXM, order-flow and target geometry."
        ),
    )


def validate_londres_trader_stop(
    proposal: LondresTraderProposal,
    stop_options: dict,
) -> tuple[LondresTraderProposal, dict]:
    """Accept only deterministic stop structure and strip discretionary sizing."""
    candidates = {
        candidate["source"]: candidate
        for candidate in stop_options.get("candidates", [])
        if candidate.get("valid")
    }
    direction = stop_options.get("direction")
    expected_action = (
        TraderAction.BUY
        if direction == "BULLISH"
        else TraderAction.SELL
        if direction == "BEARISH"
        else TraderAction.HOLD
    )

    if proposal.action == TraderAction.HOLD:
        clean = proposal.model_copy(
            update={
                "selected_stop_source": TraderStopSource.NONE,
                "stop_anchor_price": None,
                "stop_loss": None,
                "position_sizing": None,
            }
        )
        return clean, {
            "valid": True,
            "selected_source": TraderStopSource.NONE.value,
            "selected_anchor_price": None,
            "placement": None,
            "reason": "TRADER_CHOSE_HOLD",
            "position_sizing_authority": "DETERMINISTIC_RISK_ENGINE",
            "manual_position_size_allowed": False,
            "order_authorized": False,
        }

    if proposal.action != expected_action:
        return _force_hold(proposal, "TRADER_DIRECTION_CONFLICTS_WITH_LONDRES_GATE")

    if not candidates:
        return _force_hold(proposal, "NO_VALID_STRUCTURAL_STOP_OPTION")

    if len(candidates) == 1:
        source, candidate = next(iter(candidates.items()))
    else:
        source = proposal.selected_stop_source.value
        candidate = candidates.get(source)
        if candidate is None:
            return _force_hold(proposal, "STOP_SOURCE_NOT_IN_DETERMINISTIC_OPTIONS")

    validated = proposal.model_copy(
        update={
            "selected_stop_source": TraderStopSource(source),
            "stop_anchor_price": float(candidate["anchor_price"]),
            # Phase 10 selects the structural anchor only. Do not permit the
            # LLM to manufacture a buffer, broker stop, or position size.
            "stop_loss": None,
            "position_sizing": None,
        }
    )
    selection = {
        "valid": True,
        "selected_source": source,
        "selected_anchor_price": float(candidate["anchor_price"]),
        "placement": candidate.get("placement"),
        "distance_from_current": candidate.get("distance_from_current"),
        "structural_source": candidate.get("structural_source"),
        "reason": (
            "ONLY_VALID_STRUCTURAL_STOP_SELECTED"
            if len(candidates) == 1
            else "TRADER_STRUCTURAL_STOP_SELECTION_ACCEPTED"
        ),
        "position_sizing_authority": "DETERMINISTIC_RISK_ENGINE",
        "manual_position_size_allowed": False,
        "order_authorized": False,
    }
    return validated, selection


def _force_hold(
    proposal: LondresTraderProposal,
    reason: str,
) -> tuple[LondresTraderProposal, dict]:
    reasoning = proposal.reasoning.strip()
    suffix = f"Londres hard gate: {reason}."
    clean = proposal.model_copy(
        update={
            "action": TraderAction.HOLD,
            "reasoning": f"{reasoning} {suffix}".strip(),
            "entry_price": None,
            "stop_loss": None,
            "position_sizing": None,
            "selected_stop_source": TraderStopSource.NONE,
            "stop_anchor_price": None,
        }
    )
    return clean, {
        "valid": False,
        "selected_source": TraderStopSource.NONE.value,
        "selected_anchor_price": None,
        "placement": None,
        "reason": reason,
        "position_sizing_authority": "DETERMINISTIC_RISK_ENGINE",
        "manual_position_size_allowed": False,
        "order_authorized": False,
    }


def render_londres_trader_proposal(proposal: LondresTraderProposal) -> str:
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.selected_stop_source != TraderStopSource.NONE:
        parts.extend(["", f"**Selected Stop Source**: {proposal.selected_stop_source.value}"])
    if proposal.stop_anchor_price is not None:
        parts.extend(["", f"**Structural Stop Anchor**: {proposal.stop_anchor_price}"])
    if proposal.stop_selection_reason:
        parts.extend(["", f"**Stop Selection Rationale**: {proposal.stop_selection_reason}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    # Londres position size is intentionally never rendered from the LLM.
    parts.extend(["", f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**"])
    return "\n".join(parts)
