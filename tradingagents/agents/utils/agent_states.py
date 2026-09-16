from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import TypedDict


# Researcher team state
class InvestDebateState(TypedDict):
    bull_history: Annotated[
        str, "Bullish Conversation history"
    ]  # Bullish Conversation history
    bear_history: Annotated[
        str, "Bearish Conversation history"
    ]  # Bullish Conversation history
    history: Annotated[str, "Conversation history"]  # Conversation history
    current_response: Annotated[str, "Latest response"]  # Last response
    judge_decision: Annotated[str, "Final judge decision"]  # Last response
    count: Annotated[int, "Length of the current conversation"]  # Conversation length


# Risk management team state
class RiskDebateState(TypedDict):
    aggressive_history: Annotated[
        str, "Aggressive Agent's Conversation history"
    ]  # Conversation history
    conservative_history: Annotated[
        str, "Conservative Agent's Conversation history"
    ]  # Conversation history
    neutral_history: Annotated[
        str, "Neutral Agent's Conversation history"
    ]  # Conversation history
    history: Annotated[str, "Conversation history"]  # Conversation history
    latest_speaker: Annotated[str, "Analyst that spoke last"]
    current_aggressive_response: Annotated[
        str, "Latest response by the aggressive analyst"
    ]  # Last response
    current_conservative_response: Annotated[
        str, "Latest response by the conservative analyst"
    ]  # Last response
    current_neutral_response: Annotated[
        str, "Latest response by the neutral analyst"
    ]  # Last response
    judge_decision: Annotated[str, "Judge's decision"]
    count: Annotated[int, "Length of the current conversation"]  # Conversation length


class AgentState(MessagesState):
    company_of_interest: Annotated[str, "Company that we are interested in trading"]
    asset_type: Annotated[str, "Asset type under analysis such as stock or crypto"]
    instrument_context: Annotated[str, "Deterministic ticker identity resolved at run start"]
    trade_date: Annotated[str, "What date we are trading at"]

    # Londres deterministic market-structure context
    analysis_mode: Annotated[str, "Londres analysis horizon: SWING or DAILY_SWING"]
    weekly_profile_state: Annotated[
        dict, "Deterministic weekly profile, day type, expected OLHC/OHLC delivery, and status"
    ]
    htf_order_flow_state: Annotated[
        dict, "Deterministic multi-timeframe IOF/IOFC control map"
    ]
    daily_profile_state: Annotated[
        dict, "Deterministic fixed-UTC-4 Daily OLHC/OHLC profile and phase"
    ]
    h4_profile_state: Annotated[
        dict, "Deterministic H4 profile including 18/22/02/06/10/14 candles and 06:00 driver"
    ]
    profile_stack_state: Annotated[
        dict, "Combined Weekly -> Daily -> H4 deterministic profile inheritance"
    ]
    time_price_state: Annotated[
        dict, "Deterministic opening-price and ONS map including EQ and projection levels"
    ]
    liquidity_state: Annotated[
        dict,
        "Deterministic BSL/SSL, IRL/ERL, raids, protected liquidity, and active draw map",
    ]
    smt_state: Annotated[
        dict,
        "Deterministic SMT divergence with polarity normalization and CSD+IOF validation state",
    ]
    csd_state: Annotated[
        dict,
        "Deterministic CSD events from liquidity raid through opposing-close candle open reclaim",
    ]
    post_csd_iofc_state: Annotated[
        dict,
        "Deterministic IOFC formed only from a new opposing-close range after confirmed CSD",
    ]
    execution_gate_state: Annotated[
        dict,
        "SMT -> CSD -> post-CSD IOFC validation gate and directional state",
    ]
    bias_narrative_state: Annotated[
        dict, "HTF WHAT / LTF WHEN, curve, previous candle draw and parent control"
    ]
    fair_value_state: Annotated[
        dict, "Per-timeframe FVG lifecycle and structural fair-valuation/pairing evidence"
    ]
    price_delivery_state: Annotated[
        dict, "Observed price-delivery cycle and engineer/neutralize/distribute/rebalance sequence"
    ]
    liquidity_run_state: Annotated[
        dict, "Local and parent-relative LRLR/HRLR with parent matrix boundaries"
    ]
    narrative_gate_state: Annotated[
        dict, "Phase 6 confirmation plus directional narrative context; not an order instruction"
    ]
    mmxm_state: Annotated[
        dict,
        "Deterministic OC -> matrix -> Smart Money Reversal -> terminal MMXM hierarchy",
    ]
    entry_model_state: Annotated[
        dict,
        "WAIT/REVERSAL_READY/CONTINUATION_READY/INVALIDATED context; never broker authorization",
    ]
    trade_plan_state: Annotated[
        dict,
        "Direction, CSD protected invalidation, MMXM objective and eligible post-confirmation locations",
    ]
    stop_options_state: Annotated[
        dict,
        "Deterministic IOF-range and SMT-protected structural stop anchors available to the Trader",
    ]
    trader_stop_selection_state: Annotated[
        dict,
        "Hard-validated Trader choice of structural stop and approved 3%/5%/10% account-risk tier",
    ]
    risk_sizing_state: Annotated[
        dict,
        "Deterministic pips/ticks stop-range sizing using the selected 3%/5%/10% risk tier with a 10% hard ceiling",
    ]
    londres_context_state: Annotated[
        dict,
        "Combined Weekly -> Daily -> H4 -> Time & Price -> Liquidity -> SMT -> CSD -> IOFC -> MMXM -> trade-plan -> stop/risk-selection context",
    ]

    sender: Annotated[str, "Agent that sent this message"]

    # research step
    market_report: Annotated[str, "Report from the Market Analyst"]
    sentiment_report: Annotated[str, "Report from the Sentiment Analyst"]
    news_report: Annotated[
        str, "Report from the News Researcher of current world affairs"
    ]
    fundamentals_report: Annotated[str, "Report from the Fundamentals Researcher"]

    # researcher team discussion step
    investment_debate_state: Annotated[
        InvestDebateState, "Current state of the debate on if to invest or not"
    ]
    investment_plan: Annotated[str, "Plan generated by the Analyst"]

    trader_investment_plan: Annotated[str, "Plan generated by the Trader"]

    # risk management team discussion step
    risk_debate_state: Annotated[
        RiskDebateState, "Current state of the debate on evaluating risk"
    ]
    final_trade_decision: Annotated[str, "Final decision made by the Risk Analysts"]
    past_context: Annotated[
        str,
        "Memory log context injected at run start (same-ticker decisions + cross-ticker lessons)",
    ]
