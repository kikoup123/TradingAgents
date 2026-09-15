from __future__ import annotations

import json

import pandas as pd
import pytest

from tradingagents.ict import (
    Direction,
    FairValueEngine,
    LondresPhase6Engine,
    LondresPhase7Engine,
    NarrativeEngine,
    OrderFlowEngine,
    PriceDeliveryEngine,
    RangeStatus,
    classify_liquidity_run,
)
from tradingagents.ict.market_data import closed_bars


def frame(rows, *, freq="5min", start="2026-09-14 18:00"):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        dtype=float,
        index=pd.date_range(start, periods=len(rows), freq=freq, tz="UTC"),
    )


def bullish_gap():
    return frame(
        [
            (10, 11, 9, 10),
            (10, 12, 9.5, 11),
            (10.5, 11, 9, 10),
            (10, 14, 9.8, 13),
            (13, 15, 12.5, 14),
        ]
    )


def mirror(bars):
    return pd.DataFrame(
        {
            "open": 30 - bars.open,
            "high": 30 - bars.low,
            "low": 30 - bars.high,
            "close": 30 - bars.close,
        },
        index=bars.index,
    )


@pytest.mark.parametrize("bearish", [False, True])
def test_structural_fvg_is_known_only_at_third_close(bearish):
    bars = mirror(bullish_gap()) if bearish else bullish_gap()
    engine = FairValueEngine(pivot_span=1)
    assert engine.analyze(bars.iloc[:-1], timeframe="5m")["gaps"] == []
    gap = engine.analyze(bars, timeframe="5m")["gaps"][0]
    assert gap["structural_reference"]["confirmed_position"] == 2
    assert gap["source_position"] == 3
    assert gap["formation_position"] == 4
    assert gap["fair_valuation_point"] == (18 if bearish else 12)
    assert gap["consequent_encroachment"] != gap["fair_valuation_point"]
    assert gap["status"] == "OPEN"
    assert gap["pairing_return_position"] is None


@pytest.mark.parametrize("bearish", [False, True])
def test_gap_rebalance_wick_and_body_invalidation_are_distinct(bearish):
    bars = frame(
        bullish_gap().values.tolist()
        + [(14, 14.2, 12, 13), (13, 13.5, 10.8, 12.7), (12.7, 13, 10, 10.5)]
    )
    if bearish:
        bars = mirror(bars)
    engine = FairValueEngine(pivot_span=1)
    partial = engine.analyze(bars.iloc[:6], timeframe="5m")["gaps"][0]
    assert partial["status"] == "PARTIALLY_REBALANCED"
    assert partial["pairing_return_position"] == partial["pairing_rejection_position"] == 5
    balanced = engine.analyze(bars.iloc[:7], timeframe="5m")["gaps"][0]
    assert balanced["status"] == "REBALANCED"
    failed = engine.analyze(bars, timeframe="5m")["gaps"][0]
    assert failed["status"] == "INVALIDATED"
    assert failed["invalidated_position"] == 7
    assert failed["first_touch_position"] == 5


def test_wick_break_does_not_qualify_structural_fvg():
    bars = bullish_gap()
    bars.iloc[3, bars.columns.get_loc("close")] = 11.9
    gap = FairValueEngine(pivot_span=1).analyze(bars, timeframe="5m")["gaps"][0]
    assert gap["structural_reference"] is None


def test_no_fvg_for_equal_edges_and_no_self_touch():
    bars = bullish_gap()
    bars.iloc[4, bars.columns.get_loc("low")] = 11
    assert FairValueEngine().analyze(bars, timeframe="5m")["gaps"] == []
    assert (
        FairValueEngine(pivot_span=1).analyze(bullish_gap(), timeframe="5m")["gaps"][0][
            "first_touch_position"
        ]
        is None
    )


