from __future__ import annotations

import pandas as pd

from tradingagents.ict.break_even import BreakEvenStatus, StructuralBreakEvenEngine


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-09-15 14:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


def _entry(direction: str, *, price: float, low: float, high: float, position: int = 2) -> dict:
    return {
        "status": "ENTRY_TRIGGERED",
        "direction": direction,
        "exact_entry_price": price,
        "entry_zone": {"low": low, "high": high, "direction": direction},
        "entry_event": {
            "price": price,
            "position": position,
            "time": "2026-09-15T14:10:00+00:00",
            "zone_low": low,
            "zone_high": high,
        },
    }


def _calculation(direction: str, *, entry: float, stop: float, rr: float = 4.0) -> dict:
    return {
        "status": "READY",
        "direction": direction,
        "entry_price": entry,
        "executable_stop_price": stop,
        "projected_cash_risk": 300.0,
        "projected_equity_risk_fraction": 0.03,
        "risk_reward_ratio": rr,
    }


def _stop(price: float) -> dict:
    return {"status": "READY", "executable_stop_price": price}


def test_bearish_break_even_requires_exit_new_fractal_and_bos() -> None:
    bars = _bars(
        [
            (103.0, 104.0, 102.0, 103.0),
            (102.0, 103.0, 101.0, 102.0),
            (100.0, 101.0, 98.0, 99.0),
            (98.5, 99.0, 97.5, 98.0),
            (97.5, 98.0, 96.5, 97.0),
            (96.5, 97.0, 95.0, 96.0),
            (96.0, 97.0, 95.8, 96.5),
            (96.5, 98.0, 96.0, 97.5),
            (97.5, 98.0, 94.0, 94.5),
        ]
    )
    result = StructuralBreakEvenEngine().analyze(
        entry_execution=_entry("BEARISH", price=100.0, low=100.0, high=104.0),
        trade_calculation=_calculation("BEARISH", entry=100.0, stop=105.0),
        executable_stop=_stop(105.0),
        execution_bars=bars,
    )

    assert result.status == BreakEvenStatus.BREAK_EVEN_TRIGGERED
    assert result.range_exit_position == 2
    assert result.fractal_position == 5
    assert result.fractal_level == 95.0
    assert result.fractal_confirmation_position == 7
    assert result.bos_position == 8
    assert result.bos_level == 95.0
    assert result.break_even_price == 100.0
    assert result.current_stop_price == 100.0
    assert result.original_stop_price == 105.0
    assert result.original_projected_cash_risk == 300.0
    assert result.original_projected_equity_risk_fraction == 0.03
    assert result.original_risk_reward_ratio == 4.0

    payload = result.to_dict()
    assert payload["break_even_price_basis"] == "EXACT_DETERMINISTIC_ENTRY_PRICE"
    assert payload["spread_compensation_applied"] is False
    assert payload["commission_compensation_applied"] is False
    assert payload["net_zero_after_costs_guaranteed"] is False
    assert payload["order_authorized"] is False


def test_bullish_break_even_is_exact_entry_after_mirrored_bos() -> None:
    bars = _bars(
        [
            (97.0, 98.0, 96.0, 97.0),
            (98.0, 99.0, 97.0, 98.0),
            (100.0, 102.0, 99.0, 101.0),
            (101.0, 102.5, 100.5, 102.0),
            (102.0, 103.5, 101.5, 103.0),
            (103.0, 105.0, 102.5, 104.0),
            (104.0, 104.8, 103.0, 103.5),
            (103.5, 104.5, 102.5, 103.0),
            (103.0, 106.0, 102.8, 105.5),
        ]
    )
    result = StructuralBreakEvenEngine().analyze(
        entry_execution=_entry("BULLISH", price=100.0, low=96.0, high=100.0),
        trade_calculation=_calculation("BULLISH", entry=100.0, stop=95.0),
        executable_stop=_stop(95.0),
        execution_bars=bars,
    )

    assert result.status == BreakEvenStatus.BREAK_EVEN_TRIGGERED
    assert result.fractal_level == 105.0
    assert result.bos_close == 105.5
    assert result.break_even_price == 100.0
    assert result.current_stop_price == 100.0
    assert result.original_stop_price == 95.0


