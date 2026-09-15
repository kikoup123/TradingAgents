from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .csd import CSDEngine, CSDEvent
from .models import Direction, H4LocationContext, IOFCResult
from .order_flow import OrderFlowEngine
from .phase4 import LondresPhase4Engine
from .smt import SMTEngine, SMTGroupConfig


class LondresPhase6Engine:
    """Orchestrate the deterministic stack through CSD + post-CSD IOFC.

    Phase 6 removes manual CSD validation from the SMT gate. The sequence is:

        liquidity context -> SMT detected -> CSD after SMT -> new post-CSD IOFC
        -> SMT validated

    The IOF used to validate SMT here is specifically the user's post-CSD IOFC
    rule: after CSD, a NEW opposing-close range must form and later receive body
    acceptance through its full range in the CSD direction.
    """

    def __init__(self) -> None:
        self.phase4 = LondresPhase4Engine()
        self.csd = CSDEngine()
        self.order_flow = OrderFlowEngine()
        self.smt = SMTEngine()

    def analyze(
        self,
        *,
        timeframe_bars: Mapping[str, pd.DataFrame],
        intraday_bars: pd.DataFrame,
        minute_bars: pd.DataFrame,
        csd_bars: pd.DataFrame,
        csd_timeframe: str,
        smt_bars: dict[str, pd.DataFrame],
        smt_group: str | SMTGroupConfig,
        smt_timeframe: str,
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

        csd_result = self.csd.analyze(
            csd_bars,
            timeframe=csd_timeframe,
            as_of=as_of,
        )

        smt_probe = self.smt.analyze(
            smt_bars,
            group=smt_group,
            timeframe=smt_timeframe,
            csd_direction=Direction.UNCONFIRMED,
            iof_direction=Direction.UNCONFIRMED,
            as_of=as_of,
        )

        validation_csd = self._validation_csd(
            csd_result.events,
            smt_detected=smt_probe.detected,
            smt_reference_time=smt_probe.reference_time,
        )
        post_csd_iofc = self._post_csd_iofc(csd_bars, validation_csd)

        csd_direction = (
            validation_csd.direction
            if validation_csd is not None
            else Direction.UNCONFIRMED
        )
        iof_direction = (
            post_csd_iofc.expected_direction
            if post_csd_iofc is not None and post_csd_iofc.confirmed
            else Direction.UNCONFIRMED
        )

        smt = self.smt.analyze(
            smt_bars,
            group=smt_group,
            timeframe=smt_timeframe,
            csd_direction=csd_direction,
            iof_direction=iof_direction,
            as_of=as_of,
        )

        return {
            "phase": "WEEKLY_DAILY_H4_TIME_PRICE_LIQUIDITY_SMT_CSD_IOFC_STACK",
            "canonical_clock": "UTC-4_FIXED",
            "profile_stack": context["profile_stack"],
            "time_price": context["time_price"],
            "liquidity_timeframe": context["liquidity_timeframe"],
            "liquidity": context["liquidity"],
            "csd_timeframe": csd_timeframe,
            "csd": csd_result.to_dict(),
            "validation_csd": validation_csd.to_dict() if validation_csd else None,
            "post_csd_iofc": (
                post_csd_iofc.to_dict() if post_csd_iofc is not None else None
            ),
            "smt_timeframe": smt_timeframe,
            "smt": smt.to_dict(),
            "execution_gate": {
                "smt_detected": smt.detected,
                "csd_confirmed_after_smt": validation_csd is not None,
                "post_csd_iofc_confirmed": bool(
                    post_csd_iofc is not None and post_csd_iofc.confirmed
                ),
                "smt_validated": smt.validated,
                "direction": smt.direction.value,
                "state": smt.validation_state.value,
            },
        }

    @staticmethod
    def _validation_csd(
        events: list[CSDEvent],
        *,
        smt_detected: bool,
        smt_reference_time: str | None,
    ) -> CSDEvent | None:
        if not smt_detected or smt_reference_time is None:
            return None

        cutoff = pd.Timestamp(smt_reference_time)
        eligible: list[CSDEvent] = []
        for event in events:
            event_time = pd.Timestamp(event.confirmation_time)
            comparison_cutoff = cutoff
            if event_time.tzinfo is not None and cutoff.tzinfo is None:
                comparison_cutoff = cutoff.tz_localize(event_time.tzinfo)
            elif event_time.tzinfo is None and cutoff.tzinfo is not None:
                comparison_cutoff = cutoff.tz_localize(None)
            elif event_time.tzinfo is not None and cutoff.tzinfo is not None:
                comparison_cutoff = cutoff.tz_convert(event_time.tzinfo)
            if event_time >= comparison_cutoff:
                eligible.append(event)

        return eligible[-1] if eligible else None

    def _post_csd_iofc(
        self,
        csd_bars: pd.DataFrame,
        event: CSDEvent | None,
    ) -> IOFCResult | None:
        if event is None:
            return None
        return self.order_flow.find_iofc_after(
            csd_bars,
            anchor_position=event.confirmation_position,
            expected_direction=event.direction,
        )