@pytest.mark.parametrize("bearish", [False, True])
def test_ordered_delivery_sequence_requires_raid_gap_return_and_later_acceptance(bearish):
    bars = frame(
        [
            (10, 11, 9, 10),
            (10, 12, 8, 11),
            (11, 11.5, 9, 10),
            (10, 11, 7.5, 10.5),
            (10.5, 15, 10, 14),
            (14, 16, 12, 15),
            (15, 15.5, 10.8, 12),
            (12, 17, 11.5, 16),
        ]
    )
    if bearish:
        bars = mirror(bars)
    engine = PriceDeliveryEngine(pivot_span=1)
    distributed = engine.analyze(bars.iloc[:6], timeframe="5m")
    assert distributed["sequence"] == "DISTRIBUTE"
    assert distributed["expected_next"] == "REBALANCE"
    result = engine.analyze(bars, timeframe="5m")
    events = [e["event"] for e in result["events"]]
    assert events.index("DISTRIBUTE") < events.index("REBALANCE")
    assert events[-1] == ("REDISTRIBUTION" if bearish else "REACCUMULATION")
    assert result["sequence"] == "REDISTRIBUTE"
    assert engine.analyze(bars.iloc[:7], timeframe="5m")["cycle"] == "IMBALANCE_TO_STOPS"
    assert result["cycle"] == "STOPS_TO_IMBALANCE"  # continuation takes fresh stops


def test_expansion_without_raid_does_not_invent_complete_sequence():
    bars = frame([(10, 11, 9, 10), (10, 15, 9.5, 14), (14, 16, 12, 15)])
    state = PriceDeliveryEngine(pivot_span=1).analyze(bars, timeframe="5m")
    assert state["phase"] == "EXPANSION"
    assert not any(e["event"] == "DISTRIBUTE" for e in state["events"])


@pytest.mark.parametrize("direction", ["BULLISH", "BEARISH"])
def test_liquidity_run_is_relative_to_control_and_parent_boundary(direction):
    other = "BEARISH" if direction == "BULLISH" else "BULLISH"
    assert classify_liquidity_run(direction, direction)["classification"] == "LRLR"
    assert classify_liquidity_run(direction, other)["classification"] == "HRLR"
    assert (
        classify_liquidity_run(direction, direction, crosses_parent_matrix=True)["target_scope"]
        == "IRL_OR_PARENT_MATRIX"
    )
    assert classify_liquidity_run(direction, "TRANSITION")["classification"] == "UNRESOLVED"


def test_ltf_counter_flow_keeps_parent_control_and_stops_at_matrix():
    parent = frame([(10, 11, 5, 9), (9, 15, 8, 14)], freq="1h", start="2026-09-14 15:00")
    local = frame([(14, 16, 13, 15), (15, 15.5, 11.5, 12)])
    engine = NarrativeEngine(pivot_span=1)
    result = engine.analyze({"1D": parent, "5m": local})
    state = result["timeframes"]["5m"]
    assert result["bias"] == "BULLISH"
    assert state["control"] == "BEARISH"
    assert state["profile"] == "RETRACEMENT"
    assert state["liquidity_run"]["classification"] == "LRLR"
    assert state["parent_relative_run"]["classification"] == "HRLR"
    reached = frame(local.values.tolist() + [(12, 12.5, 10, 10.5)])
    state = engine.analyze({"1D": parent, "5m": reached})["timeframes"]["5m"]
    assert state["profile"] == "AT_PARENT_MATRIX_WAIT_FOR_SHIFT"
    assert state["liquidity_run"]["classification"] == "HRLR"
    assert state["reversal"]["confirmed"] is False
    assert state["parent_control"] == "BULLISH"


def test_prefix_replay_is_identical_with_future_invalidations_present():
    bars = frame(bullish_gap().values.tolist() + [(14, 14.2, 12, 13), (13, 13.5, 10, 10.5)])
    for engine in (FairValueEngine(pivot_span=1), PriceDeliveryEngine(pivot_span=1)):
        for end in range(2, len(bars) + 1):
            assert engine.analyze(
                bars, timeframe="5m", as_of=bars.index[end - 1]
            ) == engine.analyze(bars.iloc[:end], timeframe="5m")
    engine = NarrativeEngine(pivot_span=1)
    assert engine.analyze({"5m": bars}, as_of=bars.index[4]) == engine.analyze(
        {"5m": bars.iloc[:5]}
    )
    json.dumps(engine.analyze({"5m": bars}), allow_nan=False)


@pytest.mark.parametrize("invalid", ["duplicate", "nan", "inf"])
def test_bad_market_data_is_rejected(invalid):
    bars = bullish_gap()
    if invalid == "duplicate":
        bars.index = [bars.index[0]] * len(bars)
    else:
        bars.iloc[0, 0] = float(invalid)
    with pytest.raises(ValueError):
        closed_bars(bars)


