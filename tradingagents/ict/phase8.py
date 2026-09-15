"""Phase 8: MMXM recognition and non-broker entry-state contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .mmxm import MMXMEngine
from .narrative import DEFAULT_HIERARCHY
from .phase7 import LondresPhase7Engine


class LondresPhase8Engine:
    """Compose the Phase 7 narrative stack with deterministic MMXM context.

    Phase 8 does not place an order and does not size risk.  It only determines
    whether the execution stream is waiting, reversal-ready, continuation-ready
    or invalidated after MMXM and the existing SMT/CSD/IOFC gates agree.
    """

    def __init__(self) -> None:
        self.phase7 = LondresPhase7Engine()
        self.mmxm = MMXMEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase7.analyze(
            timeframe_bars=timeframe_bars,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        narrative = context["bias_narrative"]
        models = self.mmxm.analyze_hierarchy(
            dict(timeframe_bars),
            narrative=narrative,
            as_of=phase6_inputs.get("as_of"),
        )
        execution_timeframe = narrative["execution_timeframe"]
        execution_model = models[execution_timeframe]
        entry = self.mmxm.entry_contract(
            execution_model,
            narrative_gate=context["narrative_gate"],
            execution_gate=context["execution_gate"],
        )

        return {
            **context,
            "phase": "LONDRES_MMXM_ENTRY_CONTEXT_STACK",
            "mmxm": {
                "hierarchy": narrative["hierarchy"],
                "execution_timeframe": execution_timeframe,
                "models": models,
                "execution_model": execution_model,
            },
            "entry_model": entry,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase7Engine.state_update(context)
        update["mmxm_state"] = context["mmxm"]
        update["entry_model_state"] = context["entry_model"]
        return update
