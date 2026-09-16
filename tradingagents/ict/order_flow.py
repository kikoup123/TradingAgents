from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .models import (
    Direction,
    IOFCResult,
    OrderFlowRange,
    OrderFlowResult,
    RangeRole,
    RangeStatus,
)

_REQUIRED_COLUMNS = ("open", "high", "low", "close")


def _timestamp_label(index_value: object, position: int) -> str:
    if hasattr(index_value, "isoformat"):
        try:
            return index_value.isoformat()
        except TypeError:
            pass
    return str(index_value) if index_value is not None else str(position)


def _normalize_ohlc(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        raise ValueError("bars must contain at least one OHLC candle")

    rename_map = {str(col).lower(): col for col in bars.columns}
    missing = [name for name in _REQUIRED_COLUMNS if name not in rename_map]
    if missing:
        raise ValueError(f"bars are missing required columns: {', '.join(missing)}")

    normalized = bars[[rename_map[name] for name in _REQUIRED_COLUMNS]].copy()
    normalized.columns = list(_REQUIRED_COLUMNS)
    normalized = normalized.astype(float)

    if normalized[list(_REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("bars contain NaN OHLC values")

    invalid = (normalized["high"] < normalized[["open", "close", "low"]].max(axis=1)) | (
        normalized["low"] > normalized[["open", "close", "high"]].min(axis=1)
    )
    if invalid.any():
        raise ValueError("bars contain invalid OHLC relationships")

    return normalized


class OrderFlowEngine:
    """Deterministic institutional order-flow range engine.

    Londres rules encoded here:

    * Bullish IOF: a down-close candle creates a candidate range. A later candle
      must BODY-CLOSE above that candle's full high/low range. That traded-through
      down-close range then acts as support.
    * Bearish IOF: an up-close candle creates a candidate range. A later candle
      must BODY-CLOSE below the range. That traded-through up-close range then
      acts as resistance.
    * Wicks alone never confirm IOFC.
    * A confirmed bullish support range is invalidated by a later body close below
      its low. A confirmed bearish resistance range is invalidated by a later body
      close above its high.

    The engine intentionally calculates facts only. It does not ask an LLM to
    decide whether order flow "looks" bullish or bearish.
    """

    def __init__(self, *, max_candidate_ranges: int = 200):
        if max_candidate_ranges < 1:
            raise ValueError("max_candidate_ranges must be >= 1")
        self.max_candidate_ranges = max_candidate_ranges

    def analyze(self, bars: pd.DataFrame, *, timeframe: str) -> OrderFlowResult:
        data = _normalize_ohlc(bars)

        bullish_candidates: list[OrderFlowRange] = []
        bearish_candidates: list[OrderFlowRange] = []
        confirmed_events: list[OrderFlowRange] = []

        for position, (index_value, row) in enumerate(data.iterrows()):
            close = float(row["close"])
            time_label = _timestamp_label(index_value, position)

            # Confirmed ranges remain live even after candidate retention rolls.
            for confirmed in confirmed_events:
                if confirmed.status != RangeStatus.CONFIRMED:
                    continue
                invalidated = (
                    close < confirmed.low if confirmed.direction == Direction.BULLISH
                    else close > confirmed.high
                )
                if invalidated:
                    confirmed.status = RangeStatus.INVALIDATED
                    confirmed.invalidated_position = position
                    confirmed.invalidated_time = time_label

            for candidate in bullish_candidates:
                if candidate.status == RangeStatus.CANDIDATE and close > candidate.high:
                    candidate.status = RangeStatus.CONFIRMED
                    candidate.confirmed_position = position
                    candidate.confirmed_time = time_label
                    confirmed_events.append(candidate)
                elif candidate.status == RangeStatus.CONFIRMED and close < candidate.low:
                    candidate.status = RangeStatus.INVALIDATED
                    candidate.invalidated_position = position
                    candidate.invalidated_time = time_label

            for candidate in bearish_candidates:
                if candidate.status == RangeStatus.CANDIDATE and close < candidate.low:
                    candidate.status = RangeStatus.CONFIRMED
                    candidate.confirmed_position = position
                    candidate.confirmed_time = time_label
                    confirmed_events.append(candidate)
                elif candidate.status == RangeStatus.CONFIRMED and close > candidate.high:
                    candidate.status = RangeStatus.INVALIDATED
                    candidate.invalidated_position = position
                    candidate.invalidated_time = time_label

            candle_open = float(row["open"])
            candle_high = float(row["high"])
            candle_low = float(row["low"])

            if close < candle_open:
                bullish_candidates.append(
                    OrderFlowRange(
                        direction=Direction.BULLISH,
                        role=RangeRole.SUPPORT,
                        source_position=position,
                        source_time=time_label,
                        low=candle_low,
                        high=candle_high,
                        source_open=candle_open,
                        source_close=close,
                    )
                )
                bullish_candidates = bullish_candidates[-self.max_candidate_ranges :]

            elif close > candle_open:
                bearish_candidates.append(
                    OrderFlowRange(
                        direction=Direction.BEARISH,
                        role=RangeRole.RESISTANCE,
                        source_position=position,
                        source_time=time_label,
                        low=candle_low,
                        high=candle_high,
                        source_open=candle_open,
                        source_close=close,
                    )
                )
                bearish_candidates = bearish_candidates[-self.max_candidate_ranges :]

        confirmed_events.sort(
            key=lambda item: (
                item.confirmed_position if item.confirmed_position is not None else -1,
                item.source_position,
            )
        )
        latest_event = confirmed_events[-1] if confirmed_events else None

        active_support = [
            item for item in confirmed_events if item.status == RangeStatus.CONFIRMED and item.direction == Direction.BULLISH
        ]
        active_resistance = [
            item for item in confirmed_events if item.status == RangeStatus.CONFIRMED and item.direction == Direction.BEARISH
        ]

        control = Direction.UNCONFIRMED
        transition_reason = None
        if latest_event is not None:
            if latest_event.status == RangeStatus.CONFIRMED:
                control = latest_event.direction
            else:
                control = Direction.TRANSITION
                transition_reason = (
                    f"Latest {latest_event.direction.value} IOF range was invalidated "
                    "without a later confirmed opposing IOFC."
                )

        return OrderFlowResult(
            timeframe=timeframe,
            control=control,
            latest_event=latest_event,
            active_support_ranges=active_support,
            active_resistance_ranges=active_resistance,
            confirmed_events=confirmed_events,
            transition_reason=transition_reason,
        )

    def find_iofc_after(
        self,
        bars: pd.DataFrame,
        *,
        anchor_position: int,
        expected_direction: Direction,
    ) -> IOFCResult:
        """Find the first still-valid IOFC range formed AFTER a CSD/anchor event.

        Bullish CSD -> wait for a NEW down-close range -> later body close above it.
        Bearish CSD -> wait for a NEW up-close range -> later body close below it.
        """
        if expected_direction not in {Direction.BULLISH, Direction.BEARISH}:
            raise ValueError("expected_direction must be BULLISH or BEARISH")

        data = _normalize_ohlc(bars)
        if anchor_position < -1 or anchor_position >= len(data):
            raise ValueError("anchor_position is outside the supplied bars")

        result = self.analyze(data, timeframe="POST_CSD")
        eligible = [event for event in result.confirmed_events
                    if event.source_position > anchor_position
                    and event.direction == expected_direction
                    and event.status == RangeStatus.CONFIRMED]
        if eligible and result.control == expected_direction:
            return IOFCResult(
                expected_direction=expected_direction,
                confirmed=True,
                confirmation_range=eligible[0],
                reason="BODY_CLOSE_CONFIRMED_POST_CSD_IOFC",
            )

        return IOFCResult(
            expected_direction=expected_direction,
            confirmed=False,
            confirmation_range=None,
            reason=(
                "WAITING_FOR_NEW_DOWN_CLOSE_RANGE_AND_BODY_ACCEPTANCE_ABOVE"
                if expected_direction == Direction.BULLISH
                else "WAITING_FOR_NEW_UP_CLOSE_RANGE_AND_BODY_ACCEPTANCE_BELOW"
            ),
        )

    def analyze_timeframes(
        self, timeframe_bars: Mapping[str, pd.DataFrame]
    ) -> dict[str, OrderFlowResult]:
        return {
            timeframe: self.analyze(bars, timeframe=timeframe)
            for timeframe, bars in timeframe_bars.items()
        }
