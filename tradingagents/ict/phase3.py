from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .models import H4LocationContext
from .phase2 import LondresPhase2Engine
from .time_price import TimePriceEngine


class LondresPhase3Engine:
    """Orchestrate Weekly -> Daily -> H4 -> Time & Price context.

    Phase 3 adds the deterministic opening-price and ONS map to the Phase 2
    profile stack. It still stops before liquidity, SMT, CSD, IOFC execution,
    MMXM, and broker execution.
    """

    def __init__(self) -> None:
        self.phase2 = LondresPhase2Engine()
        self.time_price = TimePriceEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        intraday_bars: pd.DataFrame,
        minute_bars: pd.DataFrame,
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
        as_of: pd.Timestamp | str | None = None,
        current_price: float | None = None,
    ) -> dict:
        profile_stack = self.phase2.analyze(
            timeframe_bars=timeframe_bars,
            intraday_bars=intraday_bars,
            weekly_profile_timeframe=weekly_profile_timeframe,
            weekly_control_timeframe=weekly_control_timeframe,
            daily_order_flow_timeframe=daily_order_flow_timeframe,
            h4_order_flow_timeframe=h4_order_flow_timeframe,
            protected_weekly_extreme=protected_weekly_extreme,
            protected_daily_extreme=protected_daily_extreme,
            opposing_draw_reached=opposing_draw_reached,
            thursday_external_manipulation=thursday_external_manipulation,
            reversal_confirmed_at=reversal_confirmed_at,
            location_context=location_context,
        )

        time_price = self.time_price.analyze(
            minute_bars,
            as_of=as_of,
            current_price=current_price,
        )

        return {
            "phase": "WEEKLY_DAILY_H4_TIME_PRICE_STACK",
            "canonical_clock": "UTC-4_FIXED",
            "profile_stack": profile_stack,
            "time_price": time_price.to_dict(),
        }
