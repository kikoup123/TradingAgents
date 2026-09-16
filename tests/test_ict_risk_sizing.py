from __future__ import annotations

import pytest

from tradingagents.ict.risk_sizing import (
    AccountRiskPolicy,
    InstrumentRiskSpec,
    RiskSizingEngine,
    RiskSizingStatus,
)


def spec() -> InstrumentRiskSpec:
    return InstrumentRiskSpec(
        symbol="NQ",
        tick_size=0.25,
        tick_value_per_volume_unit=5.0,
        volume_step=1.0,
        min_volume=1.0,
        max_volume=100.0,
        volume_unit="contracts",
    )


def test_volume_is_derived_from_stop_range_and_risk_budget() -> None:
    result = RiskSizingEngine().calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.01),
    ).to_dict()

    # 10 points / 0.25 = 40 ticks; 40 * $5 = $200 risk/contract.
    # $1,000 budget / $200 = 5 contracts.
    assert result["status"] == RiskSizingStatus.READY.value
    assert result["stop_distance_ticks"] == 40
    assert result["risk_cash_budget"] == 1000.0
    assert result["risk_cash_per_volume_unit"] == 200.0
    assert result["final_volume"] == 5.0
    assert result["projected_cash_risk"] == 1000.0
    assert result["manual_volume_allowed"] is False


def test_wider_stop_automatically_reduces_volume() -> None:
    engine = RiskSizingEngine()
    policy = AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.01)

    tight = engine.calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=policy,
    )
    wide = engine.calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20020.0,
        instrument=spec(),
        policy=policy,
    )

    assert tight.final_volume == 5.0
    assert wide.final_volume == 2.0
    assert wide.projected_cash_risk == 800.0


def test_fractional_volume_is_rounded_down_not_up() -> None:
    instrument = InstrumentRiskSpec(
        symbol="XAUUSD",
        tick_size=0.01,
        tick_value_per_volume_unit=1.0,
        volume_step=0.01,
        min_volume=0.01,
        max_volume=100.0,
        volume_unit="lots",
        pip_size=0.1,
    )
    result = RiskSizingEngine().calculate(
        direction="BULLISH",
        entry_price=2500.0,
        stop_price=2493.0,
        instrument=instrument,
        policy=AccountRiskPolicy(account_equity=10000.0, risk_fraction=0.01),
    )

    assert result.status == RiskSizingStatus.READY
    assert result.stop_distance_ticks == 700
    assert result.stop_distance_pips == pytest.approx(70.0)
    assert result.raw_volume == pytest.approx(100.0 / 700.0)
    assert result.final_volume == pytest.approx(0.14)
    assert result.projected_cash_risk == pytest.approx(98.0)


def test_safe_volume_below_broker_minimum_does_not_round_up() -> None:
    result = RiskSizingEngine().calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20100.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=1000.0, risk_fraction=0.01),
    )

    assert result.status == RiskSizingStatus.BELOW_BROKER_MINIMUM
    assert result.final_volume is None
    assert "DO_NOT_ROUND_UP_AND_EXCEED_RISK_BUDGET" in result.reason_codes


def test_missing_executable_stop_waits_instead_of_sizing_from_anchor() -> None:
    result = RiskSizingEngine().calculate(
        direction="BULLISH",
        entry_price=20000.0,
        stop_price=None,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.01),
    )

    assert result.status == RiskSizingStatus.WAIT_FOR_EXECUTABLE_STOP
    assert result.final_volume is None


def test_invalid_stop_side_is_rejected() -> None:
    result = RiskSizingEngine().calculate(
        direction="BULLISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.01),
    )

    assert result.status == RiskSizingStatus.INVALID_DIRECTIONAL_GEOMETRY
    assert result.final_volume is None


def test_cash_cap_can_reduce_percentage_risk_budget() -> None:
    policy = AccountRiskPolicy(
        account_equity=100000.0,
        risk_fraction=0.02,
        max_risk_cash=750.0,
    )
    assert policy.risk_cash_budget == 750.0
