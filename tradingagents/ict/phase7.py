"""Add transcript-derived context to the existing Phase 1–6 stack."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .daily_profile import FIXED_UTC_MINUS_4
from .market_data import closed_bars
from .narrative import DEFAULT_HIERARCHY, NarrativeEngine
from .phase6 import LondresPhase6Engine


class LondresPhase7Engine:
    def __init__(self) -> None:
        self.phase6 = LondresPhase6Engine()
        self.narrative = NarrativeEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        """Accept Phase 6 inputs plus explicit highest-to-lowest narrative hierarchy.

        Existing profile and execution fields retain their meanings. The separate
        narrative gate is context qualification, not broker/trade authorization.
        """
        context = self.phase6.analyze(timeframe_bars=timeframe_bars, **phase6_inputs)
        narrative = self.narrative.analyze(
            timeframe_bars, hierarchy=hierarchy, as_of=phase6_inputs.get("as_of")
        )
        local = narrative["timeframes"][narrative["execution_timeframe"]]
        gate = context["execution_gate"]
        same_direction = gate["direction"] == local["control"] == narrative["bias"]
        # The LTF narrative must describe the very same candle stream used for
        # execution validation; a different chart cannot authorize that setup.
        same_timeframe = narrative["execution_timeframe"] == context["csd_timeframe"]
        same_stream = False
        if same_timeframe:
            narrative_bars = closed_bars(
                timeframe_bars[context["csd_timeframe"]], phase6_inputs.get("as_of")
            )
            execution_bars = closed_bars(
                phase6_inputs["csd_bars"], phase6_inputs.get("as_of")
            )
            # Equal market instants may arrive as UTC from one provider and
            # fixed UTC-4 from another. Normalize both representations before
            # requiring exact stream equality.
            narrative_bars.index = narrative_bars.index.tz_convert(FIXED_UTC_MINUS_4)
            execution_bars.index = execution_bars.index.tz_convert(FIXED_UTC_MINUS_4)
            same_stream = narrative_bars.equals(execution_bars)
        draw = local["narrative_draw"]
        continuation_draw = bool(draw and draw.get("purpose") == "LIQUIDITY_OBJECTIVE")
        qualified = bool(
            gate["smt_validated"]
            and narrative["context_confirmed"]
            and same_direction
            and same_stream
            and continuation_draw
        )
        reasons = []
        if not gate["smt_validated"]:
            reasons.append("WAIT_FOR_SMT_CSD_POST_CSD_IOFC")
        if not narrative["context_confirmed"]:
            reasons.append("WAIT_FOR_ALIGNED_NARRATIVE_AND_BOUNDED_DRAW")
        if not same_direction:
            reasons.append("NARRATIVE_EXECUTION_DIRECTION_CONFLICT")
        if not same_stream:
            reasons.append("NARRATIVE_EXECUTION_STREAM_MISMATCH")
        if not continuation_draw:
            reasons.append("NO_CONTINUATION_LIQUIDITY_OBJECTIVE")
        return {
            **context,
            "phase": "LONDRES_TRANSCRIPT_CONTEXT_STACK",
            "bias_narrative": narrative,
            "fair_value": {tf: s["fair_value"] for tf, s in narrative["timeframes"].items()},
            "price_delivery": {
                tf: s["price_delivery"] for tf, s in narrative["timeframes"].items()
            },
            "liquidity_runs": {
                tf: {"local": s["liquidity_run"], "parent": s["parent_relative_run"]}
                for tf, s in narrative["timeframes"].items()
            },
            "narrative_gate": {
                "qualified": qualified,
                "direction": gate["direction"],
                "state": "CONTEXT_CONFIRMED" if qualified else "WAIT",
                "reason_codes": reasons,
            },
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        """Explicit LangGraph adapter; no LLM needs to reinterpret the evidence."""
        profiles = context["profile_stack"]
        return {
            "londres_context_state": context,
            "profile_stack_state": profiles,
            "weekly_profile_state": profiles["weekly_profile"],
            "daily_profile_state": profiles["daily_profile"],
            "h4_profile_state": profiles["h4_profile"],
            "htf_order_flow_state": profiles["order_flow"],
            "time_price_state": context["time_price"],
            "liquidity_state": context["liquidity"],
            "smt_state": context["smt"],
            "csd_state": context["csd"],
            "post_csd_iofc_state": context["post_csd_iofc"],
            "execution_gate_state": context["execution_gate"],
            "bias_narrative_state": context["bias_narrative"],
            "fair_value_state": context["fair_value"],
            "price_delivery_state": context["price_delivery"],
            "liquidity_run_state": context["liquidity_runs"],
            "narrative_gate_state": context["narrative_gate"],
        }
