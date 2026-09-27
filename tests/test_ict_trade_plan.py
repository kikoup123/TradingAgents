from __future__ import annotations

import pandas as pd

from tradingagents.ict.trade_plan import TradePlanEngine, TradePlanState


def bars(close: float = 12.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [12.5, 12.2],
            "high": [13.0, max(12.8, close + 0.2)],
            "low": [11.5, min(11.0, close - 0.2)],
            "close": [12.2, close],
        },
        index=pd.date_range("2026-09-15 12:00", periods=2, freq="5min", tz="UTC"),
    )


def bearish_context(*, entry_state="CONTINUATION_READY", terminal_reached=False, with_location=True):
    iofc_range = (
        {
            "low": 12.5,
            "high": 13.2,
            "source_position": 7,
            "confirmed_position": 8,
            "status": "ACTIVE",
        }
        if with_location
        else None
    )
    local = {
        "reversal": {
            "csd": {
                "direction": "BEARISH",
                "confirmation_position": 6,
                "protected_extreme": 16.0,
            },
            "post_csd_iofc": {
                "confirmed": with_location,
                "confirmation_range": iofc_range,
            },
        },
        "fair_value": {
            "structural_fvg_candidates": [
                {
                    "direction": "BEARISH",
                    "low": 12.7,
                    "high": 13.0,
                    "consequent_encroachment": 12.85,
                    "fair_valuation_point": 13.1,
                    "formation_position": 9,
                    "status": "OPEN",
                },
                {
                    "direction": "BULLISH",
                    "low": 11.0,
                    "high": 11.3,
                    "consequent_encroachment": 11.15,
                    "fair_valuation_point": None,
                    "formation_position": 10,
                    "status": "OPEN",
                },
            ]
        },
        "parent_relative_run": {
            "classification": "LRLR",
            "target_scope": "ERL",
        },
        "liquidity_run": {"classification": "LRLR", "target_scope": "ERL"},
        "narrative_draw": None,
    }
    model = {
        "model": "MMSM",
        "stage": "TERMINAL_REACHED" if terminal_reached else "CONTINUATION_PHASE",
        "final_direction": "BEARISH",
        "terminal": {
            "price": 9.0,
            "purpose": "MMXM_TERMINAL",
            "side": "SELL_SIDE",
            "liquidity_class": "EXTERNAL",
            "reached": terminal_reached,
        },
        "smart_money_reversal": {"signature": {"type": "FAILURE_SWING", "evidence": {}}},
    }
    return {
        "bias_narrative": {
            "execution_timeframe": "5m",
            "timeframes": {"5m": local},
        },
        "mmxm": {"execution_model": model},
        "entry_model": {"state": entry_state},
    }


def bullish_context():
    local = {
        "reversal": {
            "csd": {
                "direction": "BULLISH",
                "confirmation_position": 6,
                "protected_extreme": 14.0,
            },
            "post_csd_iofc": {
                "confirmed": True,
                "confirmation_range": {
                    "low": 16.2,
                    "high": 17.0,
                    "source_position": 7,
                    "confirmed_position": 8,
                    "status": "ACTIVE",
                },
            },
        },
        "fair_value": {
            "structural_fvg_candidates": [
                {
                    "direction": "BULLISH",
                    "low": 16.5,
                    "high": 17.2,
                    "consequent_encroachment": 16.85,
                    "fair_valuation_point": 16.3,
                    "formation_position": 9,
                    "status": "OPEN",
                }
            ]
        },
        "parent_relative_run": {"classification": "LRLR", "target_scope": "ERL"},
        "liquidity_run": {"classification": "LRLR", "target_scope": "ERL"},
        "narrative_draw": None,
    }
    return {
        "bias_narrative": {
            "execution_timeframe": "5m",
            "timeframes": {"5m": local},
        },
        "mmxm": {
            "execution_model": {
                "model": "MMBM",
                "stage": "CONTINUATION_PHASE",
                "final_direction": "BULLISH",
                "terminal": {
                    "price": 21.0,
                    "purpose": "MMXM_TERMINAL",
                    "side": "BUY_SIDE",
                    "liquidity_class": "EXTERNAL",
                    "reached": False,
                },
                "smart_money_reversal": {
                    "signature": {"type": "FAILURE_SWING", "evidence": {}}
                },
            }
        },
        "entry_model": {"state": "CONTINUATION_READY"},
    }


