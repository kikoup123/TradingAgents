import pandas as pd

from tradingagents.ict import (
    DailyDelivery,
    DailyPhase,
    DailyProfileEngine,
    DayType,
    Direction,
    ProfileStatus,
    WeeklyProfileResult,
    WeeklyProfileType,
)


def weekly(day_type, direction=Direction.BULLISH, current_day="THURSDAY"):
    return WeeklyProfileResult(
        profile=WeeklyProfileType.CLASSIC_EXPANSION,
        status=ProfileStatus.CONFIRMED,
        direction=direction,
        current_day=current_day,
        current_day_type=day_type,
        week_phase="TEST",
        weekly_extreme=None,
        expected_daily_delivery="OLHC" if direction == Direction.BULLISH else "OHLC",
        expected_next_phase=None,
        reason_codes=[],
    )


def bars(rows, times):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.to_datetime(times),
    )


def test_bullish_continuation_confirms_olhc_after_low_then_high():
    data = bars(
        [
            (100, 101, 99, 99.5),
            (99.5, 100, 97, 98),
            (98, 102, 98, 101),
            (101, 106, 100, 105),
        ],
        [
            "2026-09-15 18:00",
            "2026-09-15 22:00",
            "2026-09-16 02:00",
            "2026-09-16 06:00",
        ],
    )

    result = DailyProfileEngine().analyze(
        data,
        weekly_profile=weekly(DayType.CONTINUATION_CANDIDATE),
        daily_order_flow_control=Direction.BULLISH,
        protected_daily_extreme=True,
        opposing_draw_reached=False,
    )

    assert result.trading_day == "2026-09-16"
    assert result.expected_delivery == DailyDelivery.OLHC
    assert result.observed_delivery == DailyDelivery.OLHC
    assert result.status == ProfileStatus.CONFIRMED
    assert result.phase == DailyPhase.EXPANSION
    assert result.direction == Direction.BULLISH


def test_bearish_continuation_confirms_ohlc_after_high_then_low():
    data = bars(
        [
            (100, 102, 99, 101),
            (101, 104, 100, 103),
            (103, 103, 97, 98),
            (98, 99, 94, 95),
        ],
        [
            "2026-09-15 18:00",
            "2026-09-15 22:00",
            "2026-09-16 02:00",
            "2026-09-16 06:00",
        ],
    )

    result = DailyProfileEngine().analyze(
        data,
        weekly_profile=weekly(DayType.CONTINUATION_CANDIDATE, Direction.BEARISH),
        daily_order_flow_control=Direction.BEARISH,
        protected_daily_extreme=True,
    )

    assert result.expected_delivery == DailyDelivery.OHLC
    assert result.observed_delivery == DailyDelivery.OHLC
    assert result.status == ProfileStatus.CONFIRMED
    assert result.direction == Direction.BEARISH


def test_bullish_week_friday_retracement_inverts_expected_daily_delivery():
    data = bars(
        [
            (110, 112, 109, 111),
            (111, 114, 110, 113),
            (113, 113, 106, 107),
        ],
        [
            "2026-09-17 18:00",
            "2026-09-17 22:00",
            "2026-09-18 02:00",
        ],
    )

    result = DailyProfileEngine().analyze(
        data,
        weekly_profile=weekly(
            DayType.RETRACEMENT_CANDIDATE,
            Direction.BULLISH,
            current_day="FRIDAY",
        ),
        daily_order_flow_control=Direction.BEARISH,
        protected_daily_extreme=True,
    )

    assert result.trading_day == "2026-09-18"
    assert result.direction == Direction.BEARISH
    assert result.expected_delivery == DailyDelivery.OHLC
    assert result.phase == DailyPhase.RETRACEMENT


def test_daily_rollover_starts_at_1800_and_labels_the_next_trading_date():
    data = bars(
        [
            (90, 92, 89, 91),
            (100, 102, 98, 99),
            (99, 101, 97, 100),
            (100, 104, 99, 103),
        ],
        [
            "2026-09-15 17:00",
            "2026-09-15 18:00",
            "2026-09-15 22:00",
            "2026-09-16 02:00",
        ],
    )

    result = DailyProfileEngine().analyze(
        data,
        weekly_profile=weekly(DayType.CONTINUATION_CANDIDATE),
    )

    assert result.trading_day == "2026-09-16"
    assert result.daily_open == 100
    assert result.daily_low == 97


def test_failed_protected_daily_extreme_invalidates_profile():
    data = bars(
        [
            (100, 101, 98, 99),
            (99, 104, 97, 103),
        ],
        ["2026-09-15 18:00", "2026-09-15 22:00"],
    )

    result = DailyProfileEngine().analyze(
        data,
        weekly_profile=weekly(DayType.CONTINUATION_CANDIDATE),
        daily_order_flow_control=Direction.BULLISH,
        protected_daily_extreme=False,
    )

    assert result.status == ProfileStatus.INVALIDATED
    assert result.phase == DailyPhase.INVALIDATED
