import pandas as pd

from tradingagents.ict import ONSConfig, TimePriceEngine


def bars(rows, times):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.to_datetime(times),
    )


def test_opening_prices_map_to_the_active_fixed_utc_minus_4_trading_day():
    data = bars(
        [
            (98, 99, 97, 98.5),
            (100, 101, 99, 100.5),
            (101, 102, 100, 101.5),
            (102, 103, 101, 102.5),
            (103, 104, 102, 103.5),
            (104, 105, 103, 104.5),
            (105, 106, 104, 105.5),
            (106, 107, 105, 106.5),
            (107, 109, 106, 108),
        ],
        [
            "2026-09-14 18:00",
            "2026-09-14 19:30",
            "2026-09-15 00:00",
            "2026-09-15 01:30",
            "2026-09-15 02:00",
            "2026-09-15 07:30",
            "2026-09-15 08:30",
            "2026-09-15 09:30",
            "2026-09-15 10:00",
        ],
    )

    result = TimePriceEngine().analyze(
        data,
        as_of="2026-09-15 10:15",
        current_price=108,
    )

    assert result.trading_day == "2026-09-15"
    assert result.opens["settlement_open"].price == 98
    assert result.opens["asian_open"].price == 100
    assert result.opens["midnight_open"].price == 101
    assert result.opens["london_open"].price == 102
    assert result.opens["open_0200"].price == 103
    assert result.opens["ny_premarket_open"].price == 104
    assert result.opens["open_0830"].price == 105
    assert result.opens["equities_open"].price == 106
    assert result.opens["open_1000"].price == 107
    assert result.opens["open_0830"].relation == "ABOVE"


def test_ny_ons_preserves_chicago_session_and_builds_half_deviations():
    data = bars(
        [
            (100, 104, 98, 102),
            (102, 108, 96, 106),
            (106, 110, 94, 108),
            (108, 109, 90, 92),
            (92, 95, 91, 94),
        ],
        [
            "2026-09-15 05:00",
            "2026-09-15 06:00",
            "2026-09-15 07:00",
            "2026-09-15 08:00",
            "2026-09-15 09:01",
        ],
    )

    result = TimePriceEngine().analyze(data, as_of="2026-09-15 09:01")
    ny = result.ons["ny_ons"]

    assert ny.high == 110
    assert ny.low == 90
    assert ny.equilibrium == 100
    assert ny.range_size == 20
    assert ny.upper_projections == [
        {"deviation": 0.5, "price": 120.0},
        {"deviation": 1.0, "price": 130.0},
    ]
    assert ny.lower_projections == [
        {"deviation": -0.5, "price": 80.0},
        {"deviation": -1.0, "price": 70.0},
    ]
    assert ny.status == "COMPLETE"


def test_ons_can_use_candle_bodies_instead_of_wicks():
    config = ONSConfig(
        "custom",
        "Custom ONS",
        "America/New_York",
        5,
        0,
        7,
        0,
        range_type="Bodies",
        show_half_deviations=False,
        projection_count=1,
    )
    data = bars(
        [
            (100, 110, 90, 104),
            (104, 115, 92, 98),
            (98, 100, 97, 99),
        ],
        ["2026-09-15 05:00", "2026-09-15 06:00", "2026-09-15 07:01"],
    )

    result = TimePriceEngine(ons_configs=(config,)).analyze(
        data, as_of="2026-09-15 07:01"
    )
    ons = result.ons["custom"]

    assert ons.high == 104
    assert ons.low == 98
    assert ons.range_size == 6
    assert ons.upper_projections == [{"deviation": 1.0, "price": 110.0}]
    assert ons.lower_projections == [{"deviation": -1.0, "price": 92.0}]


def test_missing_exact_open_is_reported_unavailable_instead_of_guessed():
    data = bars(
        [(100, 101, 99, 100.5), (101, 102, 100, 101.5)],
        ["2026-09-15 08:29", "2026-09-15 08:31"],
    )

    result = TimePriceEngine().analyze(data)

    assert result.opens["open_0830"].available is False
    assert result.opens["open_0830"].price is None
    assert result.opens["open_0830"].relation == "UNAVAILABLE"


def test_open_level_tracks_revisit_and_body_close_cross():
    data = bars(
        [
            (100, 103, 99, 102),
            (102, 103, 97, 98),
            (98, 100, 97, 99),
        ],
        ["2026-09-15 08:30", "2026-09-15 08:31", "2026-09-15 08:32"],
    )

    result = TimePriceEngine().analyze(data, current_price=99)
    level = result.opens["open_0830"]

    assert level.price == 100
    assert level.relation == "BELOW"
    assert level.touched is True
    assert level.crossed is True
    assert level.last_cross_time.endswith("08:31:00-04:00")
