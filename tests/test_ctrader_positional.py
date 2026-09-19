from __future__ import annotations

from datetime import datetime, timedelta

from tradingagents.dataflows.ctrader_positional import (
    FRACTAL_TIMEFRAME_PAIRS,
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
        bar("2026-09-19T08:00:00+00:00", 95, 100, 94, 98),
        bar("2026-09-19T09:00:00+00:00", 104, 108, 100, 101),
        bar("2026-09-19T10:00:00+00:00", 100, 104, 99, 103),
        bar("2026-09-19T11:00:00+00:00", 103, 110, 92, 96),
        bar("2026-09-19T12:00:00+00:00", 97, 98, 78, 80),
    ]


def bearish_ltf(*, hit_target: bool = True) -> list[dict]:
    rows = [
        bar("2026-09-19T10:35:00+00:00", 99, 101, 98, 100),
        bar("2026-09-19T10:40:00+00:00", 100, 102, 99, 101),
        bar("2026-09-19T10:45:00+00:00", 101, 105, 100, 104),
        bar("2026-09-19T10:50:00+00:00", 104, 104, 101, 102),
        bar("2026-09-19T10:55:00+00:00", 102, 103, 100, 101),
        # BSL raid and bearish CSD inside the 11:00 H1 candle.
        bar("2026-09-19T11:00:00+00:00", 102, 108, 101, 104),
        bar("2026-09-19T11:05:00+00:00", 104, 106, 99, 100),
        # New bearish continuation protected swing at 104.
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
        # Candle 3 opens and expands without requiring another CSD/retrace.
        bar("2026-09-19T12:00:00+00:00", 97, 98, 90, 91),
    ]
    if hit_target:
        rows.append(
            bar("2026-09-19T12:05:00+00:00", 91, 92, 79, 80)
        )
    return rows


def bullish_htf() -> list[dict]:
    return [
        bar("2026-09-19T08:00:00+00:00", 100, 106, 99, 102),
        bar("2026-09-19T09:00:00+00:00", 96, 100, 92, 95),
        bar("2026-09-19T10:00:00+00:00", 98, 102, 96, 97),
        bar("2026-09-19T11:00:00+00:00", 97, 108, 90, 104),
        bar("2026-09-19T12:00:00+00:00", 103, 116, 102, 115),
    ]


def bullish_ltf() -> list[dict]:
    return [
        bar("2026-09-19T10:35:00+00:00", 101, 102, 99, 100),
        bar("2026-09-19T10:40:00+00:00", 100, 101, 97, 98),
        bar("2026-09-19T10:45:00+00:00", 98, 99, 95, 96),
        bar("2026-09-19T10:50:00+00:00", 96, 99, 96, 98),
        bar("2026-09-19T10:55:00+00:00", 98, 100, 97, 99),
        # SSL raid and bullish CSD inside the 11:00 H1 candle.
        bar("2026-09-19T11:00:00+00:00", 98, 99, 92, 96),
        bar("2026-09-19T11:05:00+00:00", 96, 101, 95, 100),
        # New bullish continuation protected swing at 96.
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
        bar("2026-09-19T12:05:00+00:00", 107, 115, 106, 114),
    ]


def shift_bars(rows: list[dict], *, hours: int) -> list[dict]:
    shifted = []
    for row in rows:
        copied = dict(row)
        copied["time"] = (
            datetime.fromisoformat(row["time"]) + timedelta(hours=hours)
        ).isoformat()
        shifted.append(copied)
    return shifted


def test_fractal_timeframe_mapping_matches_positional_model() -> None:
    assert FRACTAL_TIMEFRAME_PAIRS == {
        "W1": "H4",
        "D1": "H1",
        "H4": "M15",
        "H1": "M5",
        "M30": "M3",
        "M15": "M1",
    }


