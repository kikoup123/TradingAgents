from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .market_data import analysis_time, closed_bars
from .models import Direction, H4LocationContext
from .phase4 import LondresPhase4Engine
from .smt import SMTEngine, SMTGroupConfig


class LondresPhase5Engine:
    """Orchestrate the deterministic stack through SMT validation.

    Phase 5 detects structural SMT across synchronized correlated instruments.
    SMT never authorizes a trade by itself. Validation requires:

        SMT direction == confirmed CSD direction == confirmed IOF direction

    CSD detection remains a separate deterministic engine; until that engine is
    wired in, Phase 5 accepts an explicit CSD direction from the caller rather
    than inventing a CSD from price data.
    """

    def __init__(self) -> None:
        self.phase4 = LondresPhase4Engine()
        self.smt = SMTEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        intraday_bars: pd.DataFrame,
        minute_bars: pd.DataFrame,
        smt_bars: dict[str, pd.DataFrame],
        smt_group: str | SMTGroupConfig,
        smt_timeframe: str,
        csd_direction: Direction = Direction.UNCONFIRMED,
        smt_validation_timeframe: str = "4H",
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
        # Phase 5 must use the same causal prefix on every branch. Without this
        # boundary, direct Phase 5 calls could let future profile/IOFC or
        # liquidity observations validate an SMT event at an earlier cutoff.
        if as_of is not None:
            as_of = analysis_time(as_of)
        timeframe_bars = {
            timeframe: closed_bars(bars, as_of)
            for timeframe, bars in timeframe_bars.items()
        }
        intraday_bars = closed_bars(intraday_bars, as_of)
        minute_bars = closed_bars(minute_bars, as_of)
        smt_bars = {
            symbol: closed_bars(bars, as_of) for symbol, bars in smt_bars.items()
        }

        context = self.phase4.analyze(
            timeframe_bars=timeframe_bars,
            intraday_bars=intraday_bars,
            minute_bars=minute_bars,
            liquidity_timeframe=liquidity_timeframe,
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

        order_flow = context["profile_stack"].get("order_flow", {})
        validation_payload = order_flow.get(smt_validation_timeframe, {})
        control_value = validation_payload.get("control", Direction.UNCONFIRMED.value)
        try:
            iof_direction = Direction(control_value)
        except ValueError:
            iof_direction = Direction.UNCONFIRMED

        smt = self.smt.analyze(
            smt_bars,
            group=smt_group,
            timeframe=smt_timeframe,
            csd_direction=csd_direction,
            iof_direction=iof_direction,
            as_of=as_of,
        )

        return {
            "phase": "WEEKLY_DAILY_H4_TIME_PRICE_LIQUIDITY_SMT_STACK",
            "canonical_clock": "UTC-4_FIXED",
            "profile_stack": context["profile_stack"],
            "time_price": context["time_price"],
            "liquidity_timeframe": context["liquidity_timeframe"],
            "liquidity": context["liquidity"],
            "smt_timeframe": smt_timeframe,
            "smt_validation_timeframe": smt_validation_timeframe,
            "smt": smt.to_dict(),
        }
