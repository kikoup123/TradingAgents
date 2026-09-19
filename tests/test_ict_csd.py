from __future__ import annotations

import pandas as pd

from tradingagents.ict.csd import CSDEngine
from tradingagents.ict.models import Direction
from tradingagents.ict.order_flow import OrderFlowEngine


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-09-14 18:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


def _bullish_csd_bars(*, confirm: bool = True, with_iofc: bool = False) -> pd.DataFrame:
    rows = [
        (10.0, 11.0, 9.5, 10.4),
        (11.8, 12.0, 10.5, 11.2),
        (10.2, 10.5, 8.0, 9.0),
        (10.8, 11.0, 9.0, 10.0),
        (10.0, 10.5, 7.5, 9.2),
        (9.4, 10.8, 7.3, 10.2),
        (10.4, 11.95, 9.8, 11.9 if confirm else 11.6),
    ]
    if with_iofc:
        rows.extend(
            [
                (11.7, 11.8, 11.0, 11.2),
                (11.3, 12.2, 11.2, 12.0),
            ]
        )
    return _frame(rows)


def _bearish_csd_bars() -> pd.DataFrame:
    return _frame(
        [
            (9.0, 10.5, 9.0, 9.8),
            (8.5, 10.0, 8.0, 9.2),
            (10.5, 12.0, 9.0, 11.2),
            (9.5, 11.0, 9.0, 10.5),
            (10.5, 12.5, 9.5, 11.0),
            (10.8, 12.7, 9.0, 9.6),
            (9.2, 9.5, 8.2, 8.3),
        ]
    )


def test_bullish_csd_uses_highest_down_close_open_after_sell_side_raid() -> None:
    result = CSDEngine(pivot_span=1).analyze(
        _bullish_csd_bars(),
        timeframe="5m",
    )

    assert result.confirmed is True
    assert result.direction == Direction.BULLISH
    assert result.latest_event is not None
    event = result.latest_event
    assert event.liquidity_side == "SELL_SIDE"
    assert event.threshold_open == 11.8
    assert event.confirmation_position == 6
    assert event.confirmation_close == 11.9
    assert event.protected_extreme == 7.3
    assert "BODY_CLOSE_CSD_CONFIRMATION" in event.reason_codes
    assert "PROTECTED_LOW_CONFIRMED" in event.reason_codes


def test_bearish_csd_uses_lowest_up_close_open_after_buy_side_raid() -> None:
    result = CSDEngine(pivot_span=1).analyze(
        _bearish_csd_bars(),
        timeframe="5m",
    )

    assert result.confirmed is True
    assert result.direction == Direction.BEARISH
    assert result.latest_event is not None
    event = result.latest_event
    assert event.liquidity_side == "BUY_SIDE"
    assert event.threshold_open == 8.5
    assert event.confirmation_position == 6
    assert event.confirmation_close == 8.3
    assert event.protected_extreme == 12.7
    assert "PROTECTED_HIGH_CONFIRMED" in event.reason_codes


def test_wick_through_csd_threshold_without_body_close_does_not_confirm() -> None:
    bars = _bullish_csd_bars(confirm=False)
    bars.iloc[-1, bars.columns.get_loc("high")] = 11.95

    result = CSDEngine(pivot_span=1).analyze(bars, timeframe="5m")

    assert result.confirmed is False
    assert result.direction == Direction.UNCONFIRMED
    assert result.latest_event is None


def test_post_csd_iofc_requires_new_opposing_close_range_after_csd() -> None:
    bars = _bullish_csd_bars(with_iofc=True)
    csd = CSDEngine(pivot_span=1).analyze(bars, timeframe="5m")
    assert csd.latest_event is not None

    iofc = OrderFlowEngine().find_iofc_after(
        bars,
        anchor_position=csd.latest_event.confirmation_position,
        expected_direction=csd.latest_event.direction,
    )

    assert iofc.confirmed is True
    assert iofc.expected_direction == Direction.BULLISH
    assert iofc.confirmation_range is not None
    assert iofc.confirmation_range.source_position == 7
    assert iofc.confirmation_range.confirmed_position == 8


def test_csd_before_reference_time_cannot_validate_later_setup() -> None:
    result = CSDEngine(pivot_span=1).analyze(
        _bullish_csd_bars(),
        timeframe="5m",
    )
    assert result.latest_event is not None

    after = pd.Timestamp(result.latest_event.confirmation_time) + pd.Timedelta(minutes=5)
    selected = CSDEngine.latest_event_at_or_after(result, reference_time=after)

    assert selected is None