def test_trade_plan_exposes_stop_target_and_locations_without_authorizing_order():
    result = TradePlanEngine().analyze(bearish_context(), bars()).to_dict()

    assert result["state"] == TradePlanState.READY_FOR_ENTRY_SELECTION.value
    assert result["direction"] == "BEARISH"
    assert result["invalidation"]["price"] == 16.0
    assert result["invalidation"]["kind"] == "PROTECTED_HIGH"
    assert result["primary_target"]["price"] == 9.0
    assert result["primary_target"]["source"] == "MMXM_TERMINAL"
    assert [location["kind"] for location in result["execution_locations"]] == [
        "POST_CSD_IOFC_RANGE",
        "POST_CSD_STRUCTURAL_FVG",
    ]
    assert all(location["entry_signal"] is False for location in result["execution_locations"])
    assert result["risk_reward_status"] == "WAIT_FOR_ENTRY_PRICE"
    assert result["order_authorized"] is False


def test_bullish_trade_plan_mirrors_stop_target_geometry():
    result = TradePlanEngine().analyze(bullish_context(), bars(close=18.0)).to_dict()

    assert result["state"] == TradePlanState.READY_FOR_ENTRY_SELECTION.value
    assert result["direction"] == "BULLISH"
    assert result["invalidation"]["price"] == 14.0
    assert result["invalidation"]["kind"] == "PROTECTED_LOW"
    assert result["primary_target"]["price"] == 21.0
    assert result["order_authorized"] is False


def test_trade_plan_waits_if_mmxm_entry_gate_is_not_ready():
    result = TradePlanEngine().analyze(
        bearish_context(entry_state="WAIT"),
        bars(),
    ).to_dict()

    assert result["state"] == TradePlanState.NOT_READY.value
    assert "MMXM_ENTRY_GATE_NOT_READY" in result["reason_codes"]


def test_trade_plan_requires_post_confirmation_execution_location():
    context = bearish_context(with_location=False)
    context["bias_narrative"]["timeframes"]["5m"]["fair_value"][
        "structural_fvg_candidates"
    ] = []
    result = TradePlanEngine().analyze(context, bars()).to_dict()

    assert result["state"] == TradePlanState.NOT_READY.value
    assert result["execution_locations"] == []
    assert "WAIT_FOR_POST_CONFIRMATION_EXECUTION_LOCATION" in result["reason_codes"]


def test_trade_plan_marks_terminal_as_objective_reached():
    result = TradePlanEngine().analyze(
        bearish_context(terminal_reached=True),
        bars(),
    ).to_dict()

    assert result["state"] == TradePlanState.OBJECTIVE_REACHED.value
    assert result["order_authorized"] is False


def test_trade_plan_marks_protected_extreme_breach_invalidated():
    result = TradePlanEngine().analyze(
        bearish_context(),
        bars(close=16.5),
    ).to_dict()

    assert result["state"] == TradePlanState.INVALIDATED.value
    assert "STRUCTURAL_INVALIDATION_REACHED" in result["reason_codes"]


def test_pre_csd_fvg_is_not_promoted_to_execution_location():
    context = bearish_context(with_location=False)
    context["bias_narrative"]["timeframes"]["5m"]["fair_value"][
        "structural_fvg_candidates"
    ] = [
        {
            "direction": "BEARISH",
            "low": 12.7,
            "high": 13.0,
            "consequent_encroachment": 12.85,
            "fair_valuation_point": 13.1,
            "formation_position": 5,
            "status": "OPEN",
        }
    ]
    result = TradePlanEngine().analyze(context, bars()).to_dict()

    assert result["execution_locations"] == []
    assert result["state"] == TradePlanState.NOT_READY.value
