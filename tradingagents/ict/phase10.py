"""Phase 10: deterministic structural stop options for the Trader."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .narrative import DEFAULT_HIERARCHY
from .phase9 import LondresPhase9Engine
from .stop_selection import StopSelectionEngine


class LondresPhase10Engine:
    """Expose valid IOF-range and SMT-protected stop anchors after Phase 9.

    The Trader may choose between deterministic structural anchors when more
    than one is valid.  This phase still does not calculate an executable stop
    price because the required tick/buffer policy has not yet been specified,
    and it never authorizes a broker order.
    """

    def __init__(self) -> None:
        self.phase9 = LondresPhase9Engine()
        self.stop_selection = StopSelectionEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase9.analyze(
            timeframe_bars=timeframe_bars,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        execution_timeframe = context["bias_narrative"]["execution_timeframe"]
        stop_options = self.stop_selection.analyze(
            context,
            timeframe_bars[execution_timeframe],
            as_of=phase6_inputs.get("as_of"),
        )
        return {
            **context,
            "phase": "LONDRES_STRUCTURAL_STOP_SELECTION_STACK",
            "stop_options": stop_options.to_dict(),
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase9Engine.state_update(context)
        update["stop_options_state"] = context["stop_options"]
        return update
