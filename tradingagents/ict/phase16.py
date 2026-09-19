"""Phase 16: structural break-even management after Phase 15 trade calculation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .break_even import StructuralBreakEvenEngine
from .narrative import DEFAULT_HIERARCHY
from .phase15 import LondresPhase15Engine
from .risk_sizing import InstrumentRiskSpec


class LondresPhase16Engine:
    """Add structural break-even management to the deterministic trade stack.

    Break-even moves the current stop to exact entry only after:
    1. price body-closes outside the post-CSD IOF entry range in trade direction;
    2. a new confirmed execution-timeframe fractal forms after that exit; and
    3. a later body close breaks that fractal in the active IOF direction.

    Transaction costs are not compensated in the break-even stop.  This phase
    remains pre-broker and never authorizes an order.
    """

    def __init__(self, *, break_even_pivot_span: int = 2) -> None:
        self.phase15 = LondresPhase15Engine()
        self.break_even = StructuralBreakEvenEngine(pivot_span=break_even_pivot_span)

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        trader_stop_selection: dict | None = None,
        instrument_risk_spec: InstrumentRiskSpec | None = None,
        account_equity: float | None = None,
        stop_buffer_ticks: int | None = None,
        max_risk_cash: float | None = None,
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase15.analyze(
            timeframe_bars=timeframe_bars,
            trader_stop_selection=trader_stop_selection,
            instrument_risk_spec=instrument_risk_spec,
            account_equity=account_equity,
            stop_buffer_ticks=stop_buffer_ticks,
            max_risk_cash=max_risk_cash,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        execution_timeframe = context["bias_narrative"]["execution_timeframe"]
        break_even = self.break_even.analyze(
            entry_execution=context.get("entry_execution"),
            trade_calculation=context.get("trade_calculation"),
            executable_stop=context.get("executable_stop"),
            execution_bars=timeframe_bars[execution_timeframe],
            as_of=phase6_inputs.get("as_of"),
        ).to_dict()

        package = dict(context.get("execution_package") or {})
        package["break_even_management"] = break_even
        package["current_stop_price"] = break_even.get("current_stop_price")
        package["original_stop_price"] = break_even.get("original_stop_price")
        package["order_authorized"] = False

        return {
            **context,
            "phase": "LONDRES_STRUCTURAL_BREAK_EVEN_MANAGEMENT_STACK",
            "break_even_management": break_even,
            "execution_package": package,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase15Engine.state_update(context)
        update["break_even_management_state"] = context["break_even_management"]
        update["execution_package_state"] = context["execution_package"]
        return update