def test_pruned_confirmed_iof_ranges_still_invalidate():
    bars = frame([(10, 11, 5, 9), (12, 13, 10, 12.5), (12.5, 12.6, 11, 12), (12, 12, 4, 4.5)])
    result = OrderFlowEngine(max_candidate_ranges=1).analyze(bars, timeframe="5m")
    first = next(e for e in result.confirmed_events if e.source_position == 0)
    assert first.status == RangeStatus.INVALIDATED
    assert first.invalidated_position == 3


def test_invalidated_post_csd_iofc_is_not_live_confirmation():
    bars = frame([(10, 11, 9, 10.5), (10.5, 11, 9, 10), (10, 12, 10, 11.5), (11.5, 11.5, 8, 8.5)])
    result = OrderFlowEngine().find_iofc_after(
        bars, anchor_position=0, expected_direction=Direction.BULLISH
    )
    assert result.confirmed is False


def phase6_inputs(bars):
    return {
        "timeframe_bars": {"1D": bars, "4H": bars, "5m": bars},
        "intraday_bars": bars,
        "minute_bars": bars,
        "csd_bars": bars,
        "csd_timeframe": "5m",
        "smt_bars": {"NQ": bars, "ES": bars, "YM": bars},
        "smt_group": "US_INDEX",
        "smt_timeframe": "5m",
    }


def test_full_phase6_and_phase7_honor_cutoff_in_every_stack_branch():
    bars = frame(bullish_gap().values.tolist() + [(14, 14.2, 12, 13), (13, 13.5, 10, 10.5)])
    for engine in (LondresPhase6Engine(), LondresPhase7Engine()):
        actual = engine.analyze(**phase6_inputs(bars), as_of=bars.index[4])
        prefix = engine.analyze(**phase6_inputs(bars.iloc[:5]), as_of=bars.index[4])
        assert actual == prefix
        if isinstance(engine, LondresPhase7Engine):
            update = engine.state_update(actual)
            assert update["execution_gate_state"] == actual["execution_gate"]
            assert update["narrative_gate_state"]["qualified"] is False
            json.dumps(update, allow_nan=False)


def test_consolidation_departure_excludes_the_impulse_from_original_range():
    bars = frame(
        [
            (10, 11, 9, 10.5),
            (10.5, 11.2, 9.5, 10),
            (10, 11, 9.6, 10.6),
            (10.6, 15, 10.4, 14),
            (14, 16, 12, 15),
        ]
    )
    oc = PriceDeliveryEngine().analyze(bars, timeframe="5m")["original_consolidation"]
    assert oc["end_position"] == 2
    assert oc["high"] == 11.2
    assert oc["status"] == "DISPLACEMENT_CONFIRMED"


def test_delivery_events_do_not_rewrite_earlier_evidence_after_future_returns():
    bars = frame(
        [
            (10, 11, 9, 10),
            (10, 12, 8, 11),
            (11, 11.5, 9, 10),
            (10, 11, 7.5, 10.5),
            (10.5, 15, 10, 14),
            (14, 16, 12, 15),
            (15, 15.5, 10.8, 12),
            (12, 17, 11.5, 16),
        ]
    )
    engine = PriceDeliveryEngine(pivot_span=1)
    history = engine.analyze(bars.iloc[:6], timeframe="5m")["events"]
    later = engine.analyze(bars, timeframe="5m")["events"]
    assert history == [event for event in later if event["position"] < 6]


def test_parent_unknown_is_not_overridden_by_directional_child():
    parent = frame([(10, 11, 9, 10)])
    result = NarrativeEngine().analyze({"1D": parent, "5m": bullish_gap()})
    assert result["bias"] == "UNCONFIRMED"
    assert result["timeframes"]["5m"]["profile"] == "UNRESOLVED"
    assert result["context_confirmed"] is False


def csd_bars():
    return frame(
        [
            (10, 11, 9.5, 10.4),
            (11.8, 12, 10.5, 11.2),
            (10.2, 10.5, 8, 9),
            (10.8, 11, 9, 10),
            (10, 10.5, 7.5, 9.2),
            (9.4, 10.8, 7.3, 10.2),
            (10.4, 11.95, 9.8, 11.9),
            (11.7, 11.8, 11, 11.2),
            (11.3, 12.2, 11.2, 12),
        ]
    )