def test_bearish_positional_uses_latest_eq_valid_continuation_and_htf_std_minus_two() -> None:
    result = evaluate_positional_from_bars(
        bearish_htf(),
        bearish_ltf(),
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["entry_model_confirmed"] is True
    assert result["exact_order_price"] == 97
    assert result["selected_protected_swing"]["source"] == (
        "POST_CSD_CONTINUATION_PROTECTED_SWING"
    )
    assert result["stop_reference"] == 104
    assert result["qualifying_htf_candle"]["equilibrium"] == 101
    assert result["target"]["timeframe"] == "H1"
    assert result["target"]["zero_reference"] == 100
    assert result["target"]["protected_extreme"] == 110
    assert result["target"]["price"] == 80
    assert result["target_rule"] == "HTF_CSD_STD_-2"
    assert result["status"] == "POSITIONAL_TARGET_HIT"
    assert result["fallback_to_unicorn"] is False
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
    assert result["qualifying_htf_candle"]["equilibrium"] == 99
    assert result["target"]["zero_reference"] == 98
    assert result["target"]["protected_extreme"] == 90
    assert result["target"]["price"] == 114
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
    assert result["exact_order_price"] == 97
    assert result["entry_rule"] == "NEXT_HTF_OPEN_AFTER_VALID_C2_PROTECTED_SWING"
    assert "C3_OPEN" in result["state_trace"]
    assert result["do_not_chase_after_open"] is True


def test_nearer_protected_swing_below_bearish_eq_is_rejected_for_farther_valid_swing() -> None:
    ltf = bearish_ltf(hit_target=False)
    # Make the post-CSD continuation protected high 100, below C2 EQ 101.
    for row in ltf:
        if row["time"] == "2026-09-19T11:10:00+00:00":
            row.update({"open": 98, "high": 100, "low": 97, "close": 99})
        elif row["time"] == "2026-09-19T11:15:00+00:00":
            row.update({"open": 99, "high": 99.5, "low": 94, "close": 95})
        elif "2026-09-19T11:20:00+00:00" <= row["time"] < "2026-09-19T12:00:00+00:00":
            row["high"] = min(row["high"], 99.5)

    result = evaluate_positional_from_bars(
        bearish_htf(),
        ltf,
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["selected_protected_swing"]["source"] == "CSD_PROTECTED_SWING"
    assert result["stop_reference"] == 108
    rejected = [
        item
        for item in result["protected_swing_candidates"]
        if item["source"] == "POST_CSD_CONTINUATION_PROTECTED_SWING"
    ]
    assert rejected
    assert rejected[-1]["protected_swing"] == 100
    assert rejected[-1]["eq_valid"] is False


def test_no_eq_valid_protected_swing_falls_back_to_normal_unicorn_engine() -> None:
    htf = bearish_htf()
    # An early C2 excursion sets a high of 130 and a low of 90, so C2 EQ is
    # 110. The later bearish CSD forms from a local 105 pivot and leaves a
    # protected high at 108, which is below EQ and therefore not a valid
    # positional stop.
    htf[-2].update({"high": 130, "low": 90})

    ltf = [
        # Older descending highs prevent the early C2 excursion from being the
        # latest structural pivot used by the later CSD.
        bar("2026-09-19T10:35:00+00:00", 134, 136, 132, 135),
        bar("2026-09-19T10:40:00+00:00", 133, 135, 131, 132),
        bar("2026-09-19T10:45:00+00:00", 132, 134, 130, 131),
        bar("2026-09-19T10:50:00+00:00", 131, 133, 129, 130),
        bar("2026-09-19T10:55:00+00:00", 130, 132, 128, 129),
        bar("2026-09-19T11:00:00+00:00", 129, 130, 90, 120),
        bar("2026-09-19T11:05:00+00:00", 120, 120, 105, 110),
        bar("2026-09-19T11:10:00+00:00", 110, 110, 101, 103),
        bar("2026-09-19T11:15:00+00:00", 103, 104, 100, 102),
        # Local pivot high used as the BSL reference.
        bar("2026-09-19T11:20:00+00:00", 102, 105, 101, 104),
        bar("2026-09-19T11:25:00+00:00", 104, 104, 101, 102),
        # Raid 105 and confirm bearish delivery on the next bar.
        bar("2026-09-19T11:30:00+00:00", 102, 108, 101, 104),
        bar("2026-09-19T11:35:00+00:00", 104, 106, 99, 100),
        bar("2026-09-19T11:40:00+00:00", 100, 107, 98, 103),
        bar("2026-09-19T11:45:00+00:00", 103, 106, 97, 98),
        bar("2026-09-19T11:50:00+00:00", 98, 104, 95, 96),
        bar("2026-09-19T11:55:00+00:00", 96, 103, 93, 94),
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
    assert result["equilibrium"] == 110
    assert all(
        candidate["eq_valid"] is False
        for candidate in result["protected_swing_candidates"]
    )


def test_same_engine_supports_c4_continuation_entry() -> None:
    htf = shift_bars(bearish_htf(), hours=1)
    ltf = shift_bars(bearish_ltf(hit_target=False), hours=1)

    result = evaluate_positional_from_bars(
        htf,
        ltf,
        direction="bearish",
        htf_timeframe="H1",
        pivot_window=1,
    )

    assert result["entry_model_confirmed"] is True
    assert result["qualifying_htf_candle"]["time"] == "2026-09-19T12:00:00+00:00"
    assert result["next_htf_candle_open"]["time"] == "2026-09-19T13:00:00+00:00"
    assert result["exact_order_price"] == 97
