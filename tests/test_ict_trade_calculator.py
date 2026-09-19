from __future__ import annotations

import math

from tradingagents.ict.risk_sizing import InstrumentRiskSpec
from tradingagents.ict.trade_calculator import (
    TradeCalculationStatus,
    TradeCalculatorEngine,
)


def _entry(direction: str = "BEARISH", price: float = 100.0) -> dict:
    return {
        "status": "ENTRY_TRIGGERED",
        "direction": direction,
        "exact_entry_price": price,
    }


def _stop(price: float = 104.5, tick_size: float = 0.25) -> dict:
    return {
        "status": "READY",
        "executable_stop_price": price,
        "tick_size": tick_size,
    }


def _instrument(*, volume_step: float = 1.0, min_volume: float = 1.0) -> InstrumentRiskSpec:
    return InstrumentRiskSpec(
        symbol="NQ",
        tick_size=0.25,
        tick_value_per_volume_unit=5.0,
        volume_step=volume_step,
        min_volume=min_volume,
        max_volume=100.0,
        volume_unit="contracts",
    )


def _selection_full(*, risk_fraction: float = 0.03, target: float = 80.0) -> dict:
    return {
        "valid": True,
        "selected_risk_fraction": risk_fraction,
        "target_management": {
            "selected_exit_mode": "FULL_AT_SD_2",
            "selected_sd_target": {"label": "-2", "price": target},
            "hold_for_htf_liquidity": False,
            "partial_trigger": None,
            "partial_fraction": 0.0,
            "runner_fraction": 0.0,
            "runner_target": None,
        },
    }


def _selection_hold(*, risk_fraction: float = 0.03) -> dict:
    return {
        "valid": True,
        "selected_risk_fraction": risk_fraction,
        "target_management": {
            "selected_exit_mode": "HOLD_HTF_LIQUIDITY",
            "selected_sd_target": {"label": "-2.5", "price": 75.0},
            "hold_for_htf_liquidity": True,
            "partial_trigger": {
                "trigger_label": "-2.5",
                "trigger_price": 75.0,
                "close_fraction": 0.60,
                "runner_fraction": 0.40,
            },
            "partial_fraction": 0.60,
            "runner_fraction": 0.40,
            "runner_target": {
                "price": 70.0,
                "side": "SELL_SIDE",
                "liquidity_class": "EXTERNAL",
            },
        },
    }


def test_full_exit_trade_calculates_volume_and_rr() -> None:
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(),
        executable_stop=_stop(),
        trader_selection=_selection_full(),
        instrument=_instrument(),
        account_equity=10_000.0,
    )

    assert result.status == TradeCalculationStatus.READY
    assert result.position_volume == 3.0
    assert result.projected_cash_risk == 270.0
    assert math.isclose(result.projected_equity_risk_fraction or 0.0, 0.027)
    assert result.stop_distance_ticks == 18
    assert result.selected_target_price == 80.0
    assert math.isclose(result.risk_reward_ratio or 0.0, 20.0 / 4.5)
    assert result.to_dict()["order_authorized"] is False


def test_hold_mode_calculates_exact_three_two_contract_split() -> None:
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(),
        executable_stop=_stop(),
        trader_selection=_selection_hold(),
        instrument=_instrument(),
        account_equity=15_000.0,
    )

    assert result.status == TradeCalculationStatus.READY
    assert result.position_volume == 5.0
    assert result.partial_fraction == 0.60
    assert result.partial_volume == 3.0
    assert result.runner_fraction == 0.40
    assert result.runner_volume == 2.0
    assert result.partial_trigger_price == 75.0
    assert result.runner_target_price == 70.0
    assert math.isclose(result.runner_risk_reward_ratio or 0.0, 30.0 / 4.5)


def test_hold_mode_fails_closed_when_exact_sixty_forty_split_is_not_on_broker_grid() -> None:
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(),
        executable_stop=_stop(),
        trader_selection=_selection_hold(),
        instrument=_instrument(),
        account_equity=10_000.0,
    )

    assert result.position_volume == 3.0
    assert result.status == TradeCalculationStatus.HOLD_SPLIT_NOT_BROKER_EXECUTABLE
    assert result.partial_volume is None
    assert result.runner_volume is None


def test_target_on_wrong_side_of_entry_is_rejected() -> None:
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(),
        executable_stop=_stop(),
        trader_selection=_selection_full(target=105.0),
        instrument=_instrument(),
        account_equity=10_000.0,
    )

    assert result.status == TradeCalculationStatus.INVALID_TARGET_GEOMETRY
    assert result.risk_reward_ratio is None


def test_missing_instrument_spec_waits() -> None:
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(),
        executable_stop=_stop(),
        trader_selection=_selection_full(),
        instrument=None,
        account_equity=10_000.0,
    )

    assert result.status == TradeCalculationStatus.WAIT_FOR_INSTRUMENT_RISK_SPEC


def test_phase14_and_risk_spec_tick_sizes_must_match() -> None:
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(),
        executable_stop=_stop(tick_size=0.5),
        trader_selection=_selection_full(),
        instrument=_instrument(),
        account_equity=10_000.0,
    )

    assert result.status == TradeCalculationStatus.INSTRUMENT_STOP_TICK_MISMATCH


def test_bullish_target_geometry_is_mirrored() -> None:
    selection = _selection_full(target=120.0)
    result = TradeCalculatorEngine().calculate(
        entry_execution=_entry(direction="BULLISH"),
        executable_stop=_stop(price=95.5),
        trader_selection=selection,
        instrument=_instrument(),
        account_equity=10_000.0,
    )

    assert result.status == TradeCalculationStatus.READY
    assert math.isclose(result.risk_reward_ratio or 0.0, 20.0 / 4.5)
