from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .liquidity import LiquidityEngine
from .models import Direction, H4LocationContext
from .phase3 import LondresPhase3Engine


class LondresPhase4Engine:
    """Orchestrate Weekly -> Daily -> H4 -> Time & Price -> Liquidity context."""

    def __init__(self) -> None:
        self.phase3 = LondresPhase3Engine()
        self.liquidity = LiquidityEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        intraday_bars: pd.DataFrame,
        minute_bars: pd.DataFrame,
        liquidity_timeframe: str = "4H",
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
        if liquidity_timeframe not in timeframe_bars:
            raise ValueError(f"Missing {liquidity_timeframe} bars required for Phase 4")

        context = self.phase3.analyze(
            timeframe_bars=timeframe_bars,
            intraday_bars=intraday_bars,
            minute_bars=minute_bars,
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
            as_of=as_of,
            current_price=current_price,
        )

        profile_stack = context["profile_stack"]
        order_flow_payload = profile_stack.get("order_flow", {}).get(liquidity_timeframe, {})
        control_value = order_flow_payload.get("control", Direction.UNCONFIRMED.value)
        try:
            control = Direction(control_value)
        except ValueError:
            control = Direction.UNCONFIRMED

        liquidity = self.liquidity.analyze(
            timeframe_bars[liquidity_timeframe],
            timeframe=liquidity_timeframe,
            order_flow_control=control,
            time_price_state=context["time_price"],
        )

        return {
            "phase": "WEEKLY_DAILY_H4_TIME_PRICE_LIQUIDITY_STACK",
            "canonical_clock": "UTC-4_FIXED",
            "profile_stack": profile_stack,
            "time_price": context["time_price"],
            "liquidity_timeframe": liquidity_timeframe,
            "liquidity": liquidity.to_dict(),
        }