def test_matrix_reversal_needs_post_csd_iofc_and_still_preserves_parent():
    parent = frame([(10, 11, 6, 9), (9, 15, 8, 14)], freq="1h", start="2026-09-14 15:00")
    engine = NarrativeEngine(pivot_span=1)
    before = engine.analyze({"1D": parent, "5m": csd_bars().iloc[:7]})
    assert before["timeframes"]["5m"]["reversal"]["confirmed"] is False
    result = engine.analyze({"1D": parent, "5m": csd_bars()})
    assert result["timeframes"]["5m"]["reversal"]["confirmed"] is True
    assert result["timeframes"]["5m"]["profile"] == "REVERSAL_CONFIRMED"
    assert result["timeframes"]["1D"]["control"] == "BULLISH"


def test_phase6_post_csd_scan_cannot_use_future_iofc_or_broken_protection():
    from tradingagents.ict import CSDEngine

    bars = csd_bars()
    event = CSDEngine(pivot_span=1).analyze(bars.iloc[:7], timeframe="5m").latest_event
    engine = LondresPhase6Engine()
    assert engine._post_csd_iofc(bars.iloc[:7], event).confirmed is False
    assert engine._post_csd_iofc(bars, event).confirmed is True
    broken = frame(bars.values.tolist() + [(12, 12.1, 7, 7.1)])
    result = engine._post_csd_iofc(broken, event)
    assert result.confirmed is False
    assert result.reason == "CSD_PROTECTED_EXTREME_INVALIDATED"


def test_phase7_gate_never_bypasses_phase6_or_accepts_another_stream(monkeypatch):
    # Gate contract test with known upstream evidence; the full real engine
    # cutoff/serialization test above exercises the non-stubbed composition.
    bars = bullish_gap()
    engine = LondresPhase7Engine()
    context = {
        "execution_gate": {"smt_validated": True, "direction": "BULLISH"},
        "csd_timeframe": "5m",
    }
    narrative = {
        "execution_timeframe": "5m",
        "bias": "BULLISH",
        "context_confirmed": True,
        "timeframes": {
            "5m": {
                "control": "BULLISH",
                "narrative_draw": {"purpose": "LIQUIDITY_OBJECTIVE"},
                "fair_value": {},
                "price_delivery": {},
                "liquidity_run": {},
                "parent_relative_run": {},
            }
        },
    }
    monkeypatch.setattr(engine.phase6, "analyze", lambda **kwargs: context)
    monkeypatch.setattr(engine.narrative, "analyze", lambda *args, **kwargs: narrative)
    assert engine.analyze(**phase6_inputs(bars))["narrative_gate"]["qualified"] is True
    context["execution_gate"]["smt_validated"] = False
    assert engine.analyze(**phase6_inputs(bars))["narrative_gate"]["qualified"] is False
    context["execution_gate"]["smt_validated"] = True
    inputs = phase6_inputs(bars)
    inputs["csd_bars"] = mirror(bars)
    gate = engine.analyze(**inputs)["narrative_gate"]
    assert gate["qualified"] is False
    assert "NARRATIVE_EXECUTION_STREAM_MISMATCH" in gate["reason_codes"]


def test_mixed_timezone_inputs_share_fixed_utc4_cutoff():
    from tradingagents.ict.daily_profile import FIXED_UTC_MINUS_4

    utc = bullish_gap()
    naive = utc.copy()
    naive.index = naive.index.tz_convert(FIXED_UTC_MINUS_4).tz_localize(None)
    aware = closed_bars(utc, utc.index[2])
    local = closed_bars(naive, utc.index[2])
    local.index = local.index.tz_convert("UTC")
    pd.testing.assert_frame_equal(aware, local, check_freq=False)
    pd.testing.assert_frame_equal(aware, closed_bars(utc, naive.index[2]))


def test_two_sided_raid_does_not_create_directional_sequence():
    bars = frame([(10, 11, 9, 10), (10, 12, 8, 10.5), (10.5, 11, 9, 10),
                  (10, 13, 7, 10), (10, 16, 9, 15), (15, 17, 14, 16)])
    result = PriceDeliveryEngine(pivot_span=1).analyze(bars, timeframe="5m")
    assert any(e["event"] == "AMBIGUOUS_TWO_SIDED_RAID" for e in result["events"])
    assert not any(e["event"] == "DISTRIBUTE" for e in result["events"])
