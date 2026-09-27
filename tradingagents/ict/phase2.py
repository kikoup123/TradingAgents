from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .daily_profile import DailyProfileEngine
from .h4_profile import H4ProfileEngine
from .models import Direction, H4LocationContext
from .order_flow import OrderFlowEngine
from .weekly_profile import WeeklyProfileEngine


class LondresPhase2Engine:
    """Orchestrate Weekly -> Daily -> H4 deterministic profile inheritance.

    Phase 2 deliberately stops before session/opening-price/liquidity logic. It
    establishes the expected day type, Daily OLHC/OHLC hypothesis, fixed UTC-4
    H4 profile, and the 06:00 driver expectation. Later phases will add the
    user's time/process levels, liquidity, SMT, CSD, and LTF IOFC execution.
    """

    def __init__(self) -> None:
        self.order_flow = OrderFlowEngine()
        self.weekly_profile = WeeklyProfileEngine()
        self.daily_profile = DailyProfileEngine()
        self.h4_profile = H4ProfileEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        intraday_bars: pd.DataFrame,
        weekly_profile_timeframe: str = "1D",
        weekly_control_timeframe: str = "4H",
        daily_order_flow_timeframe: str = "1D",
        h4_order_flow_timeframe: str = "4H",
        protected_weekly_extreme: bool | None = None,
        protected_daily_extreme: bool | None = None,
        opposing_draw_reached: bool | None = None,
        thursday_external_manipulation: bool | None = None,
        reversal_confirmed_at: pd.Timestamp | str | None = None,
        location_context: H4LocationContext = H4LocationContext.UNKNOWN,
    ) -> dict:
        for required in {weekly_profile_timeframe, weekly_control_timeframe}:
            if required not in timeframe_bars:
                raise ValueError(f"Missing {required} bars required for Phase 2")

        order_flow = self.order_flow.analyze_timeframes(timeframe_bars)
        weekly_control = order_flow[weekly_control_timeframe].control
        daily_control = (
            order_flow[daily_order_flow_timeframe].control
            if daily_order_flow_timeframe in order_flow
            else Direction.UNCONFIRMED
        )
        h4_control = (
            order_flow[h4_order_flow_timeframe].control
            if h4_order_flow_timeframe in order_flow
            else Direction.UNCONFIRMED
        )

        weekly = self.weekly_profile.analyze(
            timeframe_bars[weekly_profile_timeframe],
            htf_control=weekly_control,
            protected_weekly_extreme=protected_weekly_extreme,
            opposing_draw_reached=opposing_draw_reached,
            thursday_external_manipulation=thursday_external_manipulation,
        )

        daily = self.daily_profile.analyze(
            intraday_bars,
            weekly_profile=weekly,
            daily_order_flow_control=daily_control,
            protected_daily_extreme=protected_daily_extreme,
            opposing_draw_reached=opposing_draw_reached,
        )

        h4 = self.h4_profile.analyze(
            intraday_bars,
            daily_profile=daily,
            h4_order_flow_control=h4_control,
            reversal_confirmed_at=reversal_confirmed_at,
            location_context=location_context,
        )

        return {
            "phase": "WEEKLY_DAILY_H4_PROFILE_STACK",
            "canonical_clock": "UTC-4_FIXED",
            "weekly_control_timeframe": weekly_control_timeframe,
            "weekly_control": weekly_control.value,
            "daily_order_flow_timeframe": daily_order_flow_timeframe,
            "daily_order_flow_control": daily_control.value,
            "h4_order_flow_timeframe": h4_order_flow_timeframe,
            "h4_order_flow_control": h4_control.value,
            "order_flow": {
                timeframe: result.to_dict() for timeframe, result in order_flow.items()
            },
            "weekly_profile": weekly.to_dict(),
            "daily_profile": daily.to_dict(),
            "h4_profile": h4.to_dict(),
        }
