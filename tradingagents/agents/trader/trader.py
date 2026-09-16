"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools
import logging

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import (
    TraderAction,
    TraderProposal,
    render_trader_proposal,
)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.londres_stop import (
    LondresTraderProposal,
    TraderRiskLevel,
    TraderStopSource,
    render_londres_trader_proposal,
    validate_londres_trader_stop,
)
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)

logger = logging.getLogger(__name__)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")
    londres_structured_llm = bind_structured(llm, LondresTraderProposal, "Londres Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]
        market_report = (state["market_report"] or "").strip()
        stop_options = state.get("stop_options_state") or {}
        trade_plan = state.get("trade_plan_state") or {}
        use_londres_stop_gate = bool(stop_options.get("selection_required"))

        if market_report:
            grounding = (
                "Ground concrete price levels (entry, stop-loss, position sizing) in the technical "
                "market report's price structure -- current price, support/resistance, ATR, and "
                "volatility -- and use the research plan for direction and strategy. "
            )
            report_section = f"Technical Market Report:\n{market_report}\n\n"
        else:
            grounding = ""
            report_section = ""

        stop_system_instruction = ""
        stop_section = ""
        if use_londres_stop_gate:
            candidate_lines = []
            for candidate in stop_options.get("candidates", []):
                if not candidate.get("valid"):
                    continue
                candidate_lines.append(
                    "- "
                    f"{candidate['source']}: anchor={candidate['anchor_price']}, "
                    f"placement={candidate['placement']}, "
                    f"distance_from_current={candidate.get('distance_from_current')}, "
                    f"structural_source={candidate.get('structural_source')}"
                )
            target = trade_plan.get("primary_target") or {}
            stop_section = (
                "Londres deterministic stop options:\n"
                + "\n".join(candidate_lines)
                + "\n"
                + f"Londres direction: {stop_options.get('direction')}\n"
                + f"Primary objective: {target.get('price')} ({target.get('source')})\n"
                + "Approved account-risk tiers: 3%, 5%, 10% (10% is the hard ceiling).\n\n"
            )
            stop_system_instruction = (
                "A validated Londres trade context is present. Choose the structural stop source "
                "yourself from the exact options supplied by the deterministic engine. You may "
                "choose IOF_RANGE or SMT_PROTECTED when both are valid. If only one is valid, use "
                "that one. Do not invent a different structural stop. Explain the choice using "
                "MMXM/order-flow context and target geometry. The supplied price is a structural "
                "anchor only; do not invent a tick buffer or executable stop-loss price yet. "
                "Set stop_loss to null/omit it. For an active trade, independently choose exactly "
                "one account-risk tier: 3%, 5%, or 10%. Ten percent is an absolute hard ceiling. "
                "Explain the risk-tier choice, but do not choose a lot size, contract count, or "
                "position size: Londres volume is calculated by the deterministic risk engine from "
                "the exact entry-to-stop pips/ticks, broker tick value, and selected risk tier. "
                "Set position_sizing to null/omit it. If the Londres direction conflicts with your "
                "transaction direction, choose Hold. "
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    + grounding
                    + stop_system_instruction
                    + "State entry price and stop-loss as absolute price levels in the "
                    "instrument's quote currency (for example 189.5), never a percentage "
                    "or a range; convert a percentage distance to the price level it "
                    "implies, or omit the field if you cannot state a number. "
                    + NO_EXTERNAL_TOOLS
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Here is the research team's investment plan for {company_name}. "
                    f"{instrument_context}\n\n"
                    f"{report_section}"
                    f"{stop_section}"
                    f"Proposed Investment Plan:\n{investment_plan}\n\n"
                    f"Make an informed, strategic trading decision."
                ),
            },
        ]

        stop_selection_state = {}
        if use_londres_stop_gate:
            if londres_structured_llm is None:
                proposal = LondresTraderProposal(
                    action=TraderAction.HOLD,
                    reasoning=(
                        "The deterministic Londres stop/risk gate requires structured selection, "
                        "but this provider cannot return the required schema safely."
                    ),
                    selected_stop_source=TraderStopSource.NONE,
                    selected_risk_level=TraderRiskLevel.NONE,
                )
                stop_selection_state = {
                    "valid": False,
                    "selected_source": TraderStopSource.NONE.value,
                    "selected_anchor_price": None,
                    "selected_risk_level": TraderRiskLevel.NONE.value,
                    "selected_risk_fraction": None,
                    "hard_risk_ceiling_fraction": 0.10,
                    "placement": None,
                    "reason": "STRUCTURED_STOP_RISK_SELECTION_UNAVAILABLE",
                    "order_authorized": False,
                }
            else:
                try:
                    proposal = londres_structured_llm.invoke(messages)
                    if proposal is None:
                        raise ValueError("structured output returned no parsed result")
                    proposal, stop_selection_state = validate_londres_trader_stop(
                        proposal,
                        stop_options,
                    )
                except Exception as exc:
                    logger.warning(
                        "Londres Trader stop/risk selection failed (%s); failing closed to Hold",
                        exc,
                    )
                    proposal = LondresTraderProposal(
                        action=TraderAction.HOLD,
                        reasoning="Londres structural stop/risk selection could not be validated safely.",
                        selected_stop_source=TraderStopSource.NONE,
                        selected_risk_level=TraderRiskLevel.NONE,
                    )
                    stop_selection_state = {
                        "valid": False,
                        "selected_source": TraderStopSource.NONE.value,
                        "selected_anchor_price": None,
                        "selected_risk_level": TraderRiskLevel.NONE.value,
                        "selected_risk_fraction": None,
                        "hard_risk_ceiling_fraction": 0.10,
                        "placement": None,
                        "reason": "STRUCTURAL_STOP_RISK_SELECTION_VALIDATION_FAILED",
                        "order_authorized": False,
                    }
            trader_plan = render_londres_trader_proposal(proposal)
        else:
            trader_plan = invoke_structured_or_freetext(
                structured_llm,
                llm,
                messages,
                render_trader_proposal,
                "Trader",
            )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "trader_stop_selection_state": stop_selection_state,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