def test_leaving_iof_range_alone_does_not_trigger_break_even() -> None:
    bars = _bars(
        [
            (103.0, 104.0, 102.0, 103.0),
            (102.0, 103.0, 101.0, 102.0),
            (100.0, 101.0, 98.0, 99.0),
            (99.0, 99.5, 97.0, 98.0),
            (98.0, 98.5, 96.0, 97.0),
            (97.0, 97.5, 95.0, 96.0),
            (96.0, 96.5, 94.0, 95.0),
            (95.0, 95.5, 93.0, 94.0),
        ]
    )
    result = StructuralBreakEvenEngine().analyze(
        entry_execution=_entry("BEARISH", price=100.0, low=100.0, high=104.0),
        trade_calculation=_calculation("BEARISH", entry=100.0, stop=105.0),
        executable_stop=_stop(105.0),
        execution_bars=bars,
    )

    assert result.status == BreakEvenStatus.WAIT_FOR_NEW_FRACTAL
    assert result.range_exit_position == 2
    assert result.current_stop_price == 105.0
    assert result.break_even_price is None


def test_confirmed_fractal_without_body_close_bos_keeps_original_stop() -> None:
    bars = _bars(
        [
            (103.0, 104.0, 102.0, 103.0),
            (102.0, 103.0, 101.0, 102.0),
            (100.0, 101.0, 98.0, 99.0),
            (98.5, 99.0, 97.5, 98.0),
            (97.5, 98.0, 96.5, 97.0),
            (96.5, 97.0, 95.0, 96.0),
            (96.0, 97.0, 95.8, 96.5),
            (96.5, 98.0, 96.0, 97.5),
            (97.5, 98.0, 94.0, 95.5),
        ]
    )
    result = StructuralBreakEvenEngine().analyze(
        entry_execution=_entry("BEARISH", price=100.0, low=100.0, high=104.0),
        trade_calculation=_calculation("BEARISH", entry=100.0, stop=105.0),
        executable_stop=_stop(105.0),
        execution_bars=bars,
    )

    assert result.status == BreakEvenStatus.WAIT_FOR_BOS
    assert result.fractal_level == 95.0
    assert result.current_stop_price == 105.0
    assert result.break_even_price is None


def test_break_even_waits_until_price_body_closes_outside_iof_range() -> None:
    bars = _bars(
        [
            (103.0, 104.0, 102.0, 103.0),
            (102.0, 103.0, 101.0, 102.0),
            (100.0, 103.0, 99.0, 100.5),
            (101.0, 103.0, 99.5, 100.2),
            (100.5, 102.0, 99.8, 100.1),
        ]
    )
    result = StructuralBreakEvenEngine().analyze(
        entry_execution=_entry("BEARISH", price=100.0, low=100.0, high=104.0),
        trade_calculation=_calculation("BEARISH", entry=100.0, stop=105.0),
        executable_stop=_stop(105.0),
        execution_bars=bars,
    )

    assert result.status == BreakEvenStatus.WAIT_FOR_IOF_EXIT
    assert result.current_stop_price == 105.0


def test_phase15_ready_trade_is_required_before_break_even_management() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0)] * 6)
    calculation = _calculation("BEARISH", entry=100.0, stop=105.0)
    calculation["status"] = "WAIT_FOR_EXECUTABLE_STOP"

    result = StructuralBreakEvenEngine().analyze(
        entry_execution=_entry("BEARISH", price=100.0, low=100.0, high=104.0),
        trade_calculation=calculation,
        executable_stop=_stop(105.0),
        execution_bars=bars,
    )

    assert result.status == BreakEvenStatus.WAIT_FOR_ACTIVE_TRADE
    assert result.break_even_price is None
