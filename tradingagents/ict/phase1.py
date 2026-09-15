from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .models import Direction
from .order_flow import OrderFlowEngine
from .weekly_profile import WeeklyProfileEngine


class LondresPhase1Engine:
    """Phase 1 orchestration: HTF IOF control -> weekly profile -> day type.

    This is deliberately broker/data-source agnostic. Later phases will feed it
    canonical candles from the Londres Time & Price engine/cTrader adapter.
    """

    def __init__(self) -> None:
        self.order_flow = OrderFlowEngine()
        self.weekly_profile = WeeklyProfileEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        weekly_profile_timeframe: str = "1D",
        order_flow_control_timeframe: str = "4H",
        protected_weekly_extreme: bool | None = None,
        opposing_draw_reached: bool | None = None,
        thursday_external_manipulation: bool | None = None,
    ) -> dict:
        if weekly_profile_timeframe not in timeframe_bars:
            raise ValueError(f"Missing {weekly_profile_timeframe} bars for weekly profile")
        if order_flow_control_timeframe not in timeframe_bars:
            raise ValueError(
                f"Missing {order_flow_control_timeframe} bars for HTF order-flow control"
            )

        order_flow_results = self.order_flow.analyze_timeframes(timeframe_bars)
        control = order_flow_results[order_flow_control_timeframe].control

        weekly = self.weekly_profile.analyze(
            timeframe_bars[weekly_profile_timeframe],
            htf_control=control,
            protected_weekly_extreme=protected_weekly_extreme,
            opposing_draw_reached=opposing_draw_reached,
            thursday_external_manipulation=thursday_external_manipulation,
        )

        return {
            "phase": "WEEKLY_PROFILE_AND_HTF_ORDER_FLOW",
            "htf_control_timeframe": order_flow_control_timeframe,
            "htf_control": control.value,
            "order_flow": {
                timeframe: result.to_dict()
                for timeframe, result in order_flow_results.items()
            },
            "weekly_profile": weekly.to_dict(),
        }

    @staticmethod
    def control_alignment(result: dict) -> str:
        controls = [
            item.get("control")
            for item in result.get("order_flow", {}).values()
            if item.get("control") in {Direction.BULLISH.value, Direction.BEARISH.value}
        ]
        if not controls:
            return Direction.UNCONFIRMED.value
        if all(value == Direction.BULLISH.value for value in controls):
            return Direction.BULLISH.value
        if all(value == Direction.BEARISH.value for value in controls):
            return Direction.BEARISH.value
        return "MIXED"
