from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.ict.models import Direction
from tradingagents.ict.smt import SMTEngine, SMTValidationState


def _bars(highs: list[float], lows: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-09-14 18:00", periods=len(highs), freq="h", tz="UTC")
    opens = [(high + low) / 2.0 for high, low in zip(highs, lows, strict=True)]
    closes = list(opens)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=index,
    )


def _same_direction_group() -> dict[str, pd.DataFrame]:
    common_lows = [8.0, 9.0, 7.0, 8.0, 7.5, 8.0, 7.8, 8.1]
    return {
        "NAS100": _bars(
            [10.0, 12.0, 10.0, 11.0, 10.0, 13.0, 10.5, 10.2],
            common_lows,
        ),
        "US500": _bars(
            [10.0, 12.0, 10.0, 11.0, 10.0, 10.5, 10.4, 10.2],
            common_lows,
        ),
        "US30": _bars(
            [10.0, 12.0, 10.0, 11.0, 10.0, 10.6, 10.4, 10.2],
            common_lows,
        ),
    }


def _fx_dxy_group(*, dxy_high_take: bool = False) -> dict[str, pd.DataFrame]:
    fx_lows = [1.08, 1.09, 1.07, 1.08, 1.075, 1.08, 1.078, 1.081]
    fx_highs_event = [1.10, 1.12, 1.10, 1.11, 1.10, 1.13, 1.105, 1.102]
    dxy_highs = [100.0, 102.0, 100.0, 101.0, 100.0, 103.0 if dxy_high_take else 101.0, 100.5, 100.2]
    dxy_lows = [98.0, 99.0, 97.0, 98.0, 97.5, 98.0, 97.8, 98.1]
    return {
        "EURUSD": _bars(fx_highs_event, fx_lows),
        "GBPUSD": _bars(fx_highs_event, fx_lows),
        "DXY": _bars(dxy_highs, dxy_lows),
    }


def test_same_direction_index_smt_detects_bearish_nonconfirmation() -> None:
    result = SMTEngine(pivot_span=1).analyze(
        _same_direction_group(),
        group="US_INDEX",
        timeframe="1H",
    )

    assert result.detected is True
    assert result.direction == Direction.BEARISH
    assert result.divergence_type == "HIGH_SIDE_NONCONFIRMATION"
    assert result.leader_symbols == ["NQ"]
    assert set(result.nonconfirming_symbols) == {"ES", "YM"}
    assert result.validated is False
    assert result.validation_state == SMTValidationState.SMT_DETECTED_WAIT_CSD


def test_dxy_is_normalized_as_inverse_leg() -> None:
    result = SMTEngine(pivot_span=1).analyze(
        _fx_dxy_group(),
        group="FX_DXY",
        timeframe="1H",
    )

    assert result.detected is True
    assert result.direction == Direction.BEARISH
    assert set(result.leader_symbols) == {"EURUSD", "GBPUSD"}
    assert result.nonconfirming_symbols == ["DXY"]
    assert result.polarity_map["DXY"] == "INVERSE"
    assert "INVERSE_POLARITY_NORMALIZED" in result.reason_codes


def test_dxy_high_take_maps_to_bullish_canonical_smt() -> None:
    data = _fx_dxy_group(dxy_high_take=True)
    for symbol in ("EURUSD", "GBPUSD"):
        data[symbol].iloc[5, data[symbol].columns.get_loc("high")] = 1.105

    result = SMTEngine(pivot_span=1).analyze(
        data,
        group="FX_DXY",
        timeframe="1H",
    )

    assert result.detected is True
    assert result.direction == Direction.BULLISH
    assert result.leader_symbols == ["DXY"]
    assert set(result.nonconfirming_symbols) == {"EURUSD", "GBPUSD"}


@pytest.mark.parametrize(
    ("csd", "iof", "expected", "validated"),
    [
        (
            Direction.UNCONFIRMED,
            Direction.BEARISH,
            SMTValidationState.SMT_DETECTED_WAIT_CSD,
            False,
        ),
        (
            Direction.BEARISH,
            Direction.UNCONFIRMED,
            SMTValidationState.SMT_DETECTED_WAIT_IOF,
            False,
        ),
        (
            Direction.BULLISH,
            Direction.BEARISH,
            SMTValidationState.SMT_DIRECTION_CONFLICT,
            False,
        ),
        (
            Direction.BEARISH,
            Direction.BULLISH,
            SMTValidationState.SMT_DIRECTION_CONFLICT,
            False,
        ),
        (
            Direction.BEARISH,
            Direction.BEARISH,
            SMTValidationState.SMT_VALIDATED,
            True,
        ),
    ],
)
def test_smt_requires_aligned_csd_and_iof(
    csd: Direction,
    iof: Direction,
    expected: SMTValidationState,
    validated: bool,
) -> None:
    result = SMTEngine(pivot_span=1).analyze(
        _same_direction_group(),
        group="US_INDEX",
        timeframe="1H",
        csd_direction=csd,
        iof_direction=iof,
    )

    assert result.validation_state == expected
    assert result.validated is validated


def test_missing_timestamp_is_not_forward_filled_for_smt() -> None:
    data = _same_direction_group()
    event_time = data["US500"].index[5]
    data["US500"] = data["US500"].drop(index=event_time)

    result = SMTEngine(pivot_span=1).analyze(
        data,
        group="US_INDEX",
        timeframe="1H",
    )

    assert result.detected is False
    assert result.validated is False
