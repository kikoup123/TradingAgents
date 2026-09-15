import pandas as pd

from tradingagents.ict.liquidity import (
    LiquidityClass,
    LiquidityEngine,
    LiquiditySide,
    LiquidityStatus,
)
from tradingagents.ict.models import Direction


def _bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_structural_erl_irl_protected_low_and_bullish_draw():
    bars = _bars(
        [
            (8, 10, 6, 9),
            (9, 12, 8, 11),
            (8, 9, 5, 7),
            (10, 13, 8, 12),
            (7, 10, 4, 6),
            (11, 15, 9, 14),
            (10, 11, 7, 11),
        ]
    )

    result = LiquidityEngine(pivot_span=1).analyze(
        bars,
        timeframe="4H",
        order_flow_control=Direction.BULLISH,
    )

    external_bsl = [
        pool
        for pool in result.buy_side
        if pool.liquidity_class == LiquidityClass.EXTERNAL
    ]
    external_ssl = [
        pool
        for pool in result.sell_side
        if pool.liquidity_class == LiquidityClass.EXTERNAL
    ]

    assert [pool.price for pool in external_bsl] == [15.0]
    assert [pool.price for pool in external_ssl] == [4.0]
    assert result.protected_pool is not None
    assert result.protected_pool.side == LiquiditySide.SELL_SIDE
    assert result.protected_pool.price == 4.0
    assert result.protected_pool.protected is True
    assert result.active_draw is not None
    assert result.active_draw.side == LiquiditySide.BUY_SIDE
    assert result.active_draw.price == 15.0
    assert result.dealing_range_equilibrium == 9.5
    assert result.dealing_range_location == "PREMIUM"


def test_wick_through_buy_side_then_close_back_below_is_raid_reclaim():
    bars = _bars(
        [
            (9, 10, 8, 9.5),
            (10, 12, 9, 11),
            (9, 10, 8, 9),
            (11, 12.5, 9, 11.5),
        ]
    )

    result = LiquidityEngine(pivot_span=1).analyze(
        bars,
        timeframe="1H",
        order_flow_control=Direction.UNCONFIRMED,
    )

    pool = next(pool for pool in result.buy_side if pool.price == 12.0)
    assert pool.status == LiquidityStatus.RAIDED_RECLAIMED
    assert pool.event_position == 3
    assert pool.event_close == 11.5


def test_body_close_through_buy_side_consumes_liquidity():
    bars = _bars(
        [
            (9, 10, 8, 9.5),
            (10, 12, 9, 11),
            (9, 10, 8, 9),
            (11, 12.5, 10, 12.3),
        ]
    )

    result = LiquidityEngine(pivot_span=1).analyze(
        bars,
        timeframe="1H",
        order_flow_control=Direction.UNCONFIRMED,
    )

    pool = next(pool for pool in result.buy_side if pool.price == 12.0)
    assert pool.status == LiquidityStatus.CONSUMED
    assert pool.event_position == 3


def test_active_draw_prioritizes_external_over_nearer_internal_liquidity():
    bars = _bars(
        [
            (8, 10, 6, 9),
            (11, 15, 8, 12),
            (8, 9, 5, 7),
            (10, 13, 7, 11),
            (9, 10, 4, 9),
        ]
    )

    result = LiquidityEngine(pivot_span=1).analyze(
        bars,
        timeframe="4H",
        order_flow_control=Direction.BULLISH,
    )

    assert any(
        pool.price == 13.0 and pool.liquidity_class == LiquidityClass.INTERNAL
        for pool in result.buy_side
    )
    assert result.active_draw is not None
    assert result.active_draw.price == 15.0
    assert result.active_draw.liquidity_class == LiquidityClass.EXTERNAL


def test_complete_ons_high_and_low_are_added_as_named_external_liquidity():
    bars = _bars(
        [
            (8, 10, 6, 9),
            (9, 12, 8, 11),
            (8, 9, 5, 7),
            (10, 11, 7, 10),
        ]
    )
    time_price = {
        "ons": {
            "ny_ons": {
                "status": "COMPLETE",
                "end_time": "2026-09-15T08:00:00-04:00",
                "high": 20.0,
                "low": 3.0,
            }
        }
    }

    result = LiquidityEngine(pivot_span=1).analyze(
        bars,
        timeframe="4H",
        order_flow_control=Direction.UNCONFIRMED,
        time_price_state=time_price,
    )

    ny_high = next(pool for pool in result.buy_side if pool.source_kind == "NY_ONS_HIGH")
    ny_low = next(pool for pool in result.sell_side if pool.source_kind == "NY_ONS_LOW")
    assert ny_high.liquidity_class == LiquidityClass.EXTERNAL
    assert ny_low.liquidity_class == LiquidityClass.EXTERNAL
    assert ny_high.status == LiquidityStatus.ACTIVE
    assert ny_low.status == LiquidityStatus.ACTIVE
