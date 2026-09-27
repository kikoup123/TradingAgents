import pandas as pd

from tradingagents.ict import (
    DayType,
    Direction,
    ProfileStatus,
    WeeklyProfileEngine,
    WeeklyProfileType,
)


def week(rows, start="2026-09-14"):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.bdate_range(start, periods=len(rows)),
    )


def test_classic_expansion_bullish_early_week_low_makes_thursday_continuation_candidate():
    data = week(
        [
            (100, 104, 95, 102),
            (102, 108, 100, 107),
            (107, 112, 105, 111),
            (111, 116, 109, 115),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BULLISH,
        protected_weekly_extreme=True,
    )

    assert result.profile == WeeklyProfileType.CLASSIC_EXPANSION
    assert result.status == ProfileStatus.CONFIRMED
    assert result.current_day_type == DayType.CONTINUATION_CANDIDATE
    assert result.expected_daily_delivery == "OLHC"


def test_classic_expansion_friday_is_retracement_candidate():
    data = week(
        [
            (100, 104, 95, 102),
            (102, 108, 100, 107),
            (107, 112, 105, 111),
            (111, 116, 109, 115),
            (115, 117, 112, 113),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BULLISH,
        protected_weekly_extreme=True,
    )

    assert result.profile == WeeklyProfileType.CLASSIC_EXPANSION
    assert result.current_day == "FRIDAY"
    assert result.current_day_type == DayType.RETRACEMENT_CANDIDATE


def test_midweek_reversal_wednesday_low_makes_thursday_continuation_candidate():
    data = week(
        [
            (100, 104, 98, 102),
            (102, 105, 99, 101),
            (101, 103, 94, 102),
            (102, 110, 101, 109),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BULLISH,
        protected_weekly_extreme=True,
    )

    assert result.profile == WeeklyProfileType.MIDWEEK_REVERSAL
    assert result.current_day_type == DayType.CONTINUATION_CANDIDATE
    assert result.expected_daily_delivery == "OLHC"


def test_thursday_reversal_friday_continues_when_opposing_draw_unreached():
    data = week(
        [
            (100, 103, 99, 102),
            (102, 104, 98, 100),
            (100, 102, 97, 99),
            (99, 101, 92, 100),
            (100, 108, 99, 107),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BULLISH,
        protected_weekly_extreme=True,
        opposing_draw_reached=False,
    )

    assert result.profile == WeeklyProfileType.THURSDAY_REVERSAL
    assert result.current_day_type == DayType.CONTINUATION_CANDIDATE


def test_thursday_reversal_friday_returns_to_range_when_draw_reached():
    data = week(
        [
            (100, 103, 99, 102),
            (102, 104, 98, 100),
            (100, 102, 97, 99),
            (99, 101, 92, 100),
            (100, 108, 99, 107),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BULLISH,
        protected_weekly_extreme=True,
        opposing_draw_reached=True,
    )

    assert result.current_day_type == DayType.RETURN_TO_RANGE_CANDIDATE


def test_profile_stays_developing_until_weekly_extreme_is_protected():
    data = week(
        [
            (100, 104, 95, 102),
            (102, 108, 100, 107),
            (107, 112, 105, 111),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BULLISH,
        protected_weekly_extreme=None,
    )

    assert result.profile == WeeklyProfileType.CLASSIC_EXPANSION
    assert result.status == ProfileStatus.DEVELOPING
    assert result.current_day_type == DayType.UNRESOLVED


def test_bearish_profile_maps_continuation_to_ohlc():
    data = week(
        [
            (100, 104, 98, 99),
            (99, 108, 98, 100),
            (100, 101, 94, 95),
            (95, 96, 90, 91),
        ]
    )

    result = WeeklyProfileEngine().analyze(
        data,
        htf_control=Direction.BEARISH,
        protected_weekly_extreme=True,
    )

    assert result.profile == WeeklyProfileType.CLASSIC_EXPANSION
    assert result.current_day_type == DayType.CONTINUATION_CANDIDATE
    assert result.expected_daily_delivery == "OHLC"
