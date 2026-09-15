"""Phase 9: structural trade-plan context before proprietary entry selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .narrative import DEFAULT_HIERARCHY
from .phase8 import LondresPhase8Engine
from .trade_plan import TradePlanEngine


class LondresPhase9Engine:
    """Add direction, invalidation, objective and eligible locations to Phase 8.

    The output is intentionally pre-entry.  It does not choose Unicorn, OTE,
    housing-candle or another proprietary trigger, and it cannot authorize an
    order.
    """

    def __init__(self) -> None:
        self.phase8 = LondresPhase8Engine()
        self.trade_plan = TradePlanEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase8.analyze(
            timeframe_bars=timeframe_bars,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        execution_timeframe = context["bias_narrative"]["execution_timeframe"]
        plan = self.trade_plan.analyze(
            context,
            timeframe_bars[execution_timeframe],
            as_of=phase6_inputs.get("as_of"),
        )
        return {
            **context,
            "phase": "LONDRES_STRUCTURAL_TRADE_PLAN_STACK",
            "trade_plan": plan.to_dict(),
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase8Engine.state_update(context)
        update["trade_plan_state"] = context["trade_plan"]
        return update
