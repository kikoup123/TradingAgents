from __future__ import annotations

import pandas as pd

from tradingagents.ict.entry_execution import (
    EntryExecutionStatus,
    PostCSDIOFEntryEngine,
)


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-09-15 14:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


def _context(direction: str, *, low: float, high: float, confirmed_position: int = 3) -> dict:
    return {
        "execution_gate": {"direction": direction, "smt_validated": True},
        "post_csd_iofc": {
            "expected_direction": direction,
            "confirmed": True,
            "confirmation_range": {
                "direction": direction,
                "source_position": 2,
                "source_time": "2026-09-15T14:10:00+00:00",
                "confirmed_position": confirmed_position,
                "confirmed_time": "2026-09-15T14:15:00+00:00",
                "low": low,
                "high": high,
                "status": "CONFIRMED",
            },
        },
    }


def test_bearish_entry_triggers_on_first_return_to_iof_range_low() -> None:
    bars = _bars(
        [
            (104.0, 105.0, 103.0, 104.5),
            (103.0, 104.0, 102.0, 103.5),
            (101.0, 104.0, 100.0, 103.0),
            (99.0, 100.0, 96.0, 97.0),
            (97.0, 99.5, 95.0, 96.0),
            (96.0, 100.2, 95.5, 99.0),
        ]
    )
    result = PostCSDIOFEntryEngine().analyze(
        _context("BEARISH", low=100.0, high=104.0),
        bars,
    )

    assert result.status == EntryExecutionStatus.ENTRY_TRIGGERED
    assert result.entry is not None
    assert result.entry.position == 5
    assert result.entry.price == 100.0
    assert result.entry.fill_basis == "FIRST_TOUCH_OF_BEARISH_IOF_RANGE_LOW"


def test_bullish_entry_triggers_on_first_return_to_iof_range_high() -> None:
    bars = _bars(
        [
            (96.0, 97.0, 95.0, 96.5),
            (97.0, 98.0, 96.0, 97.5),
            (99.0, 100.0, 96.0, 97.0),
            (101.0, 104.0, 100.5, 103.0),
            (103.0, 105.0, 101.0, 104.0),
            (104.0, 104.5, 99.8, 101.0),
        ]
    )
    result = PostCSDIOFEntryEngine().analyze(
        _context("BULLISH", low=96.0, high=100.0),
        bars,
    )

    assert result.status == EntryExecutionStatus.ENTRY_TRIGGERED
    assert result.entry is not None
    assert result.entry.position == 5
    assert result.entry.price == 100.0
    assert result.entry.fill_basis == "FIRST_TOUCH_OF_BULLISH_IOF_RANGE_HIGH"


def test_open_inside_range_uses_actual_bar_open_as_exact_entry() -> None:
    bars = _bars(
        [
            (104.0, 105.0, 103.0, 104.5),
            (103.0, 104.0, 102.0, 103.5),
            (101.0, 104.0, 100.0, 103.0),
            (99.0, 100.0, 96.0, 97.0),
            (102.0, 103.0, 99.0, 100.5),
        ]
    )
    result = PostCSDIOFEntryEngine().analyze(
        _context("BEARISH", low=100.0, high=104.0),
        bars,
    )

    assert result.status == EntryExecutionStatus.ENTRY_TRIGGERED
    assert result.entry is not None
    assert result.entry.price == 102.0
    assert result.entry.fill_basis == "BAR_OPEN_ALREADY_INSIDE_IOF_RANGE"


def test_entry_waits_when_price_has_not_retraced_to_range() -> None:
    bars = _bars(
        [
            (104.0, 105.0, 103.0, 104.5),
            (103.0, 104.0, 102.0, 103.5),
            (101.0, 104.0, 100.0, 103.0),
            (99.0, 100.0, 96.0, 97.0),
            (97.0, 99.5, 95.0, 96.0),
            (96.0, 99.9, 94.0, 95.0),
        ]
    )
    result = PostCSDIOFEntryEngine().analyze(
        _context("BEARISH", low=100.0, high=104.0),
        bars,
    )

    assert result.status == EntryExecutionStatus.WAIT_FOR_RETRACE
    assert result.entry is None


def test_gap_through_entire_range_does_not_invent_fill() -> None:
    bars = _bars(
        [
            (104.0, 105.0, 103.0, 104.5),
            (103.0, 104.0, 102.0, 103.5),
            (101.0, 104.0, 100.0, 103.0),
            (99.0, 100.0, 96.0, 97.0),
            (105.0, 106.0, 103.0, 104.0),
        ]
    )
    result = PostCSDIOFEntryEngine().analyze(
        _context("BEARISH", low=100.0, high=104.0),
        bars,
    )

    assert result.status == EntryExecutionStatus.RANGE_SKIPPED_BY_GAP
    assert result.entry is None


def test_unconfirmed_post_csd_iof_range_cannot_create_entry() -> None:
    context = _context("BEARISH", low=100.0, high=104.0)
    context["post_csd_iofc"]["confirmed"] = False
    context["post_csd_iofc"]["confirmation_range"] = None
    bars = _bars([(100.0, 101.0, 99.0, 100.5)] * 5)

    result = PostCSDIOFEntryEngine().analyze(context, bars)

    assert result.status == EntryExecutionStatus.WAIT_FOR_VALIDATED_IOF_RANGE
    assert result.entry is None
