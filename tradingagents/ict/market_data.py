"""Shared closed-candle boundary for causal Londres context calculations."""

from __future__ import annotations

from math import isfinite

import pandas as pd

from .daily_profile import FIXED_UTC_MINUS_4
from .order_flow import _normalize_ohlc


def analysis_time(value: pd.Timestamp | str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize(FIXED_UTC_MINUS_4) if timestamp.tzinfo is None else timestamp


def closed_bars(bars: pd.DataFrame, as_of: pd.Timestamp | str | None = None) -> pd.DataFrame:
    """Index labels are availability/close times, never future candle open times.

    Providers must exclude unfinished candles before calling this boundary. We
    cannot reconstruct a partially formed HTF candle from its final OHLC values.
    Naive timestamps retain the existing Londres fixed-clock convention.
    """
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("closed bars require a DatetimeIndex")
    if bars.index.has_duplicates or bars.index.hasnans:
        raise ValueError("closed bars require unique non-null timestamps")
    data = bars.sort_index().copy()
    if data.index.tz is None:
        data.index = data.index.tz_localize(FIXED_UTC_MINUS_4)
    if as_of is not None:
        cutoff = analysis_time(as_of)
        data = data.loc[data.index <= cutoff]
    data = _normalize_ohlc(data)
    if not all(isfinite(value) for value in data.to_numpy().flat):
        raise ValueError("bars must have finite OHLC values")
    return data


def comparable_time(value: str | pd.Timestamp, index: pd.DatetimeIndex) -> pd.Timestamp:
    timestamp = analysis_time(value)
    if index.tz is None:
        return timestamp.tz_convert(FIXED_UTC_MINUS_4).tz_localize(None)
    return timestamp.tz_convert(index.tz)
