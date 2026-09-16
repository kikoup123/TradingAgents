from __future__ import annotations

from tradingagents.ict.executable_stop import (
    ExecutableStopEngine,
    ExecutableStopStatus,
)


def _entry(direction: str, price: float = 100.0) -> dict:
    return {
        "status": "ENTRY_TRIGGERED",
        "direction": direction,
        "exact_entry_price": price,
    }


def _selection(anchor: float, *, source: str = "IOF_RANGE", placement: str = "") -> dict:
    return {
        "valid": True,
        "selected_source": source,
        "selected_anchor_price": anchor,
        "placement": placement,
    }


def test_bearish_stop_is_buffered_above_structural_anchor() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution=_entry("BEARISH"),
        trader_stop_selection=_selection(104.0, placement="ABOVE_RANGE_HIGH"),
        tick_size=0.25,
        buffer_ticks=2,
    )

    assert result.status == ExecutableStopStatus.READY
    assert result.executable_stop_price == 104.5
    assert result.buffer_price == 0.5
    assert result.stop_distance_ticks == 18
    assert result.to_dict()["risk_sizing_ready"] is True
    assert result.to_dict()["order_authorized"] is False


def test_bullish_stop_is_buffered_below_structural_anchor() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution=_entry("BULLISH"),
        trader_stop_selection=_selection(96.0, placement="BELOW_RANGE_LOW"),
        tick_size=0.25,
        buffer_ticks=2,
    )

    assert result.status == ExecutableStopStatus.READY
    assert result.executable_stop_price == 95.5
    assert result.stop_distance_ticks == 18


def test_off_grid_anchor_is_snapped_outward_not_toward_entry() -> None:
    bearish = ExecutableStopEngine().calculate(
        entry_execution=_entry("BEARISH"),
        trader_stop_selection=_selection(104.10),
        tick_size=0.25,
        buffer_ticks=1,
    )
    bullish = ExecutableStopEngine().calculate(
        entry_execution=_entry("BULLISH"),
        trader_stop_selection=_selection(95.90),
        tick_size=0.25,
        buffer_ticks=1,
    )

    assert bearish.executable_stop_price == 104.5
    assert bullish.executable_stop_price == 95.5


def test_missing_buffer_policy_fails_closed() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution=_entry("BEARISH"),
        trader_stop_selection=_selection(104.0),
        tick_size=0.25,
        buffer_ticks=None,
    )

    assert result.status == ExecutableStopStatus.WAIT_FOR_BUFFER_POLICY
    assert result.executable_stop_price is None
    assert result.to_dict()["risk_sizing_ready"] is False


def test_zero_tick_buffer_is_rejected() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution=_entry("BEARISH"),
        trader_stop_selection=_selection(104.0),
        tick_size=0.25,
        buffer_ticks=0,
    )

    assert result.status == ExecutableStopStatus.INVALID_BUFFER_POLICY
    assert result.executable_stop_price is None


def test_missing_trader_stop_selection_waits() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution=_entry("BEARISH"),
        trader_stop_selection=None,
        tick_size=0.25,
        buffer_ticks=1,
    )

    assert result.status == ExecutableStopStatus.WAIT_FOR_SELECTED_STRUCTURAL_STOP


def test_wrong_side_anchor_is_rejected() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution=_entry("BEARISH"),
        trader_stop_selection=_selection(99.0),
        tick_size=0.25,
        buffer_ticks=1,
    )

    assert result.status == ExecutableStopStatus.INVALID_DIRECTIONAL_GEOMETRY
    assert result.executable_stop_price is None


def test_entry_must_be_triggered_before_executable_stop_can_exist() -> None:
    result = ExecutableStopEngine().calculate(
        entry_execution={
            "status": "WAIT_FOR_RETRACE",
            "direction": "BEARISH",
            "exact_entry_price": None,
        },
        trader_stop_selection=_selection(104.0),
        tick_size=0.25,
        buffer_ticks=1,
    )

    assert result.status == ExecutableStopStatus.WAIT_FOR_ENTRY
