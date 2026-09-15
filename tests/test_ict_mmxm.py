from __future__ import annotations

import pandas as pd

from tradingagents.ict.mmxm import EntryState, MMXMEngine, MMXMStage, MMXMType
from tradingagents.ict.models import Direction


def frame(rows, *, start="2026-09-14 18:00", freq="5min"):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        dtype=float,
        index=pd.date_range(start, periods=len(rows), freq=freq, tz="UTC"),
    )


def failure_swing_bars():
    return frame(
        [
            (10, 11, 9, 10.5),
            (10.5, 11.2, 9.5, 10),
            (10, 11, 9.6, 10.6),
            (10.6, 13, 10.5, 12.5),
            (12.5, 15, 12, 14.5),
            (14.5, 16, 14, 15),
            (14.5, 14.8, 13, 13.5),
            (13.5, 15, 13.2, 14.5),
            (14.5, 14.7, 12, 12.5),
            (12.5, 13, 11, 11.5),
        ]
    )


def narrative_for_mmsm(*, continuation=False, matrix=(14.0, 16.0)):
    events = []
    if continuation:
        events.append({"position": 9, "event": "REDISTRIBUTION"})
    local = {
        "timeframe": "5m",
        "price_delivery": {
            "original_consolidation": {
                "low": 9.0,
                "high": 11.2,
                "start_position": 0,
                "end_position": 2,
                "status": "DISPLACEMENT_CONFIRMED",
                "departure_position": 3,
            },
            "events": events,
        },
        "fair_value": {
            "gaps": [
                {
                    "formation_position": 3,
                    "source_position": 3,
                    "direction": "BULLISH",
                }
            ]
        },
        "parent_matrices": [],
        "reversal": {
            "confirmed": True,
            "csd": {
                "direction": "BEARISH",
                "raid_position": 5,
                "confirmation_position": 8,
                "protected_extreme": 16.0,
            },
            "post_csd_iofc": {"confirmed": True, "expected_direction": "BEARISH"},
            "matrix": {
                "timeframe": "1H",
                "low": matrix[0],
                "high": matrix[1],
                "kind": "IOF_RANGE",
            },
        },
        "liquidity": {
            "external_low": 9.0,
            "external_high": 16.0,
            "active_draw": {
                "side": "SELL_SIDE",
                "liquidity_class": "EXTERNAL",
                "price": 9.0,
            },
            "buy_side": [],
            "sell_side": [],
        },
    }
    parent = {
        "timeframe": "1H",
        "liquidity": {
            "external_low": 8.0,
            "external_high": 18.0,
        },
    }
    return {"hierarchy": ["1H", "5m"], "timeframes": {"1H": parent, "5m": local}}


def test_failure_swing_at_premium_matrix_confirms_mmsm_smr():
    result = MMXMEngine(pivot_span=1).analyze(
        failure_swing_bars(),
        timeframe="5m",
        narrative=narrative_for_mmsm(),
    ).to_dict()

    assert result["model"] == MMXMType.MMSM.value
    assert result["approach_direction"] == Direction.BULLISH.value
    assert result["final_direction"] == Direction.BEARISH.value
    assert result["matrix_location"] == "PREMIUM"
    assert result["stage"] == MMXMStage.SMART_MONEY_REVERSAL_CONFIRMED.value
    assert result["smart_money_reversal"]["confirmed"] is True
    assert result["smart_money_reversal"]["signature"]["type"] == "FAILURE_SWING"


def test_first_post_reversal_redistribution_advances_mmsm_to_continuation():
    result = MMXMEngine(pivot_span=1).analyze(
        failure_swing_bars(),
        timeframe="5m",
        narrative=narrative_for_mmsm(continuation=True),
    ).to_dict()

    assert result["stage"] == MMXMStage.CONTINUATION_PHASE.value


def test_wrong_matrix_location_does_not_create_complete_mmsm():
    result = MMXMEngine(pivot_span=1).analyze(
        failure_swing_bars(),
        timeframe="5m",
        narrative=narrative_for_mmsm(matrix=(8.0, 10.0)),
    ).to_dict()

    assert result["model"] == MMXMType.UNRESOLVED.value
    assert result["stage"] == MMXMStage.NO_MODEL.value
    assert "MATRIX_NOT_IN_REQUIRED_PREMIUM_DISCOUNT_LOCATION" in result["reason_codes"]


def test_breaker_requires_failed_old_iof_range_and_retest_from_new_side():
    bars = frame(
        [
            (13, 14, 12.5, 13.5),
            (15.5, 16, 14, 14.5),
            (14.5, 16.5, 14.2, 16.2),
            (16.2, 16.4, 13.5, 13.8),
            (13.8, 15, 13, 13.5),
        ]
    )
    evidence = MMXMEngine(pivot_span=1)._breaker(
        bars,
        matrix={"low": 14.0, "high": 16.0},
        final_direction=Direction.BEARISH,
        start_position=1,
    )

    assert evidence is not None
    assert evidence["direction"] == "BEARISH"
    assert evidence["rule"] == "INVALIDATED_OPPOSING_IOF_RANGE_RETESTED_FROM_NEW_SIDE"


def test_entry_contract_never_authorizes_order_and_requires_parent_alignment():
    model = {
        "model": "MMSM",
        "stage": MMXMStage.SMART_MONEY_REVERSAL_CONFIRMED.value,
        "final_direction": "BEARISH",
        "parent_relationship": "ALIGNED_CHILD_MODEL",
    }
    gate = {"qualified": True}
    execution = {"smt_validated": True, "direction": "BEARISH"}
    ready = MMXMEngine.entry_contract(model, narrative_gate=gate, execution_gate=execution)
    assert ready["state"] == EntryState.REVERSAL_READY.value
    assert ready["order_authorized"] is False

    model["parent_relationship"] = "COUNTER_MODEL_WITHIN_PARENT"
    wait = MMXMEngine.entry_contract(model, narrative_gate=gate, execution_gate=execution)
    assert wait["state"] == EntryState.WAIT.value
    assert "HTF_CONTROL_OVERRIDES_COUNTER_MODEL_ENTRY" in wait["reason_codes"]
