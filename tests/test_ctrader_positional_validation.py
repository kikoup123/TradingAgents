from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tradingagents.dataflows.ctrader_positional_validation as validation


def _bar(time: datetime, price: float) -> dict:
    return {
        "time": time.isoformat(),
        "open": price,
        "high": price + 2.0,
        "low": price - 2.0,
        "close": price + 0.5,
    }


def _htf_bars() -> list[dict]:
    start = datetime(2026, 9, 19, 8, tzinfo=timezone.utc)
    return [
        _bar(start + timedelta(hours=index), 100.0 - index)
        for index in range(5)
    ]


def _ltf_bars() -> list[dict]:
    start = datetime(2026, 9, 19, 6, tzinfo=timezone.utc)
    return [
        _bar(start + timedelta(minutes=5 * index), 100.0)
        for index in range(90)
    ]


def _wait_result(direction: str, status: str) -> dict:
    return {
        "symbol": "NASDAQ",
        "direction": direction,
        "status": status,
        "entry_model_confirmed": False,
        "fallback_to_unicorn": status != "WAIT_HTF_FRACTAL",
        "execution_allowed": False,
    }


def test_history_scan_records_positional_and_unicorn_fallback_without_future_leak(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_evaluate(htf_bars, ltf_bars, *, direction, **kwargs):
        entry_time = htf_bars[-1]["time"]
        last_ltf = ltf_bars[-1]["time"] if ltf_bars else None
        calls.append((entry_time, direction, last_ltf))

        if entry_time == "2026-09-19T10:00:00+00:00":
            if direction == "bearish":
                return _wait_result(direction, "WAIT_LTF_CSD")
            return _wait_result(direction, "WAIT_HTF_FRACTAL")

        if entry_time == "2026-09-19T11:00:00+00:00":
            if direction == "bearish":
                return {
                    "symbol": "NASDAQ",
                    "direction": "bearish",
                    "status": "POSITIONAL_TARGET_HIT",
                    "entry_model_confirmed": True,
                    "fallback_to_unicorn": False,
                    "fractal_stage": "C2",
                    "entry_candle_label": "C3_OPEN",
                    "fractal_context": {
                        "c1": {"time": "2026-09-19T09:00:00+00:00"},
                        "c2": {"time": "2026-09-19T10:00:00+00:00"},
                    },
                    "next_htf_candle_open": {
                        "time": entry_time,
                        "price": 97.0,
                    },
                    "qualifying_htf_candle": {
                        "time": "2026-09-19T10:00:00+00:00",
                        "directional_wick_equilibrium": 104.0,
                        "tspot": {
                            "top": 102.0,
                            "bottom": 98.0,
                        },
                    },
                    "selected_protected_swing": {
                        "protected_swing": 103.0,
                        "source": "POST_CSD_CONTINUATION_PROTECTED_SWING",
                        "eq_covers": False,
                        "tspot_covers": True,
                    },
                    "stop_reference": 103.0,
                    "target": {
                        "price": 80.0,
                        "source": "TV_FRACTAL_STRUCTURE_STD",
                        "zero_reference": 100.0,
                        "one_reference": 110.0,
                    },
                    "risk_reward": 17.0 / 6.0,
                    "position_outcome": {
                        "status": "TARGET_HIT",
                        "time": "2026-09-19T11:35:00+00:00",
                    },
                    "execution_allowed": False,
                }
            return _wait_result(direction, "WAIT_HTF_FRACTAL")

        return _wait_result(direction, "WAIT_HTF_FRACTAL")

    monkeypatch.setattr(
        validation,
        "evaluate_positional_from_bars",
        fake_evaluate,
    )

    result = validation.scan_positional_history_from_bars(
        _htf_bars(),
        _ltf_bars(),
        htf_timeframe="H1",
        symbol="NASDAQ",
        outcome_horizon_htf_bars=1,
    )

    assert len(result["records"]) == 2
    assert result["records"][0]["route"] == "UNICORN_FALLBACK_REQUIRED"
    assert result["records"][1]["route"] == "POSITIONAL"
    assert result["records"][1]["outcome"] == "TARGET_HIT"

    summary = result["summary"]
    assert summary["fractal_candidates"] == 2
    assert summary["positional_signals"] == 1
    assert summary["unicorn_fallback_required"] == 1
    assert summary["observed_outcomes"] == {"TARGET_HIT": 1}
    assert summary["target_hit_rate_on_completed_signals"] == 1.0
    assert summary["unique_positional_sequences"] == 1
    assert summary["secondary_positional_signals"] == 0
    assert summary["primary_sequence_target_hit_rate"] == 1.0
    assert result["records"][1]["sequence_entry_role"] == "PRIMARY"
    assert result["execution_allowed"] is False

    eleven_calls = [
        last_ltf
        for entry_time, _, last_ltf in calls
        if entry_time == "2026-09-19T11:00:00+00:00"
    ]
    assert eleven_calls
    assert all(
        datetime.fromisoformat(last_ltf)
        < datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        for last_ltf in eleven_calls
        if last_ltf is not None
    )


def test_empty_history_returns_safe_summary() -> None:
    result = validation.scan_positional_history_from_bars(
        _htf_bars()[:2],
        _ltf_bars(),
        htf_timeframe="H1",
        symbol="NASDAQ",
    )

    assert result["records"] == []
    assert result["summary"]["fractal_candidates"] == 0
    assert result["summary"]["positional_signals"] == 0
    assert result["execution_allowed"] is False


def test_validate_history_fetches_mapped_h1_m5_pair(monkeypatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_fetch(symbol: str, timeframe: str, count: int) -> list[dict]:
        calls.append((symbol, timeframe, count))
        return _htf_bars() if timeframe == "H1" else _ltf_bars()

    monkeypatch.setattr(validation, "_fetch", fake_fetch)
    monkeypatch.setattr(
        validation,
        "evaluate_positional_from_bars",
        lambda *args, direction, **kwargs: _wait_result(
            direction,
            "WAIT_HTF_FRACTAL",
        ),
    )

    result = validation.validate_positional_history(
        symbol="NASDAQ",
        htf_timeframe="H1",
        htf_count=5,
        ltf_count=90,
    )

    assert calls == [
        ("NASDAQ", "H1", 5),
        ("NASDAQ", "M5", 90),
    ]
    assert result["htf_timeframe"] == "H1"
    assert result["ltf_timeframe"] == "M5"
    assert result["execution_allowed"] is False


def test_sequence_roles_prevent_c3_continuation_from_counting_as_new_primary() -> None:
    records = [
        {
            "route": "POSITIONAL",
            "sequence_id": "bullish|c1|c2",
            "entry_time": "2026-09-19T11:00:00+00:00",
            "outcome": "TARGET_HIT",
            "risk_reward": 2.0,
            "direction": "bullish",
            "fractal_stage": "C2",
            "status": "POSITIONAL_TARGET_HIT",
        },
        {
            "route": "POSITIONAL",
            "sequence_id": "bullish|c1|c2",
            "entry_time": "2026-09-19T12:00:00+00:00",
            "outcome": "TARGET_HIT",
            "risk_reward": 3.0,
            "direction": "bullish",
            "fractal_stage": "C3",
            "status": "POSITIONAL_TARGET_HIT",
        },
    ]

    validation._annotate_sequence_roles(records)
    summary = validation._summarize(records)

    assert records[0]["sequence_entry_role"] == "PRIMARY"
    assert records[1]["sequence_entry_role"] == "SECONDARY_CONTINUATION"
    assert summary["positional_signals"] == 2
    assert summary["unique_positional_sequences"] == 1
    assert summary["secondary_positional_signals"] == 1
    assert summary["primary_sequence_outcomes"] == {"TARGET_HIT": 1}
    assert summary["completed_primary_sequence_outcomes"] == 1
    assert summary["primary_sequence_target_hit_rate"] == 1.0


def test_days_validation_fetches_paginated_h1_m5_with_warmup(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_paginated_bars(**kwargs):
        calls.append(kwargs)
        bars = _htf_bars() if kwargs["timeframe"] == "H1" else _ltf_bars()
        return {
            "symbol": kwargs["symbol"],
            "timeframe": kwargs["timeframe"],
            "requested_start": str(kwargs["start_time"]),
            "requested_end": str(kwargs["end_time"]),
            "page_size": kwargs["page_size"],
            "pages_requested": 2,
            "bars": bars,
            "count": len(bars),
            "first": bars[0]["time"],
            "last": bars[-1]["time"],
            "execution_allowed": False,
        }

    monkeypatch.setattr(
        validation,
        "fetch_paginated_bars",
        fake_paginated_bars,
    )
    monkeypatch.setattr(
        validation,
        "evaluate_positional_from_bars",
        lambda *args, direction, **kwargs: _wait_result(
            direction,
            "WAIT_HTF_FRACTAL",
        ),
    )

    result = validation.validate_positional_history_days(
        symbol="NASDAQ",
        htf_timeframe="H1",
        days=2,
        end_time="2026-09-19T12:00:00+00:00",
        warmup_days=1,
        page_size=500,
        outcome_horizon_htf_bars=4,
    )

    assert [(call["timeframe"], call["page_size"]) for call in calls] == [
        ("H1", 500),
        ("M5", 500),
    ]
    assert result["historical_fetch"]["days"] == 2
    assert result["historical_fetch"]["warmup_days"] == 1
    assert result["historical_fetch"]["htf"]["pages_requested"] == 2
    assert result["historical_fetch"]["ltf"]["pages_requested"] == 2
    assert result["execution_allowed"] is False


def test_entry_window_filters_warmup_candles_without_removing_context(
    monkeypatch,
) -> None:
    seen_entry_times: list[str] = []

    def fake_evaluate(htf_bars, ltf_bars, *, direction, **kwargs):
        seen_entry_times.append(htf_bars[-1]["time"])
        return _wait_result(direction, "WAIT_HTF_FRACTAL")

    monkeypatch.setattr(
        validation,
        "evaluate_positional_from_bars",
        fake_evaluate,
    )

    validation.scan_positional_history_from_bars(
        _htf_bars(),
        _ltf_bars(),
        htf_timeframe="H1",
        symbol="NASDAQ",
        entry_start_time="2026-09-19T11:00:00+00:00",
        entry_end_time="2026-09-19T12:00:00+00:00",
    )

    assert set(seen_entry_times) == {
        "2026-09-19T11:00:00+00:00",
        "2026-09-19T12:00:00+00:00",
    }
