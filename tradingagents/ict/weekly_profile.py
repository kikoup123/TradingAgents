from __future__ import annotations

import pandas as pd

from .models import (
    DayType,
    Direction,
    ProfileStatus,
    WeeklyExtreme,
    WeeklyProfileResult,
    WeeklyProfileType,
)
from .order_flow import _normalize_ohlc

_DAY_ORDER = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
}


class WeeklyProfileEngine:
    """Classify the active Londres weekly profile from deterministic inputs.

    Input bars must already represent the user's canonical trading day. In the
    planned Time & Price engine that means daily candles rolled at the configured
    Londres day boundary (fixed UTC-4 by default), not arbitrary broker/calendar
    daily candles.

    The engine deliberately separates an observed weekly-extreme candidate from
    a confirmed/protected weekly extreme. Day-of-week alone can never confirm a
    profile.
    """

    def analyze(
        self,
        daily_bars: pd.DataFrame,
        *,
        htf_control: Direction,
        protected_weekly_extreme: bool | None = None,
        opposing_draw_reached: bool | None = None,
        thursday_external_manipulation: bool | None = None,
    ) -> WeeklyProfileResult:
        data = _normalize_ohlc(daily_bars)
        if len(data) > 5:
            data = data.iloc[-5:].copy()

        days = [self._day_name(index_value) for index_value in data.index]
        if any(day not in _DAY_ORDER for day in days):
            raise ValueError(
                "daily_bars must represent Monday-Friday trading days with a datetime index"
            )

        current_day = days[-1]

        if htf_control not in {Direction.BULLISH, Direction.BEARISH}:
            return WeeklyProfileResult(
                profile=WeeklyProfileType.UNRESOLVED,
                status=ProfileStatus.UNRESOLVED,
                direction=htf_control,
                current_day=current_day,
                current_day_type=DayType.UNRESOLVED,
                week_phase="ORDER_FLOW_UNRESOLVED",
                weekly_extreme=None,
                expected_daily_delivery=None,
                expected_next_phase="WAIT_FOR_HTF_ORDER_FLOW_CONTROL",
                reason_codes=["HTF_ORDER_FLOW_NOT_DIRECTIONAL"],
            )

        if htf_control == Direction.BULLISH:
            extreme_position = int(data["low"].to_numpy().argmin())
            extreme_type = "LOW"
            extreme_price = float(data.iloc[extreme_position]["low"])
            expected_daily_delivery = "OLHC"
        else:
            extreme_position = int(data["high"].to_numpy().argmax())
            extreme_type = "HIGH"
            extreme_price = float(data.iloc[extreme_position]["high"])
            expected_daily_delivery = "OHLC"

        extreme_day = days[extreme_position]
        weekly_extreme = WeeklyExtreme(
            extreme_type=extreme_type,
            day=extreme_day,
            price=extreme_price,
            protected=protected_weekly_extreme,
        )

        profile = self._profile_for_day(
            extreme_day,
            current_day=current_day,
            thursday_external_manipulation=thursday_external_manipulation,
        )

        status = self._status(profile, protected_weekly_extreme)
        day_type, week_phase, next_phase, reason_codes = self._phase_logic(
            profile=profile,
            status=status,
            extreme_day=extreme_day,
            current_day=current_day,
            opposing_draw_reached=opposing_draw_reached,
        )

        reason_codes.insert(0, f"OBSERVED_WEEKLY_{extreme_type}_ON_{extreme_day}")
        reason_codes.insert(1, f"HTF_CONTROL_{htf_control.value}")

        if protected_weekly_extreme is True:
            reason_codes.append("WEEKLY_EXTREME_PROTECTED")
        elif protected_weekly_extreme is False:
            reason_codes.append("WEEKLY_EXTREME_FAILED_PROTECTION")
        else:
            reason_codes.append("WEEKLY_EXTREME_PROTECTION_PENDING")

        return WeeklyProfileResult(
            profile=profile,
            status=status,
            direction=htf_control,
            current_day=current_day,
            current_day_type=day_type,
            week_phase=week_phase,
            weekly_extreme=weekly_extreme,
            expected_daily_delivery=expected_daily_delivery,
            expected_next_phase=next_phase,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _day_name(index_value: object) -> str:
        if not hasattr(index_value, "day_name"):
            try:
                index_value = pd.Timestamp(index_value)
            except Exception as exc:  # pragma: no cover - defensive boundary
                raise ValueError("daily_bars index must be datetime-like") from exc
        return index_value.day_name().upper()

    @staticmethod
    def _profile_for_day(
        extreme_day: str,
        *,
        current_day: str,
        thursday_external_manipulation: bool | None,
    ) -> WeeklyProfileType:
        if extreme_day in {"MONDAY", "TUESDAY"}:
            return WeeklyProfileType.CLASSIC_EXPANSION
        if extreme_day == "WEDNESDAY":
            return WeeklyProfileType.MIDWEEK_REVERSAL
        if extreme_day == "THURSDAY":
            return WeeklyProfileType.THURSDAY_REVERSAL
        if (
            extreme_day == "FRIDAY"
            and current_day == "FRIDAY"
            and thursday_external_manipulation is True
        ):
            return WeeklyProfileType.FRIDAY_REVERSAL
        return WeeklyProfileType.UNRESOLVED

    @staticmethod
    def _status(
        profile: WeeklyProfileType, protected_weekly_extreme: bool | None
    ) -> ProfileStatus:
        if profile == WeeklyProfileType.UNRESOLVED:
            return ProfileStatus.UNRESOLVED
        if protected_weekly_extreme is True:
            return ProfileStatus.CONFIRMED
        if protected_weekly_extreme is False:
            return ProfileStatus.INVALIDATED
        return ProfileStatus.DEVELOPING

    @staticmethod
    def _phase_logic(
        *,
        profile: WeeklyProfileType,
        status: ProfileStatus,
        extreme_day: str,
        current_day: str,
        opposing_draw_reached: bool | None,
    ) -> tuple[DayType, str, str | None, list[str]]:
        reason_codes: list[str] = []

        if profile == WeeklyProfileType.UNRESOLVED:
            return (
                DayType.UNRESOLVED,
                "UNRESOLVED",
                "WAIT_FOR_VALID_WEEKLY_PROFILE",
                ["NO_SUPPORTED_WEEKLY_PROFILE_YET"],
            )

        if status == ProfileStatus.INVALIDATED:
            return (
                DayType.UNRESOLVED,
                "PROFILE_INVALIDATED",
                "RECLASSIFY_WEEKLY_PROFILE",
                ["PROTECTED_EXTREME_INVALIDATED"],
            )

        current_order = _DAY_ORDER[current_day]
        extreme_order = _DAY_ORDER[extreme_day]

        if current_order == extreme_order:
            return (
                DayType.REVERSAL_CANDIDATE,
                "REVERSAL_FORMATION",
                "WAIT_FOR_PROTECTION_AND_DIRECTIONAL_DELIVERY",
                ["CURRENT_DAY_IS_WEEKLY_EXTREME_CANDIDATE"],
            )

        if current_order < extreme_order:
            return (
                DayType.RANGE,
                "PRE_REVERSAL_RANGE",
                f"WATCH_{extreme_day}_FOR_REVERSAL",
                ["PROFILE_EXTREME_LIES_LATER_IN_WEEK"],
            )

        if status != ProfileStatus.CONFIRMED:
            return (
                DayType.UNRESOLVED,
                "POST_EXTREME_UNCONFIRMED",
                "WAIT_FOR_WEEKLY_EXTREME_PROTECTION",
                ["EXTREME_EXISTS_BUT_IS_NOT_YET_PROTECTED"],
            )

        if profile == WeeklyProfileType.CLASSIC_EXPANSION:
            if current_day == "FRIDAY":
                reason_codes.append("CLASSIC_EXPANSION_FRIDAY_RETRACEMENT_BIAS")
                return (
                    DayType.RETRACEMENT_CANDIDATE,
                    "FRIDAY_REBALANCE",
                    "REASSESS_RETRACEMENT_VS_UNFINISHED_DRAW",
                    reason_codes,
                )
            return (
                DayType.CONTINUATION_CANDIDATE,
                "WEEKLY_EXPANSION",
                "CONTINUATION_TOWARD_OPPOSING_WEEKLY_DRAW",
                ["EARLY_WEEK_EXTREME_PROTECTED"],
            )

        if profile == WeeklyProfileType.MIDWEEK_REVERSAL:
            return (
                DayType.CONTINUATION_CANDIDATE,
                "POST_MIDWEEK_EXPANSION",
                "CONTINUATION_TOWARD_OPPOSING_WEEKLY_DRAW",
                ["WEDNESDAY_EXTREME_PROTECTED"],
            )

        if profile == WeeklyProfileType.THURSDAY_REVERSAL and current_day == "FRIDAY":
            if opposing_draw_reached is False:
                return (
                    DayType.CONTINUATION_CANDIDATE,
                    "FRIDAY_CONTINUATION",
                    "CONTINUE_TOWARD_UNFINISHED_OPPOSING_DRAW",
                    ["THURSDAY_REVERSAL_CONFIRMED", "OPPOSING_DRAW_UNREACHED"],
                )
            if opposing_draw_reached is True:
                return (
                    DayType.RETURN_TO_RANGE_CANDIDATE,
                    "FRIDAY_RETURN_TO_RANGE",
                    "LOOK_FOR_REBALANCE_AFTER_DRAW_COMPLETION",
                    ["THURSDAY_REVERSAL_CONFIRMED", "OPPOSING_DRAW_REACHED"],
                )
            return (
                DayType.WAITING_FOR_DRAW_STATUS,
                "FRIDAY_BRANCH_PENDING",
                "RESOLVE_OPPOSING_DRAW_STATUS",
                ["THURSDAY_REVERSAL_CONFIRMED", "OPPOSING_DRAW_STATUS_UNKNOWN"],
            )

        if profile == WeeklyProfileType.FRIDAY_REVERSAL:
            return (
                DayType.REVERSAL_CANDIDATE,
                "DELAYED_FRIDAY_REVERSAL",
                "WAIT_FOR_FRIDAY_REVERSAL_CONFIRMATION",
                ["THURSDAY_EXTERNAL_MANIPULATION_WITHOUT_EARLIER_EXTREME_PROTECTION"],
            )

        return (
            DayType.CONTINUATION_CANDIDATE,
            "EXPANSION",
            "CONTINUE_WITH_CONFIRMED_WEEKLY_DIRECTION",
            reason_codes,
        )
