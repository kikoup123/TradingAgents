from __future__ import annotations

from datetime import timedelta, timezone

import pandas as pd

from .models import (
    DailyDelivery,
    DailyPhase,
    DailyProfileResult,
    DayType,
    Direction,
    ProfileStatus,
    WeeklyProfileResult,
)
from .order_flow import _normalize_ohlc

FIXED_UTC_MINUS_4 = timezone(timedelta(hours=-4))
DAILY_ROLLOVER_HOUR = 18


class DailyProfileEngine:
    """Build the current Londres daily candle from intraday OHLC data.

    The canonical clock is fixed UTC-4. This is intentionally *not* New York
    DST time. If the input index is timezone-aware it is converted to UTC-4. If
    it is naive, it is interpreted as already expressed in the fixed UTC-4
    trading clock.

    A Londres trading day starts at 18:00 UTC-4 and ends immediately before the
    following 18:00. Weekly profile context determines whether the day is a
    continuation, reversal, retracement, or unresolved candidate; actual body
    delivery and order-flow control determine whether the hypothesis confirms.
    """

    def analyze(
        self,
        intraday_bars: pd.DataFrame,
        *,
        weekly_profile: WeeklyProfileResult,
        daily_order_flow_control: Direction = Direction.UNCONFIRMED,
        protected_daily_extreme: bool | None = None,
        opposing_draw_reached: bool | None = None,
    ) -> DailyProfileResult:
        data = self._canonicalize(_normalize_ohlc(intraday_bars))
        if data.empty:
            raise ValueError("intraday_bars must contain at least one bar")

        day_keys = [self._trading_day_key(ts) for ts in data.index]
        current_key = day_keys[-1]
        mask = [key == current_key for key in day_keys]
        current = data.loc[mask].copy()

        daily_open = float(current.iloc[0]["open"])
        daily_high = float(current["high"].max())
        daily_low = float(current["low"].min())
        current_close = float(current.iloc[-1]["close"])

        high_position = int(current["high"].to_numpy().argmax())
        low_position = int(current["low"].to_numpy().argmin())
        high_time = current.index[high_position]
        low_time = current.index[low_position]

        observed_delivery = self._observed_delivery(high_position, low_position)
        expected_direction, expected_delivery = self._expected_delivery(weekly_profile)

        status = self._status(
            expected_direction=expected_direction,
            expected_delivery=expected_delivery,
            observed_delivery=observed_delivery,
            order_flow_control=daily_order_flow_control,
            protected_daily_extreme=protected_daily_extreme,
        )
        phase = self._phase(
            day_type=weekly_profile.current_day_type,
            expected_delivery=expected_delivery,
            expected_direction=expected_direction,
            status=status,
            daily_open=daily_open,
            current_close=current_close,
            high_position=high_position,
            low_position=low_position,
            opposing_draw_reached=opposing_draw_reached,
        )

        reason_codes = [
            f"WEEKLY_DAY_TYPE_{weekly_profile.current_day_type.value}",
            f"OBSERVED_DELIVERY_{observed_delivery.value}",
            f"DAILY_ORDER_FLOW_{daily_order_flow_control.value}",
        ]
        if expected_delivery != DailyDelivery.UNRESOLVED:
            reason_codes.append(f"EXPECTED_DELIVERY_{expected_delivery.value}")
        if protected_daily_extreme is True:
            reason_codes.append("DAILY_EXTREME_PROTECTED")
        elif protected_daily_extreme is False:
            reason_codes.append("DAILY_EXTREME_FAILED_PROTECTION")
        else:
            reason_codes.append("DAILY_EXTREME_PROTECTION_PENDING")
        if opposing_draw_reached is True:
            reason_codes.append("OPPOSING_DRAW_REACHED")
        elif opposing_draw_reached is False:
            reason_codes.append("OPPOSING_DRAW_UNREACHED")

        return DailyProfileResult(
            trading_day=current_key.isoformat(),
            direction=expected_direction,
            day_type=weekly_profile.current_day_type,
            expected_delivery=expected_delivery,
            observed_delivery=observed_delivery,
            status=status,
            phase=phase,
            daily_open=daily_open,
            daily_high=daily_high,
            daily_low=daily_low,
            current_close=current_close,
            high_time=high_time.isoformat(),
            low_time=low_time.isoformat(),
            protected_extreme=protected_daily_extreme,
            expected_next_phase=self._next_phase(phase, opposing_draw_reached),
            reason_codes=reason_codes,
        )

    @staticmethod
    def _canonicalize(data: pd.DataFrame) -> pd.DataFrame:
        result = data.copy()
        index = pd.DatetimeIndex(result.index)
        if index.tz is None:
            index = index.tz_localize(FIXED_UTC_MINUS_4)
        else:
            index = index.tz_convert(FIXED_UTC_MINUS_4)
        result.index = index
        return result.sort_index()

    @staticmethod
    def _trading_day_key(ts: pd.Timestamp):
        local = pd.Timestamp(ts)
        if local.hour >= DAILY_ROLLOVER_HOUR:
            return local.date()
        return (local - pd.Timedelta(days=1)).date()

    @staticmethod
    def _observed_delivery(high_position: int, low_position: int) -> DailyDelivery:
        if low_position < high_position:
            return DailyDelivery.OLHC
        if high_position < low_position:
            return DailyDelivery.OHLC
        return DailyDelivery.UNRESOLVED

    @staticmethod
    def _opposite(direction: Direction) -> Direction:
        if direction == Direction.BULLISH:
            return Direction.BEARISH
        if direction == Direction.BEARISH:
            return Direction.BULLISH
        return Direction.UNCONFIRMED

    @classmethod
    def _expected_delivery(
        cls, weekly_profile: WeeklyProfileResult
    ) -> tuple[Direction, DailyDelivery]:
        day_type = weekly_profile.current_day_type
        direction = weekly_profile.direction

        if direction not in {Direction.BULLISH, Direction.BEARISH}:
            return Direction.UNCONFIRMED, DailyDelivery.UNRESOLVED

        if day_type in {
            DayType.CONTINUATION_CANDIDATE,
            DayType.REVERSAL_CANDIDATE,
        }:
            return (
                direction,
                DailyDelivery.OLHC if direction == Direction.BULLISH else DailyDelivery.OHLC,
            )

        if day_type in {
            DayType.RETRACEMENT_CANDIDATE,
            DayType.RETURN_TO_RANGE_CANDIDATE,
        }:
            opposite = cls._opposite(direction)
            return (
                opposite,
                DailyDelivery.OLHC if opposite == Direction.BULLISH else DailyDelivery.OHLC,
            )

        return Direction.UNCONFIRMED, DailyDelivery.UNRESOLVED

    @staticmethod
    def _status(
        *,
        expected_direction: Direction,
        expected_delivery: DailyDelivery,
        observed_delivery: DailyDelivery,
        order_flow_control: Direction,
        protected_daily_extreme: bool | None,
    ) -> ProfileStatus:
        if expected_delivery == DailyDelivery.UNRESOLVED:
            return ProfileStatus.UNRESOLVED
        if protected_daily_extreme is False:
            return ProfileStatus.INVALIDATED
        if (
            observed_delivery == expected_delivery
            and order_flow_control == expected_direction
            and protected_daily_extreme is True
        ):
            return ProfileStatus.CONFIRMED
        return ProfileStatus.DEVELOPING

    @staticmethod
    def _phase(
        *,
        day_type: DayType,
        expected_delivery: DailyDelivery,
        expected_direction: Direction,
        status: ProfileStatus,
        daily_open: float,
        current_close: float,
        high_position: int,
        low_position: int,
        opposing_draw_reached: bool | None,
    ) -> DailyPhase:
        if status == ProfileStatus.INVALIDATED:
            return DailyPhase.INVALIDATED
        if expected_delivery == DailyDelivery.UNRESOLVED:
            return DailyPhase.UNRESOLVED
        if day_type in {
            DayType.RETRACEMENT_CANDIDATE,
            DayType.RETURN_TO_RANGE_CANDIDATE,
        }:
            return DailyPhase.RETRACEMENT
        if opposing_draw_reached is True:
            return DailyPhase.OBJECTIVE_REACHED
        if day_type == DayType.REVERSAL_CANDIDATE and status != ProfileStatus.CONFIRMED:
            return DailyPhase.REVERSAL_FORMATION
        if status == ProfileStatus.CONFIRMED:
            return DailyPhase.EXPANSION

        if expected_direction == Direction.BULLISH:
            if current_close <= daily_open and low_position <= high_position:
                return DailyPhase.MANIPULATION
            if current_close > daily_open and low_position < high_position:
                return DailyPhase.EXPANSION
        elif expected_direction == Direction.BEARISH:
            if current_close >= daily_open and high_position <= low_position:
                return DailyPhase.MANIPULATION
            if current_close < daily_open and high_position < low_position:
                return DailyPhase.EXPANSION
        return DailyPhase.OPENING

    @staticmethod
    def _next_phase(phase: DailyPhase, opposing_draw_reached: bool | None) -> str | None:
        if phase == DailyPhase.INVALIDATED:
            return "RECLASSIFY_DAILY_PROFILE"
        if phase == DailyPhase.UNRESOLVED:
            return "WAIT_FOR_WEEKLY_DAY_TYPE"
        if phase in {DailyPhase.OPENING, DailyPhase.MANIPULATION, DailyPhase.REVERSAL_FORMATION}:
            return "WAIT_FOR_DIRECTIONAL_ORDER_FLOW_AND_PROTECTED_EXTREME"
        if phase == DailyPhase.EXPANSION:
            if opposing_draw_reached is False:
                return "CONTINUE_TOWARD_OPPOSING_DRAW"
            return "MONITOR_DRAW_COMPLETION_AND_REBALANCE"
        if phase == DailyPhase.OBJECTIVE_REACHED:
            return "REASSESS_CONTINUATION_VS_RETRACEMENT"
        if phase == DailyPhase.RETRACEMENT:
            return "MONITOR_RETURN_TO_RANGE_AND_ORDER_FLOW"
        return None
