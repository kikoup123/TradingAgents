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


def test_volume_is_derived_from_stop_range_and_three_percent_budget() -> None:
    result = RiskSizingEngine().calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.03),
    ).to_dict()

    # 10 points / 0.25 = 40 ticks; 40 * $5 = $200 risk/contract.
    # $3,000 budget / $200 = 15 contracts.
    assert result["status"] == RiskSizingStatus.READY.value
    assert result["stop_distance_ticks"] == 40
    assert result["selected_risk_fraction"] == 0.03
    assert result["hard_risk_ceiling_fraction"] == 0.10
    assert result["risk_cash_budget"] == 3000.0
    assert result["risk_cash_per_volume_unit"] == 200.0
    assert result["final_volume"] == 15.0
    assert result["projected_cash_risk"] == 3000.0
    assert result["manual_volume_allowed"] is False
    assert result["allowed_risk_fractions"] == [0.03, 0.05, 0.10]


def test_five_percent_risk_tier_is_supported() -> None:
    result = RiskSizingEngine().calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.05),
    )

    assert result.status == RiskSizingStatus.READY
    assert result.risk_cash_budget == 5000.0
    assert result.final_volume == 25.0
    assert result.projected_equity_risk_fraction == pytest.approx(0.05)


def test_ten_percent_is_supported_and_is_hard_ceiling() -> None:
    result = RiskSizingEngine().calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.10),
    )

    assert result.status == RiskSizingStatus.READY
    assert result.risk_cash_budget == 10000.0
    assert result.final_volume == 50.0
    assert result.projected_equity_risk_fraction == pytest.approx(0.10)


@pytest.mark.parametrize("invalid_fraction", [0.01, 0.02, 0.04, 0.06, 0.11, 0.50])
def test_unapproved_risk_fraction_is_rejected(invalid_fraction: float) -> None:
    with pytest.raises(ValueError, match="0.03, 0.05, or 0.10"):
        AccountRiskPolicy(account_equity=100000.0, risk_fraction=invalid_fraction)


def test_wider_stop_automatically_reduces_volume() -> None:
    engine = RiskSizingEngine()
    policy = AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.03)

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

    assert tight.final_volume == 15.0
    assert wide.final_volume == 7.0
    assert wide.projected_cash_risk == 2800.0


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
        policy=AccountRiskPolicy(account_equity=10000.0, risk_fraction=0.03),
    )

    assert result.status == RiskSizingStatus.READY
    assert result.stop_distance_ticks == 700
    assert result.stop_distance_pips == pytest.approx(70.0)
    assert result.raw_volume == pytest.approx(300.0 / 700.0)
    assert result.final_volume == pytest.approx(0.42)
    assert result.projected_cash_risk == pytest.approx(294.0)


def test_safe_volume_below_broker_minimum_does_not_round_up() -> None:
    result = RiskSizingEngine().calculate(
        direction="BEARISH",
        entry_price=20000.0,
        stop_price=20100.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=1000.0, risk_fraction=0.03),
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
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.03),
    )

    assert result.status == RiskSizingStatus.WAIT_FOR_EXECUTABLE_STOP
    assert result.final_volume is None


def test_invalid_stop_side_is_rejected() -> None:
    result = RiskSizingEngine().calculate(
        direction="BULLISH",
        entry_price=20000.0,
        stop_price=20010.0,
        instrument=spec(),
        policy=AccountRiskPolicy(account_equity=100000.0, risk_fraction=0.03),
    )

    assert result.status == RiskSizingStatus.INVALID_DIRECTIONAL_GEOMETRY
    assert result.final_volume is None


def test_cash_cap_can_reduce_percentage_risk_budget() -> None:
    policy = AccountRiskPolicy(
        account_equity=100000.0,
        risk_fraction=0.05,
        max_risk_cash=750.0,
    )
    assert policy.risk_cash_budget == 750.0
