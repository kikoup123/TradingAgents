from __future__ import annotations

import pandas as pd

from .daily_profile import DailyProfileEngine, FIXED_UTC_MINUS_4
from .models import (
    DailyProfileResult,
    Direction,
    H4CandleState,
    H4LocationContext,
    H4Phase,
    H4ProfileResult,
    H4ProfileType,
    ProfileStatus,
)
from .order_flow import _normalize_ohlc

H4_LABELS = ("18:00", "22:00", "02:00", "06:00", "10:00", "14:00")
DRIVER_INDEX = 3
DRIVER_LABEL = "06:00"


class H4ProfileEngine:
    """Classify the Londres H4 daily profile on a fixed UTC-4 clock.

    The six H4 candles of the labelled trading day are anchored at 18:00 on
    the prior calendar date, then 22:00, 02:00, 06:00, 10:00, and 14:00. The
    06:00 H4 candle is treated as the driver in Phase 2 because the supplied
    profile model distinguishes:

    * reversal established before the driver -> driver continuation;
    * no reversal before the driver -> driver reversal;
    * a completed reversal driver that fails to reverse -> invalidation.

    IRL/ERL/OPR/OB location is carried as explicit context. Detection of those
    locations belongs to the later liquidity/PD-array engine and is therefore
    not guessed here.
    """

    def analyze(
        self,
        intraday_bars: pd.DataFrame,
        *,
        daily_profile: DailyProfileResult,
        h4_order_flow_control: Direction = Direction.UNCONFIRMED,
        reversal_confirmed_at: pd.Timestamp | str | None = None,
        location_context: H4LocationContext = H4LocationContext.UNKNOWN,
    ) -> H4ProfileResult:
        data = DailyProfileEngine._canonicalize(_normalize_ohlc(intraday_bars))
        if data.empty:
            raise ValueError("intraday_bars must contain at least one bar")

        day_keys = [DailyProfileEngine._trading_day_key(ts) for ts in data.index]
        current_key = day_keys[-1]
        current = data.loc[[key == current_key for key in day_keys]].copy()
        session_start_date = pd.Timestamp(current_key) - pd.Timedelta(days=1)
        day_start = pd.Timestamp(
            year=session_start_date.year,
            month=session_start_date.month,
            day=session_start_date.day,
            hour=18,
            tz=FIXED_UTC_MINUS_4,
        )
        now = current.index[-1]
        candles = self._aggregate_h4(current, day_start, now)
        if not candles:
            raise ValueError("unable to build H4 candles from intraday_bars")

        active_h4 = candles[-1].label
        active_index = H4_LABELS.index(active_h4)
        driver_start = day_start + pd.Timedelta(hours=4 * DRIVER_INDEX)
        reversal_before_driver = self._reversal_before_driver(
            reversal_confirmed_at, driver_start=driver_start, now=now
        )

        direction = daily_profile.direction
        if daily_profile.status == ProfileStatus.INVALIDATED:
            return self._invalidated_result(
                direction=direction,
                candles=candles,
                active_h4=active_h4,
                location_context=location_context,
                reason="DAILY_PROFILE_INVALIDATED",
            )
        if direction not in {Direction.BULLISH, Direction.BEARISH}:
            return H4ProfileResult(
                profile=H4ProfileType.UNRESOLVED,
                status=ProfileStatus.UNRESOLVED,
                direction=direction,
                phase=H4Phase.UNRESOLVED,
                active_h4=active_h4,
                driver_h4=DRIVER_LABEL,
                expected_driver_action="UNRESOLVED",
                reversal_before_driver=reversal_before_driver,
                location_context=location_context,
                candles=candles,
                expected_next_phase="WAIT_FOR_DAILY_DIRECTION",
                reason_codes=["DAILY_DIRECTION_NOT_RESOLVED"],
            )

        profile, phase, expected_action = self._profile_state(
            active_index=active_index,
            reversal_before_driver=reversal_before_driver,
            reversal_confirmed_at=reversal_confirmed_at,
            driver_start=driver_start,
        )

        status = ProfileStatus.DEVELOPING
        reason_codes = [
            f"ACTIVE_H4_{active_h4.replace(':', '')}",
            f"DAILY_DIRECTION_{direction.value}",
            f"H4_ORDER_FLOW_{h4_order_flow_control.value}",
            f"LOCATION_{location_context.value}",
        ]
        if reversal_before_driver is True:
            reason_codes.append("REVERSAL_CONFIRMED_BEFORE_0600_DRIVER")
        elif reversal_before_driver is False:
            reason_codes.append("NO_REVERSAL_CONFIRMED_BEFORE_0600_DRIVER")
        else:
            reason_codes.append("PRE_DRIVER_REVERSAL_STATUS_PENDING")

        driver = self._find_candle(candles, DRIVER_LABEL)
        if driver is not None and driver.complete:
            if expected_action == "REVERSAL":
                reversed_by_driver = self._driver_reversal_evidence(
                    candles=candles,
                    driver=driver,
                    direction=direction,
                    h4_order_flow_control=h4_order_flow_control,
                )
                if reversed_by_driver:
                    status = ProfileStatus.CONFIRMED
                    reason_codes.append("0600_DRIVER_REVERSAL_CONFIRMED")
                else:
                    return self._invalidated_result(
                        direction=direction,
                        candles=candles,
                        active_h4=active_h4,
                        location_context=location_context,
                        reason="0600_DRIVER_FAILED_TO_REVERSE",
                        reversal_before_driver=reversal_before_driver,
                    )
            elif expected_action == "CONTINUATION":
                continued = self._driver_continuation_evidence(
                    driver=driver,
                    direction=direction,
                    h4_order_flow_control=h4_order_flow_control,
                )
                if continued:
                    status = ProfileStatus.CONFIRMED
                    reason_codes.append("0600_DRIVER_CONTINUATION_CONFIRMED")
                else:
                    reason_codes.append("0600_DRIVER_CONTINUATION_NOT_YET_CONFIRMED")

        if active_index > DRIVER_INDEX and status == ProfileStatus.CONFIRMED:
            phase = H4Phase.POST_DRIVER_EXPANSION
            if profile == H4ProfileType.SIX_AM_CONTINUATION:
                profile = H4ProfileType.NY_CONTINUATION
            elif profile == H4ProfileType.SIX_AM_REVERSAL:
                profile = H4ProfileType.NY_REVERSAL

        return H4ProfileResult(
            profile=profile,
            status=status,
            direction=direction,
            phase=phase,
            active_h4=active_h4,
            driver_h4=DRIVER_LABEL,
            expected_driver_action=expected_action,
            reversal_before_driver=reversal_before_driver,
            location_context=location_context,
            candles=candles,
            expected_next_phase=self._next_phase(
                status=status,
                phase=phase,
                expected_action=expected_action,
            ),
            reason_codes=reason_codes,
        )

    @staticmethod
    def _aggregate_h4(
        current: pd.DataFrame, day_start: pd.Timestamp, now: pd.Timestamp
    ) -> list[H4CandleState]:
        result: list[H4CandleState] = []
        elapsed_hours = (current.index - day_start).total_seconds() / 3600.0
        bucket_ids = (elapsed_hours // 4).astype(int)
        working = current.copy()
        working["_bucket"] = bucket_ids
        working = working[(working["_bucket"] >= 0) & (working["_bucket"] <= 5)]

        for bucket_id, group in working.groupby("_bucket", sort=True):
            bucket_id = int(bucket_id)
            start = day_start + pd.Timedelta(hours=4 * bucket_id)
            end = start + pd.Timedelta(hours=4)
            result.append(
                H4CandleState(
                    label=H4_LABELS[bucket_id],
                    start_time=start.isoformat(),
                    end_time=end.isoformat(),
                    open=float(group.iloc[0]["open"]),
                    high=float(group["high"].max()),
                    low=float(group["low"].min()),
                    close=float(group.iloc[-1]["close"]),
                    complete=bool(now >= end),
                )
            )
        return result

    @staticmethod
    def _canonical_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize(FIXED_UTC_MINUS_4)
        return ts.tz_convert(FIXED_UTC_MINUS_4)

    @classmethod
    def _reversal_before_driver(
        cls,
        reversal_confirmed_at: pd.Timestamp | str | None,
        *,
        driver_start: pd.Timestamp,
        now: pd.Timestamp,
    ) -> bool | None:
        if reversal_confirmed_at is not None:
            reversal_time = cls._canonical_timestamp(reversal_confirmed_at)
            return bool(reversal_time < driver_start)
        if now < driver_start:
            return None
        return False

    @classmethod
    def _profile_state(
        cls,
        *,
        active_index: int,
        reversal_before_driver: bool | None,
        reversal_confirmed_at: pd.Timestamp | str | None,
        driver_start: pd.Timestamp,
    ) -> tuple[H4ProfileType, H4Phase, str]:
        if active_index < DRIVER_INDEX:
            if reversal_confirmed_at is not None:
                reversal_time = cls._canonical_timestamp(reversal_confirmed_at)
                london_start = driver_start - pd.Timedelta(hours=4)
                if london_start <= reversal_time < driver_start:
                    return H4ProfileType.LONDON_REVERSAL, H4Phase.PRE_DRIVER, "CONTINUATION"
            return H4ProfileType.UNRESOLVED, H4Phase.PRE_DRIVER, "PENDING"

        if reversal_before_driver is True:
            return (
                H4ProfileType.SIX_AM_CONTINUATION,
                H4Phase.DRIVER_CONTINUATION,
                "CONTINUATION",
            )
        return H4ProfileType.SIX_AM_REVERSAL, H4Phase.DRIVER_REVERSAL, "REVERSAL"

    @staticmethod
    def _find_candle(candles: list[H4CandleState], label: str) -> H4CandleState | None:
        return next((item for item in candles if item.label == label), None)

    @staticmethod
    def _body_matches_direction(candle: H4CandleState, direction: Direction) -> bool:
        if direction == Direction.BULLISH:
            return candle.close > candle.open
        if direction == Direction.BEARISH:
            return candle.close < candle.open
        return False

    @classmethod
    def _driver_continuation_evidence(
        cls,
        *,
        driver: H4CandleState,
        direction: Direction,
        h4_order_flow_control: Direction,
    ) -> bool:
        return bool(
            cls._body_matches_direction(driver, direction)
            and h4_order_flow_control == direction
        )

    @classmethod
    def _driver_reversal_evidence(
        cls,
        *,
        candles: list[H4CandleState],
        driver: H4CandleState,
        direction: Direction,
        h4_order_flow_control: Direction,
    ) -> bool:
        pre_driver = [item for item in candles if H4_LABELS.index(item.label) < DRIVER_INDEX]
        if not pre_driver or h4_order_flow_control != direction:
            return False
        if not cls._body_matches_direction(driver, direction):
            return False
        if direction == Direction.BULLISH:
            prior_low = min(item.low for item in pre_driver)
            return driver.low <= prior_low
        prior_high = max(item.high for item in pre_driver)
        return driver.high >= prior_high

    @staticmethod
    def _next_phase(
        *, status: ProfileStatus, phase: H4Phase, expected_action: str
    ) -> str | None:
        if status == ProfileStatus.INVALIDATED:
            return "RECLASSIFY_H4_PROFILE"
        if phase == H4Phase.PRE_DRIVER:
            return "RESOLVE_REVERSAL_BEFORE_0600_DRIVER"
        if phase in {H4Phase.DRIVER_CONTINUATION, H4Phase.DRIVER_REVERSAL}:
            return f"WAIT_FOR_0600_{expected_action}_CONFIRMATION"
        if phase == H4Phase.POST_DRIVER_EXPANSION:
            return "CONTINUE_WITH_DAILY_PROFILE_UNTIL_DRAW_OR_INVALIDATION"
        return None

    @staticmethod
    def _invalidated_result(
        *,
        direction: Direction,
        candles: list[H4CandleState],
        active_h4: str,
        location_context: H4LocationContext,
        reason: str,
        reversal_before_driver: bool | None = None,
    ) -> H4ProfileResult:
        return H4ProfileResult(
            profile=H4ProfileType.UNRESOLVED,
            status=ProfileStatus.INVALIDATED,
            direction=direction,
            phase=H4Phase.INVALIDATED,
            active_h4=active_h4,
            driver_h4=DRIVER_LABEL,
            expected_driver_action="RECLASSIFY",
            reversal_before_driver=reversal_before_driver,
            location_context=location_context,
            candles=candles,
            expected_next_phase="RECLASSIFY_H4_PROFILE",
            reason_codes=[reason],
        )
