from __future__ import annotations

from copy import deepcopy

from tradingagents.ict.order_validator import (
    HardPreBrokerOrderValidator,
    PreBrokerValidationStatus,
)
from tradingagents.ict.risk_sizing import InstrumentRiskSpec


def _instrument() -> InstrumentRiskSpec:
    return InstrumentRiskSpec(
        symbol="TEST",
        tick_size=1.0,
        tick_value_per_volume_unit=10.0,
        volume_step=1.0,
        min_volume=1.0,
        max_volume=100.0,
        volume_unit="contracts",
        pip_size=1.0,
    )


def _package() -> dict:
    return {
        "entry": {
            "status": "ENTRY_TRIGGERED",
            "direction": "BEARISH",
            "exact_entry_price": 100.0,
        },
        "stop": {
            "status": "READY",
            "direction": "BEARISH",
            "entry_price": 100.0,
            "selected_stop_source": "IOF_RANGE",
            "structural_anchor_price": 103.0,
            "executable_stop_price": 104.0,
        },
        "selection": {
            "valid": True,
            "selected_source": "IOF_RANGE",
            "selected_anchor_price": 103.0,
            "selected_risk_fraction": 0.03,
            "selected_exit_mode": "FULL_AT_SD_2",
            "target_management": {
                "selected_exit_mode": "FULL_AT_SD_2",
                "selected_sd_target": {"label": "-2", "price": 90.0},
                "hold_for_htf_liquidity": False,
            },
        },
        "calculation": {
            "status": "READY",
            "direction": "BEARISH",
            "symbol": "TEST",
            "entry_price": 100.0,
            "executable_stop_price": 104.0,
            "selected_risk_fraction": 0.03,
            "selected_exit_mode": "FULL_AT_SD_2",
            "position_volume": 7.0,
            "projected_cash_risk": 280.0,
            "projected_equity_risk_fraction": 0.028,
            "selected_target_price": 90.0,
            "selected_target_label": "-2",
            "risk_reward_ratio": 2.5,
            "partial_trigger_price": None,
            "partial_fraction": None,
            "partial_volume": None,
            "runner_fraction": None,
            "runner_volume": None,
            "runner_target_price": None,
            "risk_sizing": {
                "status": "READY",
                "symbol": "TEST",
                "final_volume": 7.0,
            },
        },
        "management": {
            "status": "WAIT_FOR_IOF_EXIT",
            "direction": "BEARISH",
            "entry_price": 100.0,
            "original_stop_price": 104.0,
            "current_stop_price": 104.0,
            "break_even_price": None,
            "original_projected_cash_risk": 280.0,
            "original_projected_equity_risk_fraction": 0.028,
            "original_risk_reward_ratio": 2.5,
        },
    }


def _validate(package: dict, *, instrument: InstrumentRiskSpec | None = None):
    return HardPreBrokerOrderValidator().validate(
        entry_execution=package["entry"],
        executable_stop=package["stop"],
        trade_calculation=package["calculation"],
        break_even_management=package["management"],
        trader_selection=package["selection"],
        instrument=instrument or _instrument(),
        account_equity=10_000.0,
    )


def test_complete_pre_break_even_package_is_authorized() -> None:
    result = _validate(_package())

    assert result.status == PreBrokerValidationStatus.AUTHORIZED
    assert result.order_authorized is True
    assert result.current_stop_price == 104.0


def test_triggered_break_even_requires_current_stop_exactly_at_entry() -> None:
    package = _package()
    package["management"].update(
        {
            "status": "BREAK_EVEN_TRIGGERED",
            "current_stop_price": 100.0,
            "break_even_price": 100.0,
        }
    )

    result = _validate(package)

    assert result.status == PreBrokerValidationStatus.AUTHORIZED
    assert result.current_stop_price == 100.0
    assert result.original_stop_price == 104.0


def test_triggered_break_even_with_cost_compensated_stop_fails_closed() -> None:
    package = _package()
    package["management"].update(
        {
            "status": "BREAK_EVEN_TRIGGERED",
            "current_stop_price": 99.0,
            "break_even_price": 99.0,
        }
    )

    result = _validate(package)

    assert result.status == PreBrokerValidationStatus.BREAK_EVEN_POLICY_VIOLATION
    assert result.order_authorized is False


def test_risk_above_selected_tier_fails_closed() -> None:
    package = _package()
    package["calculation"]["projected_cash_risk"] = 400.0
    package["calculation"]["projected_equity_risk_fraction"] = 0.04
    package["management"]["original_projected_cash_risk"] = 400.0
    package["management"]["original_projected_equity_risk_fraction"] = 0.04

    result = _validate(package)

    assert result.status == PreBrokerValidationStatus.RISK_POLICY_VIOLATION
    assert result.order_authorized is False


def test_symbol_mismatch_fails_closed() -> None:
    package = _package()
    instrument = InstrumentRiskSpec(
        symbol="OTHER",
        tick_size=1.0,
        tick_value_per_volume_unit=10.0,
        volume_step=1.0,
        min_volume=1.0,
        max_volume=100.0,
        volume_unit="contracts",
    )

    result = _validate(package, instrument=instrument)

    assert result.status == PreBrokerValidationStatus.SYMBOL_MISMATCH
    assert result.order_authorized is False


def test_tampered_llm_volume_fails_closed() -> None:
    package = _package()
    package["calculation"]["position_volume"] = 8.0

    result = _validate(package)

    assert result.status == PreBrokerValidationStatus.VOLUME_POLICY_VIOLATION
    assert result.order_authorized is False


def test_hold_mode_requires_exact_broker_grid_60_40_contract() -> None:
    package = _package()
    package["selection"].update(
        {
            "selected_exit_mode": "HOLD_HTF_LIQUIDITY",
            "target_management": {
                "selected_exit_mode": "HOLD_HTF_LIQUIDITY",
                "selected_sd_target": {"label": "-2.5", "price": 88.0},
                "hold_for_htf_liquidity": True,
            },
        }
    )
    package["calculation"].update(
        {
            "selected_exit_mode": "HOLD_HTF_LIQUIDITY",
            "position_volume": 10.0,
            "projected_cash_risk": 300.0,
            "projected_equity_risk_fraction": 0.03,
            "selected_target_price": 88.0,
            "selected_target_label": "-2.5",
            "partial_trigger_price": 88.0,
            "partial_fraction": 0.60,
            "partial_volume": 6.0,
            "runner_fraction": 0.40,
            "runner_volume": 4.0,
            "runner_target_price": 80.0,
            "risk_sizing": {
                "status": "READY",
                "symbol": "TEST",
                "final_volume": 10.0,
            },
        }
    )
    package["management"].update(
        {
            "original_projected_cash_risk": 300.0,
            "original_projected_equity_risk_fraction": 0.03,
        }
    )

    result = _validate(package)

    assert result.status == PreBrokerValidationStatus.AUTHORIZED
    assert result.order_authorized is True

    tampered = deepcopy(package)
    tampered["calculation"]["partial_volume"] = 5.0
    tampered["calculation"]["runner_volume"] = 5.0
    bad = _validate(tampered)
    assert bad.status == PreBrokerValidationStatus.TARGET_CONTRACT_VIOLATION
    assert bad.order_authorized is False
