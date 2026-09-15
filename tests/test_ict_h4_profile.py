import pandas as pd

from tradingagents.ict import (
    DailyDelivery,
    DailyPhase,
    DailyProfileResult,
    DayType,
    Direction,
    H4LocationContext,
    H4Phase,
    H4ProfileEngine,
    H4ProfileType,
    ProfileStatus,
)


def daily(direction=Direction.BULLISH):
    delivery = DailyDelivery.OLHC if direction == Direction.BULLISH else DailyDelivery.OHLC
    return DailyProfileResult(
        trading_day="2026-09-15",
        direction=direction,
        day_type=DayType.CONTINUATION_CANDIDATE,
        expected_delivery=delivery,
        observed_delivery=delivery,
        status=ProfileStatus.CONFIRMED,
        phase=DailyPhase.EXPANSION,
        daily_open=100,
        daily_high=110,
        daily_low=95,
        current_close=108,
        high_time="2026-09-16T09:00:00-04:00",
        low_time="2026-09-16T03:00:00-04:00",
        protected_extreme=True,
        expected_next_phase=None,
        reason_codes=[],
    )


def bars(rows, times):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.to_datetime(times),
    )


def continuation_bars():
    return bars(
        [
            (100, 101, 98, 99),
            (99, 100, 96, 97),
            (97, 100, 95, 99),
            (99, 106, 98, 105),
            (105, 106, 104, 105),
        ],
        [
            "2026-09-15 18:00",
            "2026-09-15 22:00",
            "2026-09-16 02:00",
            "2026-09-16 06:00",
            "2026-09-16 10:00",
        ],
    )


def reversal_bars(driver_low=94):
    return bars(
        [
            (100, 101, 98, 99),
            (99, 100, 96, 97),
            (97, 99, 95, 96),
            (96, 104, driver_low, 103),
            (103, 105, 102, 104),
        ],
        [
            "2026-09-15 18:00",
            "2026-09-15 22:00",
            "2026-09-16 02:00",
            "2026-09-16 06:00",
            "2026-09-16 10:00",
        ],
    )


def test_h4_buckets_use_18_22_02_06_10_sequence_across_midnight():
    result = H4ProfileEngine().analyze(
        continuation_bars(),
        daily_profile=daily(),
        h4_order_flow_control=Direction.BULLISH,
        reversal_confirmed_at="2026-09-16 03:00-04:00",
    )

    assert [candle.label for candle in result.candles] == [
        "18:00",
        "22:00",
        "02:00",
        "06:00",
        "10:00",
    ]


def test_reversal_before_0600_driver_creates_ny_continuation_profile():
    result = H4ProfileEngine().analyze(
        continuation_bars(),
        daily_profile=daily(),
        h4_order_flow_control=Direction.BULLISH,
        reversal_confirmed_at="2026-09-16 03:00-04:00",
        location_context=H4LocationContext.IRL,
    )

    assert result.reversal_before_driver is True
    assert result.expected_driver_action == "CONTINUATION"
    assert result.status == ProfileStatus.CONFIRMED
    assert result.profile == H4ProfileType.NY_CONTINUATION
    assert result.phase == H4Phase.POST_DRIVER_EXPANSION
    assert result.location_context == H4LocationContext.IRL


def test_no_reversal_before_driver_requires_0600_reversal():
    result = H4ProfileEngine().analyze(
        reversal_bars(),
        daily_profile=daily(),
        h4_order_flow_control=Direction.BULLISH,
        reversal_confirmed_at=None,
    )

    assert result.reversal_before_driver is False
    assert result.expected_driver_action == "REVERSAL"
    assert result.status == ProfileStatus.CONFIRMED
    assert result.profile == H4ProfileType.NY_REVERSAL
    assert result.phase == H4Phase.POST_DRIVER_EXPANSION


def test_0600_driver_failure_invalidates_reversal_profile():
    result = H4ProfileEngine().analyze(
        reversal_bars(driver_low=95.5),
        daily_profile=daily(),
        h4_order_flow_control=Direction.BULLISH,
        reversal_confirmed_at=None,
    )

    assert result.status == ProfileStatus.INVALIDATED
    assert result.phase == H4Phase.INVALIDATED
    assert "0600_DRIVER_FAILED_TO_REVERSE" in result.reason_codes


def test_pre_driver_london_reversal_is_identified_before_0600():
    data = bars(
        [
            (100, 101, 98, 99),
            (99, 100, 96, 97),
            (97, 101, 95, 100),
        ],
        [
            "2026-09-15 18:00",
            "2026-09-15 22:00",
            "2026-09-16 02:00",
        ],
    )

    result = H4ProfileEngine().analyze(
        data,
        daily_profile=daily(),
        reversal_confirmed_at="2026-09-16 03:00-04:00",
    )

    assert result.profile == H4ProfileType.LONDON_REVERSAL
    assert result.phase == H4Phase.PRE_DRIVER
    assert result.expected_driver_action == "CONTINUATION"
