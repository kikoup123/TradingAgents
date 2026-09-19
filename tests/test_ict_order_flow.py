import pandas as pd

from tradingagents.ict import Direction, OrderFlowEngine, RangeStatus


def bars(rows):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-09-14 09:00", periods=len(rows), freq="5min"),
    )


def test_bullish_down_close_range_requires_body_close_above():
    data = bars(
        [
            (100, 101, 95, 96),
            (96, 102, 96, 100),
            (100, 103, 99, 102),
        ]
    )

    result = OrderFlowEngine().analyze(data, timeframe="5m")

    assert result.control == Direction.BULLISH
    assert result.latest_event is not None
    assert result.latest_event.low == 95
    assert result.latest_event.high == 101
    assert result.latest_event.confirmed_position == 2
    assert result.latest_event.status == RangeStatus.CONFIRMED


def test_bearish_up_close_range_requires_body_close_below():
    data = bars(
        [
            (100, 105, 99, 104),
            (104, 104, 98, 100),
            (100, 101, 96, 98),
        ]
    )

    result = OrderFlowEngine().analyze(data, timeframe="5m")

    assert result.control == Direction.BEARISH
    assert result.latest_event is not None
    assert result.latest_event.confirmed_position == 2


def test_latest_confirmed_range_invalidation_moves_control_to_transition():
    data = bars(
        [
            (100, 101, 95, 96),
            (103, 104, 96, 102),
            (102, 103, 94, 94.5),
        ]
    )

    result = OrderFlowEngine().analyze(data, timeframe="5m")

    assert result.control == Direction.TRANSITION
    assert result.latest_event is not None
    assert result.latest_event.status == RangeStatus.INVALIDATED


def test_post_csd_bullish_iofc_ignores_pre_anchor_down_close_range():
    data = bars(
        [
            (100, 102, 96, 97),
            (97, 99, 96, 98),
            (98, 99, 94, 95),
            (95, 100, 95, 99.5),
        ]
    )

    result = OrderFlowEngine().find_iofc_after(
        data,
        anchor_position=1,
        expected_direction=Direction.BULLISH,
    )

    assert result.confirmed is True
    assert result.confirmation_range is not None
    assert result.confirmation_range.source_position == 2
    assert result.confirmation_range.confirmed_position == 3


def test_post_csd_waits_when_only_wick_trades_through_range():
    data = bars(
        [
            (100, 101, 99, 100.5),
            (100.5, 101, 95, 96),
            (96, 102, 96, 100.5),
        ]
    )

    result = OrderFlowEngine().find_iofc_after(
        data,
        anchor_position=0,
        expected_direction=Direction.BULLISH,
    )

    assert result.confirmed is False
    assert "BODY_ACCEPTANCE_ABOVE" in result.reason
