"""Phase 14: executable broker-stop geometry from the selected structural anchor."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .executable_stop import ExecutableStopEngine
from .narrative import DEFAULT_HIERARCHY
from .phase13 import LondresPhase13Engine


class LondresPhase14Engine:
    """Add an executable stop only after entry and structural-stop selection exist.

    No buffer is assumed. The caller must explicitly supply the broker symbol's
    tick size and the stop-buffer rule in whole ticks. Until then this layer
    fails closed and keeps risk sizing blocked.
    """

    def __init__(self) -> None:
        self.phase13 = LondresPhase13Engine()
        self.executable_stop = ExecutableStopEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        trader_stop_selection: dict | None = None,
        instrument_tick_size: float | None = None,
        stop_buffer_ticks: int | None = None,
        hierarchy: Sequence[str] = DEFAULT_HIERARCHY,
        **phase6_inputs,
    ) -> dict:
        context = self.phase13.analyze(
            timeframe_bars=timeframe_bars,
            hierarchy=hierarchy,
            **phase6_inputs,
        )
        stop = self.executable_stop.calculate(
            entry_execution=context.get("entry_execution"),
            trader_stop_selection=trader_stop_selection,
            tick_size=instrument_tick_size,
            buffer_ticks=stop_buffer_ticks,
        )
        stop_dict = stop.to_dict()

        package = dict(context.get("execution_package") or {})
        package["executable_stop"] = stop_dict
        package["risk_sizing_ready"] = bool(stop_dict.get("risk_sizing_ready"))
        package["risk_sizing_blocker"] = (
            None
            if stop_dict.get("risk_sizing_ready")
            else stop_dict.get("status")
        )
        package["order_authorized"] = False

        return {
            **context,
            "phase": "LONDRES_EXECUTABLE_STOP_BUFFER_STACK",
            "executable_stop": stop_dict,
            "execution_package": package,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        update = LondresPhase13Engine.state_update(context)
        update["executable_stop_state"] = context["executable_stop"]
        update["execution_package_state"] = context["execution_package"]
        return update
