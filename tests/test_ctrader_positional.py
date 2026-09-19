from __future__ import annotations

from tradingagents.dataflows.ctrader_positional import (
    FRACTAL_TIMEFRAME_PAIRS,
    _c2_direction,
    _resolve_fractal_stage,
    _tspot_zone,
    _wick_eq_price,
    evaluate_positional_from_bars,
)


def bar(
    time: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> dict:
    return {
        "time": time,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def bearish_htf() -> list[dict]:
    return [
        bar("2026-09-19T09:00:00+00:00", 104, 108, 100, 101),
        # C1
        bar("2026-09-19T10:00:00+00:00", 100, 104, 99, 103),
        # C2: sweep C1 high 104, close back below it.
        bar("2026-09-19T11:00:00+00:00", 103, 110, 92, 96),
        # C3
        bar("2026-09-19T12:00:00+00:00", 97, 98, 78, 80),
    ]


def bearish_ltf(*, hit_target: bool = True) -> list[dict]:
    rows = [
        # Pre-C2 structure. 10:55 low 100 is the closest prior swing low
        # used as structural projection zero. C2 extreme 110 => STD -2 = 80.
        bar("2026-09-19T10:35:00+00:00", 99, 101, 98, 100),
        bar("2026-09-19T10:40:00+00:00", 100, 102, 99, 101),
        bar("2026-09-19T10:45:00+00:00", 101, 105, 100, 104),
        bar("2026-09-19T10:50:00+00:00", 104, 104, 101, 102),
        bar("2026-09-19T10:55:00+00:00", 102, 103, 100, 101),
        # C2 extreme / BSL raid. Body closes back below prior 105 swing.
        bar("2026-09-19T11:00:00+00:00", 102, 110, 101, 104),
        bar("2026-09-19T11:05:00+00:00", 104, 106, 99, 100),
        # New post-CSD continuation protected high at 104.
        bar("2026-09-19T11:10:00+00:00", 100, 104, 99, 103),
        bar("2026-09-19T11:15:00+00:00", 103, 103.5, 96, 97),
        bar("2026-09-19T11:20:00+00:00", 97, 100, 95, 96),
        bar("2026-09-19T11:25:00+00:00", 96, 99, 94, 95),
        bar("2026-09-19T11:30:00+00:00", 95, 98, 93, 94),
        bar("2026-09-19T11:35:00+00:00", 94, 97, 92, 93),
        bar("2026-09-19T11:40:00+00:00", 93, 96, 90, 91),
        bar("2026-09-19T11:45:00+00:00", 91, 94, 88, 89),
        bar("2026-09-19T11:50:00+00:00", 89, 92, 86, 87),
        bar("2026-09-19T11:55:00+00:00", 87, 90, 84, 85),
        # C3 opens and can expand immediately without a new C3 signal.
        bar("2026-09-19T12:00:00+00:00", 97, 98, 90, 91),
    ]
    if hit_target:
        rows.append(bar("2026-09-19T12:05:00+00:00", 91, 92, 79, 80))
    return rows


def bullish_htf() -> list[dict]:
    return [
        bar("2026-09-19T09:00:00+00:00", 96, 100, 92, 95),
        # C1
        bar("2026-09-19T10:00:00+00:00", 98, 102, 96, 97),
        # C2: sweep C1 low 96, close back above it.
        bar("2026-09-19T11:00:00+00:00", 97, 108, 90, 104),
        # C3
        bar("2026-09-19T12:00:00+00:00", 103, 121, 102, 120),
    ]


def bullish_ltf() -> list[dict]:
    return [
        # 10:55 high 100 is the closest prior swing high.
        # C2 extreme 90 => structural STD -2 = 120.
        bar("2026-09-19T10:35:00+00:00", 101, 102, 99, 100),
        bar("2026-09-19T10:40:00+00:00", 100, 101, 97, 98),
        bar("2026-09-19T10:45:00+00:00", 98, 99, 95, 96),
        bar("2026-09-19T10:50:00+00:00", 96, 99, 96, 98),
        bar("2026-09-19T10:55:00+00:00", 98, 100, 97, 99),
        # C2 extreme / SSL raid.
        bar("2026-09-19T11:00:00+00:00", 98, 99, 90, 96),
        bar("2026-09-19T11:05:00+00:00", 96, 101, 95, 100),
        # New post-CSD continuation protected low at 96.
        bar("2026-09-19T11:10:00+00:00", 99, 100, 96, 97),
        bar("2026-09-19T11:15:00+00:00", 97, 102, 97, 101),
        bar("2026-09-19T11:20:00+00:00", 101, 103, 98, 102),
        bar("2026-09-19T11:25:00+00:00", 102, 104, 99, 103),
        bar("2026-09-19T11:30:00+00:00", 103, 105, 100, 104),
        bar("2026-09-19T11:35:00+00:00", 104, 106, 101, 105),
        bar("2026-09-19T11:40:00+00:00", 105, 107, 102, 106),
        bar("2026-09-19T11:45:00+00:00", 106, 108, 103, 107),
        bar("2026-09-19T11:50:00+00:00", 107, 109, 104, 108),
        bar("2026-09-19T11:55:00+00:00", 108, 110, 105, 109),
        bar("2026-09-19T12:00:00+00:00", 103, 108, 102, 107),
        bar("2026-09-19T12:05:00+00:00", 107, 121, 106, 120),
    ]


def c4_htf() -> list[dict]:
    return [
        # C1
        bar("2026-09-19T09:00:00+00:00", 100, 105, 95, 102),
        # C2 bearish sweep/reclaim.
        bar("2026-09-19T10:00:00+00:00", 103, 110, 92, 100),
        # C3 survives: no high above 110 and no bullish opposite model.
        bar("2026-09-19T11:00:00+00:00", 100, 108, 85, 90),
        # C4 open.
        bar("2026-09-19T12:00:00+00:00", 91, 93, 78, 80),
    ]


def c4_ltf() -> list[dict]:
    return [
        # Structural anchor for original C2: 09:55 low 100, C2 high 110.
        bar("2026-09-19T09:45:00+00:00", 103, 104, 101, 102),
        bar("2026-09-19T09:50:00+00:00", 102, 103, 102, 103),
        bar("2026-09-19T09:55:00+00:00", 103, 104, 100, 102),
        bar("2026-09-19T10:00:00+00:00", 102, 110, 101, 104),
        # Pre-C3 local structure.
        bar("2026-09-19T10:45:00+00:00", 100, 103, 98, 101),
        bar("2026-09-19T10:50:00+00:00", 101, 105, 99, 104),
        bar("2026-09-19T10:55:00+00:00", 104, 103, 98, 100),
        # C3 CSD before C4 opens.
        bar("2026-09-19T11:00:00+00:00", 102, 108, 101, 104),
        bar("2026-09-19T11:05:00+00:00", 104, 106, 99, 100),
        bar("2026-09-19T11:10:00+00:00", 100, 104, 99, 103),
        bar("2026-09-19T11:15:00+00:00", 103, 103.5, 96, 97),
        bar("2026-09-19T11:20:00+00:00", 97, 100, 95, 96),
        bar("2026-09-19T11:25:00+00:00", 96, 99, 94, 95),
        bar("2026-09-19T11:30:00+00:00", 95, 98, 93, 94),
        bar("2026-09-19T11:35:00+00:00", 94, 97, 92, 93),
        bar("2026-09-19T11:40:00+00:00", 93, 96, 90, 91),
        bar("2026-09-19T11:45:00+00:00", 91, 94, 88, 89),
        bar("2026-09-19T11:50:00+00:00", 89, 92, 86, 87),
        bar("2026-09-19T11:55:00+00:00", 87, 90, 85, 86),
        bar("2026-09-19T12:00:00+00:00", 91, 93, 88, 90),
        bar("2026-09-19T12:05:00+00:00", 90, 91, 79, 80),
    ]


def test_fractal_timeframe_mapping_matches_model() -> None:
    assert FRACTAL_TIMEFRAME_PAIRS == {
        "W1": "H4",
        "D1": "H1",
        "H4": "M15",
        "H1": "M5",
        "M30": "M3",
        "M15": "M1",
    }


def test_exact_c2_sweep_reclaim_rules() -> None:
    bearish_c1 = bar("2026-09-19T10:00:00+00:00", 100, 104, 99, 103)
    bearish_c2 = bar("2026-09-19T11:00:00+00:00", 103, 110, 92, 96)
    bullish_c1 = bar("2026-09-19T10:00:00+00:00", 98, 102, 96, 97)
    bullish_c2 = bar("2026-09-19T11:00:00+00:00", 97, 108, 90, 104)

    assert _c2_direction(bearish_c1, bearish_c2) == "bearish"
    assert _c2_direction(bullish_c1, bullish_c2) == "bullish"

    no_reclaim = dict(bearish_c2)
    no_reclaim["low"] = 100
    no_reclaim["close"] = 105
    assert _c2_direction(bearish_c1, no_reclaim) is None


def test_directional_wick_eq_and_tspot_match_pine_geometry() -> None:
    bearish_c2 = bearish_htf()[-2]
    bullish_c2 = bullish_htf()[-2]

    assert _wick_eq_price("bearish", bearish_c2) == 106.5
    assert _tspot_zone("bearish", bearish_c2) == {
        "top": 103.0,
        "bottom": 96.0,
        "mid": 99.5,
    }

    assert _wick_eq_price("bullish", bullish_c2) == 93.5
    assert _tspot_zone("bullish", bullish_c2) == {
        "top": 104.0,
        "bottom": 97.0,
        "mid": 100.5,
    }


def test_bearish_positional_uses_tspot_valid_swing_and_tv_structural_std() -> None:
    result = evaluate_positional_from_bars(
        bearish_htf(),
        bearish_ltf(),
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["entry_model_confirmed"] is True
    assert result["fractal_stage"] == "C2"
    assert result["entry_candle_label"] == "C3_OPEN"
    assert result["exact_order_price"] == 97
    assert result["selected_protected_swing"]["source"] == (
        "POST_CSD_CONTINUATION_PROTECTED_SWING"
    )
    assert result["stop_reference"] == 104
    # 104 does not cover wick EQ 106.5, but it fully protects bearish T-Spot top 103.
    assert result["selected_protected_swing"]["eq_covers"] is False
    assert result["selected_protected_swing"]["tspot_covers"] is True
    assert result["qualifying_htf_candle"]["directional_wick_equilibrium"] == 106.5
    assert result["target"]["source"] == "TV_FRACTAL_STRUCTURE_STD"
    assert result["target"]["zero_reference"] == 100
    assert result["target"]["one_reference"] == 110
    assert result["target"]["price"] == 80
    assert result["target_rule"] == "TV_FRACTAL_STRUCTURE_STD_-2"
    assert "legacy_csd" in result["target_models"]
    assert result["status"] == "POSITIONAL_TARGET_HIT"
    assert result["execution_allowed"] is False


def test_bullish_positional_is_symmetric() -> None:
    result = evaluate_positional_from_bars(
        bullish_htf(),
        bullish_ltf(),
        direction="bullish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["entry_model_confirmed"] is True
    assert result["exact_order_price"] == 103
    assert result["stop_reference"] == 96
    assert result["qualifying_htf_candle"]["directional_wick_equilibrium"] == 93.5
    assert result["selected_protected_swing"]["tspot_covers"] is True
    assert result["target"]["zero_reference"] == 100
    assert result["target"]["one_reference"] == 90
    assert result["target"]["price"] == 120
    assert result["status"] == "POSITIONAL_TARGET_HIT"
    assert result["execution_allowed"] is False


def test_positional_entry_captures_immediate_expansion_without_new_c3_signal() -> None:
    result = evaluate_positional_from_bars(
        bearish_htf(),
        bearish_ltf(hit_target=False),
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["status"] == "POSITIONAL_ACTIVE"
    assert result["entry_gate_passed"] is True
    assert result["entry_candle_label"] == "C3_OPEN"
    assert "C3_OPEN" in result["state_trace"]
    assert result["do_not_chase_after_open"] is True


def test_protected_swing_that_covers_neither_eq_nor_tspot_falls_back() -> None:
    htf = bearish_htf()
    htf[-2].update({"high": 130, "low": 90, "close": 96})

    ltf = [
        bar("2026-09-19T10:35:00+00:00", 99, 101, 98, 100),
        bar("2026-09-19T10:40:00+00:00", 100, 102, 99, 101),
        bar("2026-09-19T10:45:00+00:00", 101, 105, 100, 104),
        bar("2026-09-19T10:50:00+00:00", 104, 104, 101, 102),
        bar("2026-09-19T10:55:00+00:00", 102, 103, 100, 101),
        # Early C2 extreme does not reclaim the local swing, so it is not the CSD.
        bar("2026-09-19T11:00:00+00:00", 102, 130, 101, 120),
        bar("2026-09-19T11:05:00+00:00", 120, 120, 105, 110),
        bar("2026-09-19T11:10:00+00:00", 110, 110, 101, 103),
        bar("2026-09-19T11:15:00+00:00", 103, 104, 100, 102),
        bar("2026-09-19T11:20:00+00:00", 102, 105, 101, 104),
        bar("2026-09-19T11:25:00+00:00", 104, 104, 101, 102),
        # Later valid bearish CSD protected high = 108, below EQ/T-Spot.
        bar("2026-09-19T11:30:00+00:00", 102, 108, 101, 104),
        bar("2026-09-19T11:35:00+00:00", 104, 106, 99, 100),
        bar("2026-09-19T11:40:00+00:00", 100, 107, 98, 103),
        bar("2026-09-19T11:45:00+00:00", 103, 106, 97, 98),
        bar("2026-09-19T11:50:00+00:00", 98, 104, 95, 96),
        bar("2026-09-19T11:55:00+00:00", 96, 103, 90, 96),
        bar("2026-09-19T12:00:00+00:00", 97, 98, 90, 91),
    ]

    result = evaluate_positional_from_bars(
        htf,
        ltf,
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["entry_model_confirmed"] is False
    assert result["fallback_to_unicorn"] is True
    assert result["status"] == "WAIT_VALID_PROTECTED_SWING"
    assert result["equilibrium"] == 116.5
    assert result["tspot"]["top"] == 113
    assert all(
        candidate["geometry_valid"] is False
        for candidate in result["protected_swing_candidates"]
    )


def test_valid_c3_closure_can_arm_c4_open() -> None:
    result = evaluate_positional_from_bars(
        c4_htf(),
        c4_ltf(),
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["entry_model_confirmed"] is True
    assert result["fractal_stage"] == "C3"
    assert result["entry_candle_label"] == "C4_OPEN"
    assert result["next_htf_candle_open"]["time"] == "2026-09-19T12:00:00+00:00"
    assert result["exact_order_price"] == 91
    assert result["target"]["zero_reference"] == 100
    assert result["target"]["one_reference"] == 110
    assert result["target"]["price"] == 80
    assert "C4_OPEN" in result["state_trace"]


def test_failed_c3_does_not_arm_c4() -> None:
    htf = c4_htf()
    # Violate the original C2 high without letting the failed C3 become a new
    # bearish C2 sweep/reclaim or an opposite bullish setup.
    htf[-2].update({"high": 111, "low": 95, "close": 110.5})

    assert _resolve_fractal_stage(htf, direction="bearish") is None

    result = evaluate_positional_from_bars(
        htf,
        c4_ltf(),
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )
    assert result["status"] == "WAIT_HTF_FRACTAL"
    assert result["fallback_to_unicorn"] is True
