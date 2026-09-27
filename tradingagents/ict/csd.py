from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from .models import Direction
from .order_flow import _normalize_ohlc


@dataclass(frozen=True)
class CSDPivotReference:
    side: str
    price: float
    source_position: int
    source_time: str
    confirmed_position: int
    confirmed_time: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CSDEvent:
    direction: Direction
    timeframe: str
    liquidity_side: str
    liquidity_reference: CSDPivotReference
    raid_position: int
    raid_time: str
    threshold_position: int
    threshold_time: str
    threshold_open: float
    confirmation_position: int
    confirmation_time: str
    confirmation_close: float
    protected_extreme: float
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "timeframe": self.timeframe,
            "liquidity_side": self.liquidity_side,
            "liquidity_reference": self.liquidity_reference.to_dict(),
            "raid_position": self.raid_position,
            "raid_time": self.raid_time,
            "threshold_position": self.threshold_position,
            "threshold_time": self.threshold_time,
            "threshold_open": self.threshold_open,
            "confirmation_position": self.confirmation_position,
            "confirmation_time": self.confirmation_time,
            "confirmation_close": self.confirmation_close,
            "protected_extreme": self.protected_extreme,
            "reason_codes": list(self.reason_codes),
        }


@dataclass
class CSDResult:
    timeframe: str
    confirmed: bool
    direction: Direction
    latest_event: CSDEvent | None
    events: list[CSDEvent] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "confirmed": self.confirmed,
            "direction": self.direction.value,
            "latest_event": self.latest_event.to_dict() if self.latest_event else None,
            "events": [event.to_dict() for event in self.events],
            "reason_codes": list(self.reason_codes),
        }


@dataclass
class _PendingCSD:
    direction: Direction
    liquidity_side: str
    liquidity_reference: CSDPivotReference
    raid_position: int
    raid_time: str
    threshold_position: int
    threshold_time: str
    threshold_open: float
    protected_extreme: float


