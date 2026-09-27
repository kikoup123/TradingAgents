from __future__ import annotations

import pandas as pd

from tradingagents.ict.stop_selection import StopSelectionEngine, StopSource


def bars(close: float = 12.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [12.4, 12.3],
            "high": [12.8, 12.6],
            "low": [11.8, 11.7],
            "close": [12.2, close],
        },
        index=pd.date_range("2026-09-15 12:00", periods=2, freq="5min", tz="UTC"),
    )


def bearish_context() -> dict:
    return {
        "trade_plan": {
            "state": "READY_FOR_ENTRY_SELECTION",
            "direction": "BEARISH",
        },
        "validation_csd": {
            "direction": "BEARISH",
            "confirmation_position": 6,
            "protected_extreme": 16.0,
        },
        "bias_narrative": {
            "execution_timeframe": "5m",
            "timeframes": {
                "5m": {
                    "reversal": {
                        "csd": {
                            "direction": "BEARISH",
                            "confirmation_position": 6,
                            "protected_extreme": 16.0,
                        },
                        "post_csd_iofc": {
                            "confirmed": True,
                            "confirmation_range": {
                                "direction": "BEARISH",
                                "low": 12.4,
                                "high": 13.0,
                                "source_position": 7,
                                "confirmed_position": 8,
                            },
                        },
                    },
                    "order_flow": {
                        "active_support_ranges": [],
                        "active_resistance_ranges": [
                            {
                                "direction": "BEARISH",
                                "low": 14.0,
                                "high": 14.6,
                                "source_position": 4,
                                "confirmed_position": 5,
                            },
                            {
                                "direction": "BEARISH",
                                "low": 12.5,
                                "high": 13.2,
                                "source_position": 9,
                                "confirmed_position": 10,
                            },
                        ],
                    },
                }
            },
        },
    }


def bullish_context() -> dict:
    return {
        "trade_plan": {
            "state": "READY_FOR_ENTRY_SELECTION",
            "direction": "BULLISH",
        },
        "validation_csd": {
            "direction": "BULLISH",
            "confirmation_position": 6,
            "protected_extreme": 8.0,
        },
        "bias_narrative": {
            "execution_timeframe": "5m",
            "timeframes": {
                "5m": {
                    "reversal": {
                        "csd": {
                            "direction": "BULLISH",
                            "confirmation_position": 6,
                            "protected_extreme": 8.0,
                        },
                        "post_csd_iofc": {
                            "confirmed": True,
                            "confirmation_range": {
                                "direction": "BULLISH",
                                "low": 10.8,
                                "high": 11.4,
                                "source_position": 7,
                                "confirmed_position": 8,
                            },
                        },
                    },
                    "order_flow": {
                        "active_support_ranges": [
                            {
                                "direction": "BULLISH",
                                "low": 10.5,
                                "high": 11.2,
                                "source_position": 9,
                                "confirmed_position": 10,
                            }
                        ],
                        "active_resistance_ranges": [],
                    },
                }
            },
        },
    }


def test_bearish_stop_options_include_latest_iof_high_and_smt_protected_high() -> None:
    result = StopSelectionEngine().analyze(bearish_context(), bars()).to_dict()

    assert result["selection_required"] is True
    assert result["allowed_sources"] == [
        StopSource.IOF_RANGE.value,
        StopSource.SMT_PROTECTED.value,
    ]
    iof, smt = result["candidates"]
    assert iof["anchor_price"] == 13.2
    assert iof["placement"] == "ABOVE_RANGE_HIGH"
    assert iof["structural_source"] == "LATEST_VALID_DIRECTIONAL_IOF_RANGE_AFTER_CSD"
    assert smt["anchor_price"] == 16.0
    assert smt["placement"] == "ABOVE_PROTECTED_HIGH"
    assert smt["structural_source"] == "POST_SMT_CSD_PROTECTED_EXTREME"
    assert result["order_authorized"] is False


def test_bullish_stop_options_are_symmetric() -> None:
    result = StopSelectionEngine().analyze(bullish_context(), bars()).to_dict()

    iof, smt = result["candidates"]
    assert iof["anchor_price"] == 10.5
    assert iof["placement"] == "BELOW_RANGE_LOW"
    assert smt["anchor_price"] == 8.0
    assert smt["placement"] == "BELOW_PROTECTED_LOW"


def test_pre_csd_iof_range_is_not_used_as_last_orderflow_stop() -> None:
    context = bearish_context()
    context["bias_narrative"]["timeframes"]["5m"]["order_flow"][
        "active_resistance_ranges"
    ] = [
        {
            "direction": "BEARISH",
            "low": 14.0,
            "high": 14.6,
            "source_position": 4,
            "confirmed_position": 5,
        }
    ]

    result = StopSelectionEngine().analyze(context, bars()).to_dict()

    iof = next(item for item in result["candidates"] if item["source"] == "IOF_RANGE")
    assert iof["anchor_price"] == 13.0
    assert iof["source_position"] == 7


def test_stop_selection_validator_accepts_either_valid_candidate() -> None:
    stop_context = StopSelectionEngine().analyze(bearish_context(), bars()).to_dict()

    iof = StopSelectionEngine.validate_selection(
        stop_context,
        selected_source=StopSource.IOF_RANGE.value,
    )
    protected = StopSelectionEngine.validate_selection(
        stop_context,
        selected_source=StopSource.SMT_PROTECTED.value,
    )

    assert iof["valid"] is True
    assert iof["selected_anchor_price"] == 13.2
    assert protected["valid"] is True
    assert protected["selected_anchor_price"] == 16.0


def test_stop_selection_validator_rejects_invented_source() -> None:
    stop_context = StopSelectionEngine().analyze(bearish_context(), bars()).to_dict()

    result = StopSelectionEngine.validate_selection(
        stop_context,
        selected_source="ATR_STOP",
    )

    assert result["valid"] is False
    assert result["selected_source"] == StopSource.NONE.value
    assert result["selected_anchor_price"] is None
