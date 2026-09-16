"""Structured Londres stop/risk/target choices and hard validation for the Trader."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from tradingagents.agents.schemas import TraderAction, TraderProposal
from tradingagents.ict.target_management import TraderExitMode, select_target_management


class TraderStopSource(str, Enum):
    IOF_RANGE = "IOF_RANGE"
    SMT_PROTECTED = "SMT_PROTECTED"
    NONE = "NONE"


class TraderRiskLevel(str, Enum):
    RISK_3 = "3%"
    RISK_5 = "5%"
    RISK_10 = "10%"
    NONE = "NONE"

    @property
    def fraction(self) -> float | None:
        return {
            TraderRiskLevel.RISK_3: 0.03,
            TraderRiskLevel.RISK_5: 0.05,
            TraderRiskLevel.RISK_10: 0.10,
            TraderRiskLevel.NONE: None,
        }[self]


class LondresTraderProposal(TraderProposal):
    """Trader proposal with constrained structural-stop, risk and exit choices.

    ``stop_anchor_price`` is not the executable broker stop. It is the exact
    deterministic structural boundary selected by the Trader. A later risk
    layer applies the instrument-specific tick/buffer beyond that anchor and
    derives volume from the resulting entry-to-stop range.

    The Trader may choose only 3%, 5%, or 10% account risk and only one of the
    deterministic Phase 12 exit modes. The Trader never chooses lots/contracts
    or manufactures a take-profit price directly.
    """

    selected_stop_source: TraderStopSource = Field(
        default=TraderStopSource.NONE,
        description=(
            "Choose exactly one deterministic structural stop source supplied in "
            "the prompt: IOF_RANGE or SMT_PROTECTED. Use NONE only for Hold/Wait."
        ),
    )
    selected_risk_level: TraderRiskLevel = Field(
        default=TraderRiskLevel.NONE,
        description=(
            "Choose exactly one approved Londres account-risk tier for an active trade: "
            "3%, 5%, or 10%. Ten percent is the hard maximum. Use NONE only for Hold/Wait."
        ),
    )
    selected_exit_mode: TraderExitMode = Field(
        default=TraderExitMode.NONE,
        description=(
            "Choose exactly one deterministic exit mode supplied by Phase 12: "
            "FULL_AT_SD_2, FULL_AT_SD_2_5, or HOLD_HTF_LIQUIDITY when available. "
            "Use NONE only for Hold/Wait."
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
    risk_selection_reason: str | None = Field(
        default=None,
        description=(
            "Briefly explain why 3%, 5%, or 10% risk was selected from the approved tiers. "
            "Do not translate this into lots/contracts; the risk engine sizes the trade."
        ),
    )
    target_selection_reason: str | None = Field(
        default=None,
        description=(
            "Briefly explain why the deterministic -2, -2.5, or HTF-liquidity hold mode "
            "fits the current delivery. Do not invent another target price."
        ),
    )


def validate_londres_trader_stop(
    proposal: LondresTraderProposal,
    stop_options: dict,
    target_management: dict | None = None,
) -> tuple[LondresTraderProposal, dict]:
    """Accept only deterministic stop, 3/5/10 risk, and Phase 12 exit choices."""
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
                "selected_risk_level": TraderRiskLevel.NONE,
                "selected_exit_mode": TraderExitMode.NONE,
                "stop_anchor_price": None,
                "stop_loss": None,
                "position_sizing": None,
            }
        )
        return clean, {
            "valid": True,
            "selected_source": TraderStopSource.NONE.value,
            "selected_anchor_price": None,
            "selected_risk_level": TraderRiskLevel.NONE.value,
            "selected_risk_fraction": None,
            "selected_exit_mode": TraderExitMode.NONE.value,
            "target_management": None,
            "hard_risk_ceiling_fraction": 0.10,
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

    if proposal.selected_risk_level == TraderRiskLevel.NONE:
        return _force_hold(proposal, "ACTIVE_TRADE_REQUIRES_3_5_OR_10_PERCENT_RISK_TIER")

    target_selection = None
    if target_management:
        if target_management.get("status") != "READY":
            return _force_hold(proposal, "TARGET_MANAGEMENT_NOT_READY")
        if proposal.selected_exit_mode == TraderExitMode.NONE:
            return _force_hold(proposal, "ACTIVE_TRADE_REQUIRES_DETERMINISTIC_EXIT_MODE")
        try:
            target_selection = select_target_management(
                target_management,
                proposal.selected_exit_mode,
            )
        except ValueError:
            return _force_hold(proposal, "EXIT_MODE_NOT_IN_DETERMINISTIC_TARGET_OPTIONS")

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
            # The Trader selects structural stop + approved risk/target policy,
            # never executable stop buffer, broker volume, or arbitrary TP price.
            "stop_loss": None,
            "position_sizing": None,
        }
    )
    selection = {
        "valid": True,
        "selected_source": source,
        "selected_anchor_price": float(candidate["anchor_price"]),
        "selected_risk_level": validated.selected_risk_level.value,
        "selected_risk_fraction": validated.selected_risk_level.fraction,
        "selected_exit_mode": validated.selected_exit_mode.value,
        "target_management": target_selection,
        "hard_risk_ceiling_fraction": 0.10,
        "allowed_risk_levels": ["3%", "5%", "10%"],
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
            "selected_risk_level": TraderRiskLevel.NONE,
            "selected_exit_mode": TraderExitMode.NONE,
            "stop_anchor_price": None,
        }
    )
    return clean, {
        "valid": False,
        "selected_source": TraderStopSource.NONE.value,
        "selected_anchor_price": None,
        "selected_risk_level": TraderRiskLevel.NONE.value,
        "selected_risk_fraction": None,
        "selected_exit_mode": TraderExitMode.NONE.value,
        "target_management": None,
        "hard_risk_ceiling_fraction": 0.10,
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
    if proposal.selected_risk_level != TraderRiskLevel.NONE:
        parts.extend(["", f"**Selected Account Risk**: {proposal.selected_risk_level.value}"])
    if proposal.risk_selection_reason:
        parts.extend(["", f"**Risk Selection Rationale**: {proposal.risk_selection_reason}"])
    if proposal.selected_exit_mode != TraderExitMode.NONE:
        parts.extend(["", f"**Selected Exit Mode**: {proposal.selected_exit_mode.value}"])
    if proposal.target_selection_reason:
        parts.extend(["", f"**Target Selection Rationale**: {proposal.target_selection_reason}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    # Londres position size and TP prices are never invented by the LLM.
    parts.extend(["", f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**"])
    return "\n".join(parts)
