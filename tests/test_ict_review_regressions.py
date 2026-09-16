from __future__ import annotations

from datetime import timedelta, timezone

import pandas as pd

from tradingagents.ict import (
    FairValueEngine,
    LondresPhase5Engine,
    LondresPhase7Engine,
    SMTEngine,
)


def _frame(rows, *, start="2026-09-16 13:00", tz="UTC") -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        dtype=float,
        index=pd.date_range(start, periods=len(rows), freq="h", tz=tz),
    )


def _flat_frame(*, tz="UTC") -> pd.DataFrame:
    return _frame(
        [(100, 101, 99, 100), (100, 102, 99, 101), (101, 102, 100, 101)],
        tz=tz,
    )


def _bullish_gap() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (10, 11, 9, 10),
            (10, 12, 9.5, 11),
            (10.5, 11, 9, 10),
            (10, 14, 9.8, 13),
            (13, 15, 12.5, 14),
        ],
        columns=["open", "high", "low", "close"],
        dtype=float,
        index=pd.date_range("2026-09-16 09:00", periods=5, freq="5min", tz="UTC"),
    )


def test_smt_naive_cutoff_is_interpreted_in_fixed_utc_minus_4() -> None:
    bars = _flat_frame()
    synchronized = SMTEngine._synchronize(
        {"NQ": bars, "ES": bars.copy(), "YM": bars.copy()},
        as_of="2026-09-16 10:00",
    )
    assert list(synchronized["NQ"].index) == list(bars.index[:2])
    assert synchronized["NQ"].index[-1] == pd.Timestamp("2026-09-16 14:00", tz="UTC")


def test_phase5_applies_cutoff_to_profile_liquidity_and_smt_inputs() -> None:
    bars = _flat_frame()
    engine = LondresPhase5Engine()

    class Phase4Probe:
        def analyze(self, **kwargs):
            assert all(len(data) == 2 for data in kwargs["timeframe_bars"].values())
            assert len(kwargs["intraday_bars"]) == 2
            assert len(kwargs["minute_bars"]) == 2
            return {
                "profile_stack": {"order_flow": {"4H": {"control": "BULLISH"}}},
                "time_price": {},
                "liquidity_timeframe": "4H",
                "liquidity": {},
            }

    class SMTProbeResult:
        def to_dict(self):
            return {"detected": False, "validated": False}

    class SMTProbe:
        def analyze(self, bounded_bars, **kwargs):
            del kwargs
            assert all(len(data) == 2 for data in bounded_bars.values())
            return SMTProbeResult()

    engine.phase4 = Phase4Probe()
    engine.smt = SMTProbe()
    result = engine.analyze(
        timeframe_bars={"1D": bars, "4H": bars},
        intraday_bars=bars,
        minute_bars=bars,
        smt_bars={"NQ": bars, "ES": bars, "YM": bars},
        smt_group="US_INDEX",
        smt_timeframe="4H",
        as_of="2026-09-16 10:00",
    )
    assert result["smt"]["validated"] is False


def test_structural_fvg_requires_impulse_to_trade_through_reference() -> None:
    bars = _bullish_gap()
    bars.iloc[3] = (12.5, 14, 12.4, 13)
    gap = FairValueEngine(pivot_span=1).analyze(bars, timeframe="5m")["gaps"][0]
    assert gap["structural_reference"] is None
    assert "NO_STRUCTURAL_CLOSE_THROUGH" in gap["reason_codes"]


def test_fvg_invalidation_candle_keeps_touch_fill_and_return_evidence() -> None:
    bars = _bullish_gap()
    invalidating = pd.DataFrame(
        [(13, 13.2, 10.5, 10.8)],
        columns=bars.columns,
        dtype=float,
        index=[bars.index[-1] + pd.Timedelta(minutes=5)],
    )
    bars = pd.concat([bars, invalidating])
    gap = FairValueEngine(pivot_span=1).analyze(bars, timeframe="5m")["gaps"][0]
    assert gap["status"] == "INVALIDATED"
    assert gap["invalidated_position"] == 5
    assert gap["first_touch_position"] == 5
    assert gap["filled_position"] == 5
    assert gap["pairing_return_position"] == 5


def test_phase7_same_stream_accepts_equivalent_aware_timezones() -> None:
    utc_bars = _flat_frame()
    fixed_clock = timezone(timedelta(hours=-4))
    execution_bars = utc_bars.copy()
    execution_bars.index = execution_bars.index.tz_convert(fixed_clock)

    engine = LondresPhase7Engine()

    class Phase6Probe:
        def analyze(self, **kwargs):
            del kwargs
            return {
                "execution_gate": {"direction": "BULLISH", "smt_validated": True},
                "csd_timeframe": "5m",
            }

    class NarrativeProbe:
        def analyze(self, *args, **kwargs):
            del args, kwargs
            return {
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

    engine.phase6 = Phase6Probe()
    engine.narrative = NarrativeProbe()
    result = engine.analyze(
        timeframe_bars={"5m": utc_bars},
        csd_bars=execution_bars,
    )
    assert result["narrative_gate"]["qualified"] is True
    assert "NARRATIVE_EXECUTION_STREAM_MISMATCH" not in result["narrative_gate"][
        "reason_codes"
    ]