class CSDEngine:
    """Deterministic Change in the State of Delivery engine.

    Londres rules encoded here:

    * Bullish CSD begins with a first-time sell-side liquidity raid of a
      confirmed structural low. The reversal threshold is the OPEN of the
      highest down-close candle participating in that delivery leg. A later
      candle must BODY-CLOSE above that opening price. The lowest price made
      during the raid-to-confirmation sequence becomes the protected low.
    * Bearish CSD is the inverse: first-time buy-side liquidity raid, then a
      BODY-CLOSE below the OPEN of the lowest up-close candle in the delivery
      leg. The highest price made during the sequence becomes the protected high.
    * Wicks through a CSD threshold never confirm a CSD.
    * Structural references are only usable after their pivot confirmation
      bars have completed, so the engine does not classify pivots with look-ahead.

    This engine identifies the delivery shift itself. Post-CSD IOFC is handled
    separately by OrderFlowEngine.find_iofc_after().
    """

    def __init__(self, *, pivot_span: int = 2) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be >= 1")
        self.pivot_span = pivot_span

    def analyze(
        self,
        bars: pd.DataFrame,
        *,
        timeframe: str,
        as_of: pd.Timestamp | str | None = None,
    ) -> CSDResult:
        data = _normalize_ohlc(bars).sort_index()
        if as_of is not None:
            data = self._cutoff(data, as_of)
        if len(data) < 2 * self.pivot_span + 2:
            return CSDResult(
                timeframe=timeframe,
                confirmed=False,
                direction=Direction.UNCONFIRMED,
                latest_event=None,
                reason_codes=["INSUFFICIENT_STRUCTURAL_DATA"],
            )

        pivots = self._confirmed_pivots(data)
        pending: list[_PendingCSD] = []
        events: list[CSDEvent] = []
        ambiguous_raid_seen = False

        for position in range(2 * self.pivot_span + 1, len(data)):
            high_ref = self._latest_reference(pivots["high"], before_position=position)
            low_ref = self._latest_reference(pivots["low"], before_position=position)

            buy_side_raid = bool(
                high_ref is not None and self._first_high_take(data, position, high_ref)
            )
            sell_side_raid = bool(
                low_ref is not None and self._first_low_take(data, position, low_ref)
            )

            if buy_side_raid and sell_side_raid:
                ambiguous_raid_seen = True
            else:
                if sell_side_raid and low_ref is not None:
                    candidate = self._new_bullish_candidate(
                        data,
                        position=position,
                        low_ref=low_ref,
                        latest_high_ref=high_ref,
                    )
                    if candidate is not None:
                        pending.append(candidate)

                if buy_side_raid and high_ref is not None:
                    candidate = self._new_bearish_candidate(
                        data,
                        position=position,
                        high_ref=high_ref,
                        latest_low_ref=low_ref,
                    )
                    if candidate is not None:
                        pending.append(candidate)

            row = data.iloc[position]
            candle_open = float(row["open"])
            candle_close = float(row["close"])
            candle_high = float(row["high"])
            candle_low = float(row["low"])
            time_label = self._time_label(data.index[position], position)

            survivors: list[_PendingCSD] = []
            for candidate in pending:
                if candidate.direction == Direction.BULLISH:
                    candidate.protected_extreme = min(candidate.protected_extreme, candle_low)
                    if candle_close < candle_open and candle_open > candidate.threshold_open:
                        candidate.threshold_open = candle_open
                        candidate.threshold_position = position
                        candidate.threshold_time = time_label
                    confirmed = candle_close > candidate.threshold_open
                else:
                    candidate.protected_extreme = max(candidate.protected_extreme, candle_high)
                    if candle_close > candle_open and candle_open < candidate.threshold_open:
                        candidate.threshold_open = candle_open
                        candidate.threshold_position = position
                        candidate.threshold_time = time_label
                    confirmed = candle_close < candidate.threshold_open

                if not confirmed:
                    survivors.append(candidate)
                    continue

                events.append(
                    CSDEvent(
                        direction=candidate.direction,
                        timeframe=timeframe,
                        liquidity_side=candidate.liquidity_side,
                        liquidity_reference=candidate.liquidity_reference,
                        raid_position=candidate.raid_position,
                        raid_time=candidate.raid_time,
                        threshold_position=candidate.threshold_position,
                        threshold_time=candidate.threshold_time,
                        threshold_open=candidate.threshold_open,
                        confirmation_position=position,
                        confirmation_time=time_label,
                        confirmation_close=candle_close,
                        protected_extreme=candidate.protected_extreme,
                        reason_codes=[
                            f"{candidate.liquidity_side}_LIQUIDITY_RAID",
                            "OPPOSING_CLOSE_CANDLE_OPEN_RECLAIM",
                            "BODY_CLOSE_CSD_CONFIRMATION",
                            (
                                "PROTECTED_LOW_CONFIRMED"
                                if candidate.direction == Direction.BULLISH
                                else "PROTECTED_HIGH_CONFIRMED"
                            ),
                        ],
                    )
                )

            pending = survivors

        events.sort(key=lambda event: event.confirmation_position)
        latest = events[-1] if events else None
        reasons = [f"CSD_EVENTS_{len(events)}"]
        if ambiguous_raid_seen:
            reasons.append("AMBIGUOUS_TWO_SIDED_LIQUIDITY_RAID_IGNORED")
        if latest is None:
            reasons.append("NO_CONFIRMED_CSD")
        else:
            reasons.extend(
                [
                    f"LATEST_CSD_{latest.direction.value}",
                    "CSD_REQUIRES_BODY_CLOSE_NOT_WICK",
                ]
            )

        return CSDResult(
            timeframe=timeframe,
            confirmed=latest is not None,
            direction=latest.direction if latest else Direction.UNCONFIRMED,
            latest_event=latest,
            events=events,
            reason_codes=reasons,
        )

    def _confirmed_pivots(
        self, data: pd.DataFrame
    ) -> dict[str, list[CSDPivotReference]]:
        span = self.pivot_span
        highs: list[CSDPivotReference] = []
        lows: list[CSDPivotReference] = []
        for position in range(span, len(data) - span):
            row = data.iloc[position]
            left = data.iloc[position - span : position]
            right = data.iloc[position + 1 : position + span + 1]
            confirmation_position = position + span

            high = float(row["high"])
            if high > float(left["high"].max()) and high > float(right["high"].max()):
                highs.append(
                    CSDPivotReference(
                        side="HIGH",
                        price=high,
                        source_position=position,
                        source_time=self._time_label(data.index[position], position),
                        confirmed_position=confirmation_position,
                        confirmed_time=self._time_label(
                            data.index[confirmation_position], confirmation_position
                        ),
                    )
                )

            low = float(row["low"])
            if low < float(left["low"].min()) and low < float(right["low"].min()):
                lows.append(
                    CSDPivotReference(
                        side="LOW",
                        price=low,
                        source_position=position,
                        source_time=self._time_label(data.index[position], position),
                        confirmed_position=confirmation_position,
                        confirmed_time=self._time_label(
                            data.index[confirmation_position], confirmation_position
                        ),
                    )
                )
        return {"high": highs, "low": lows}

    def _new_bullish_candidate(
        self,
        data: pd.DataFrame,
        *,
        position: int,
        low_ref: CSDPivotReference,
        latest_high_ref: CSDPivotReference | None,
    ) -> _PendingCSD | None:
        start = (
            latest_high_ref.source_position
            if latest_high_ref is not None
            else low_ref.confirmed_position
        )
        segment = data.iloc[max(0, start) : position + 1]
        opposing = segment.loc[segment["close"] < segment["open"]]
        if opposing.empty:
            return None
        opens = opposing["open"].astype(float)
        threshold_index = opens.idxmax()
        threshold_position = self._position_of(data, threshold_index)
        return _PendingCSD(
            direction=Direction.BULLISH,
            liquidity_side="SELL_SIDE",
            liquidity_reference=low_ref,
            raid_position=position,
            raid_time=self._time_label(data.index[position], position),
            threshold_position=threshold_position,
            threshold_time=self._time_label(threshold_index, threshold_position),
            threshold_open=float(opens.loc[threshold_index]),
            protected_extreme=float(data.iloc[position]["low"]),
        )

    def _new_bearish_candidate(
        self,
        data: pd.DataFrame,
        *,
        position: int,
        high_ref: CSDPivotReference,
        latest_low_ref: CSDPivotReference | None,
    ) -> _PendingCSD | None:
        start = (
            latest_low_ref.source_position
            if latest_low_ref is not None
            else high_ref.confirmed_position
        )
        segment = data.iloc[max(0, start) : position + 1]
        opposing = segment.loc[segment["close"] > segment["open"]]
        if opposing.empty:
            return None
        opens = opposing["open"].astype(float)
        threshold_index = opens.idxmin()
        threshold_position = self._position_of(data, threshold_index)
        return _PendingCSD(
            direction=Direction.BEARISH,
            liquidity_side="BUY_SIDE",
            liquidity_reference=high_ref,
            raid_position=position,
            raid_time=self._time_label(data.index[position], position),
            threshold_position=threshold_position,
            threshold_time=self._time_label(threshold_index, threshold_position),
            threshold_open=float(opens.loc[threshold_index]),
            protected_extreme=float(data.iloc[position]["high"]),
        )

    @staticmethod
    def latest_event_at_or_after(
        result: CSDResult,
        *,
        reference_time: pd.Timestamp | str,
    ) -> CSDEvent | None:
        cutoff = pd.Timestamp(reference_time)
        eligible: list[CSDEvent] = []
        for event in result.events:
            event_time = pd.Timestamp(event.confirmation_time)
            if event_time.tzinfo is not None and cutoff.tzinfo is None:
                cutoff_compare = cutoff.tz_localize(event_time.tzinfo)
            elif event_time.tzinfo is None and cutoff.tzinfo is not None:
                cutoff_compare = cutoff.tz_localize(None)
            elif event_time.tzinfo is not None and cutoff.tzinfo is not None:
                cutoff_compare = cutoff.tz_convert(event_time.tzinfo)
            else:
                cutoff_compare = cutoff
            if event_time >= cutoff_compare:
                eligible.append(event)
        return eligible[-1] if eligible else None

    @staticmethod
    def _latest_reference(
        references: list[CSDPivotReference], *, before_position: int
    ) -> CSDPivotReference | None:
        eligible = [
            reference
            for reference in references
            if reference.confirmed_position < before_position
        ]
        return eligible[-1] if eligible else None

    @staticmethod
    def _first_high_take(
        data: pd.DataFrame,
        position: int,
        reference: CSDPivotReference,
    ) -> bool:
        if float(data.iloc[position]["high"]) <= reference.price:
            return False
        previous = data.iloc[reference.confirmed_position : position]
        return previous.empty or float(previous["high"].max()) <= reference.price

    @staticmethod
    def _first_low_take(
        data: pd.DataFrame,
        position: int,
        reference: CSDPivotReference,
    ) -> bool:
        if float(data.iloc[position]["low"]) >= reference.price:
            return False
        previous = data.iloc[reference.confirmed_position : position]
        return previous.empty or float(previous["low"].min()) >= reference.price

    @staticmethod
    def _position_of(data: pd.DataFrame, index_value: object) -> int:
        location = data.index.get_loc(index_value)
        if isinstance(location, slice):
            return int(location.start or 0)
        return int(location)

    @staticmethod
    def _cutoff(data: pd.DataFrame, as_of: pd.Timestamp | str) -> pd.DataFrame:
        if not isinstance(data.index, pd.DatetimeIndex):
            raise ValueError("as_of requires bars with a DatetimeIndex")
        cutoff = pd.Timestamp(as_of)
        if data.index.tz is not None and cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize(data.index.tz)
        elif data.index.tz is None and cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        elif data.index.tz is not None and cutoff.tzinfo is not None:
            cutoff = cutoff.tz_convert(data.index.tz)
        return data.loc[data.index <= cutoff].copy()

    @staticmethod
    def _time_label(index_value: object, position: int) -> str:
        if hasattr(index_value, "isoformat"):
            try:
                return index_value.isoformat()
            except TypeError:
                pass
        return str(index_value) if index_value is not None else str(position)
