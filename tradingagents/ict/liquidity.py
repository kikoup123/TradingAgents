from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from .models import Direction
from .order_flow import _normalize_ohlc


class LiquiditySide(str, Enum):
    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


class LiquidityClass(str, Enum):
    EXTERNAL = "EXTERNAL"
    INTERNAL = "INTERNAL"


class LiquidityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RAIDED_RECLAIMED = "RAIDED_RECLAIMED"
    CONSUMED = "CONSUMED"


@dataclass
class LiquidityPool:
    side: LiquiditySide
    liquidity_class: LiquidityClass
    price: float
    timeframe: str
    source_kind: str
    source_position: int
    source_time: str
    status: LiquidityStatus = LiquidityStatus.ACTIVE
    protected: bool = False
    event_position: int | None = None
    event_time: str | None = None
    event_close: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["side"] = self.side.value
        result["liquidity_class"] = self.liquidity_class.value
        result["status"] = self.status.value
        return result


@dataclass
class LiquidityResult:
    timeframe: str
    order_flow_control: Direction
    current_price: float
    buy_side: list[LiquidityPool]
    sell_side: list[LiquidityPool]
    protected_pool: LiquidityPool | None
    active_draw: LiquidityPool | None
    external_high: float | None
    external_low: float | None
    dealing_range_equilibrium: float | None
    dealing_range_location: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "order_flow_control": self.order_flow_control.value,
            "current_price": self.current_price,
            "buy_side": [pool.to_dict() for pool in self.buy_side],
            "sell_side": [pool.to_dict() for pool in self.sell_side],
            "protected_pool": self.protected_pool.to_dict() if self.protected_pool else None,
            "active_draw": self.active_draw.to_dict() if self.active_draw else None,
            "external_high": self.external_high,
            "external_low": self.external_low,
            "dealing_range_equilibrium": self.dealing_range_equilibrium,
            "dealing_range_location": self.dealing_range_location,
            "reason_codes": list(self.reason_codes),
        }


class LiquidityEngine:
    """Deterministic ICT liquidity map.

    Phase 4 encodes objective price facts instead of asking an LLM to infer
    liquidity visually:

    * confirmed swing highs are buy-side liquidity (BSL);
    * confirmed swing lows are sell-side liquidity (SSL);
    * the highest confirmed swing high and lowest confirmed swing low are ERL;
    * confirmed swings inside that external range are IRL;
    * a wick through a pool followed by a body close back through the level is
      classified as a raid + reclaim;
    * a body close through the level consumes that pool;
    * bullish order flow protects the latest active structural SSL, while
      bearish order flow protects the latest active structural BSL;
    * the active draw is the nearest ACTIVE external pool in the direction of
      confirmed order flow, falling back to internal liquidity only when no
      external pool remains in that direction.

    The engine only uses completed pivots. A pivot requires `pivot_span` bars on
    both sides, preventing look-ahead classification of the most recent bars.
    """

    def __init__(self, *, pivot_span: int = 2, tolerance: float = 0.0) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be >= 1")
        if tolerance < 0:
            raise ValueError("tolerance must be >= 0")
        self.pivot_span = pivot_span
        self.tolerance = tolerance

    def analyze(
        self,
        bars: pd.DataFrame,
        *,
        timeframe: str,
        order_flow_control: Direction = Direction.UNCONFIRMED,
        time_price_state: dict[str, Any] | None = None,
    ) -> LiquidityResult:
        data = _normalize_ohlc(bars).sort_index()
        structural = self._structural_pools(data, timeframe=timeframe)
        named = self._named_session_pools(
            time_price_state,
            timeframe=timeframe,
        )
        pools = structural + named

        for pool in pools:
            self._resolve_pool_status(data, pool)

        protected = self._protected_pool(pools, order_flow_control)
        if protected is not None:
            protected.protected = True

        current_price = float(data.iloc[-1]["close"])
        active_draw = self._active_draw(
            pools,
            current_price=current_price,
            order_flow_control=order_flow_control,
        )

        buy_side = [pool for pool in pools if pool.side == LiquiditySide.BUY_SIDE]
        sell_side = [pool for pool in pools if pool.side == LiquiditySide.SELL_SIDE]
        external_high = self._external_price(buy_side, high=True)
        external_low = self._external_price(sell_side, high=False)
        equilibrium, location = self._dealing_range_location(
            current_price=current_price,
            external_high=external_high,
            external_low=external_low,
        )

        reason_codes = [
            f"ORDER_FLOW_{order_flow_control.value}",
            f"STRUCTURAL_POOLS_{len(structural)}",
            f"NAMED_SESSION_POOLS_{len(named)}",
        ]
        if protected is not None:
            reason_codes.append(
                f"PROTECTED_{protected.side.value}_{protected.source_kind}"
            )
        if active_draw is not None:
            reason_codes.append(
                f"ACTIVE_DRAW_{active_draw.side.value}_{active_draw.liquidity_class.value}"
            )
        else:
            reason_codes.append("ACTIVE_DRAW_UNRESOLVED")

        return LiquidityResult(
            timeframe=timeframe,
            order_flow_control=order_flow_control,
            current_price=current_price,
            buy_side=buy_side,
            sell_side=sell_side,
            protected_pool=protected,
            active_draw=active_draw,
            external_high=external_high,
            external_low=external_low,
            dealing_range_equilibrium=equilibrium,
            dealing_range_location=location,
            reason_codes=reason_codes,
        )

    def _structural_pools(
        self,
        data: pd.DataFrame,
        *,
        timeframe: str,
    ) -> list[LiquidityPool]:
        highs: list[LiquidityPool] = []
        lows: list[LiquidityPool] = []
        span = self.pivot_span

        for position in range(span, len(data) - span):
            row = data.iloc[position]
            left = data.iloc[position - span : position]
            right = data.iloc[position + 1 : position + span + 1]
            source_time = self._time_label(data.index[position], position)

            high = float(row["high"])
            if high > float(left["high"].max()) and high > float(right["high"].max()):
                highs.append(
                    LiquidityPool(
                        side=LiquiditySide.BUY_SIDE,
                        liquidity_class=LiquidityClass.INTERNAL,
                        price=high,
                        timeframe=timeframe,
                        source_kind="SWING_HIGH",
                        source_position=position,
                        source_time=source_time,
                    )
                )

            low = float(row["low"])
            if low < float(left["low"].min()) and low < float(right["low"].min()):
                lows.append(
                    LiquidityPool(
                        side=LiquiditySide.SELL_SIDE,
                        liquidity_class=LiquidityClass.INTERNAL,
                        price=low,
                        timeframe=timeframe,
                        source_kind="SWING_LOW",
                        source_position=position,
                        source_time=source_time,
                    )
                )

        if highs:
            max(highs, key=lambda pool: pool.price).liquidity_class = LiquidityClass.EXTERNAL
        if lows:
            min(lows, key=lambda pool: pool.price).liquidity_class = LiquidityClass.EXTERNAL
        return highs + lows

    @staticmethod
    def _named_session_pools(
        time_price_state: dict[str, Any] | None,
        *,
        timeframe: str,
    ) -> list[LiquidityPool]:
        if not time_price_state:
            return []
        ons = time_price_state.get("ons", {})
        pools: list[LiquidityPool] = []
        for key, payload in ons.items():
            if payload.get("status") not in {"ACTIVE", "COMPLETE"}:
                continue
            end_time = payload.get("end_time") or payload.get("start_time") or "UNKNOWN"
            high = payload.get("high")
            low = payload.get("low")
            if high is not None:
                pools.append(
                    LiquidityPool(
                        side=LiquiditySide.BUY_SIDE,
                        liquidity_class=LiquidityClass.EXTERNAL,
                        price=float(high),
                        timeframe=timeframe,
                        source_kind=f"{key.upper()}_HIGH",
                        source_position=-1,
                        source_time=str(end_time),
                    )
                )
            if low is not None:
                pools.append(
                    LiquidityPool(
                        side=LiquiditySide.SELL_SIDE,
                        liquidity_class=LiquidityClass.EXTERNAL,
                        price=float(low),
                        timeframe=timeframe,
                        source_kind=f"{key.upper()}_LOW",
                        source_position=-1,
                        source_time=str(end_time),
                    )
                )
        return pools

    def _resolve_pool_status(self, data: pd.DataFrame, pool: LiquidityPool) -> None:
        event_data = self._event_window(data, pool)
        if event_data.empty:
            return

        for position, (index_value, row) in enumerate(event_data.iterrows()):
            absolute_position = self._absolute_position(data, index_value, position)
            close = float(row["close"])
            event_time = self._time_label(index_value, absolute_position)

            if pool.side == LiquiditySide.BUY_SIDE:
                breached = float(row["high"]) > pool.price + self.tolerance
                if not breached:
                    continue
                pool.status = (
                    LiquidityStatus.RAIDED_RECLAIMED
                    if close < pool.price - self.tolerance
                    else LiquidityStatus.CONSUMED
                )
            else:
                breached = float(row["low"]) < pool.price - self.tolerance
                if not breached:
                    continue
                pool.status = (
                    LiquidityStatus.RAIDED_RECLAIMED
                    if close > pool.price + self.tolerance
                    else LiquidityStatus.CONSUMED
                )

            pool.event_position = absolute_position
            pool.event_time = event_time
            pool.event_close = close
            return

    @staticmethod
    def _event_window(data: pd.DataFrame, pool: LiquidityPool) -> pd.DataFrame:
        if pool.source_position >= 0:
            return data.iloc[pool.source_position + 1 :]
        if not isinstance(data.index, pd.DatetimeIndex):
            return data.iloc[0:0]
        try:
            source_time = pd.Timestamp(pool.source_time)
            index = data.index
            if index.tz is not None and source_time.tzinfo is None:
                source_time = source_time.tz_localize(index.tz)
            elif index.tz is None and source_time.tzinfo is not None:
                source_time = source_time.tz_localize(None)
            elif index.tz is not None and source_time.tzinfo is not None:
                source_time = source_time.tz_convert(index.tz)
            return data.loc[index >= source_time]
        except (TypeError, ValueError):
            return data.iloc[0:0]

    @staticmethod
    def _absolute_position(data: pd.DataFrame, index_value: object, fallback: int) -> int:
        try:
            location = data.index.get_loc(index_value)
            return int(location) if not isinstance(location, slice) else int(location.start or 0)
        except (KeyError, TypeError):
            return fallback

    @staticmethod
    def _protected_pool(
        pools: list[LiquidityPool],
        control: Direction,
    ) -> LiquidityPool | None:
        if control == Direction.BULLISH:
            candidates = [
                pool
                for pool in pools
                if pool.side == LiquiditySide.SELL_SIDE
                and pool.source_position >= 0
                and pool.status == LiquidityStatus.ACTIVE
            ]
        elif control == Direction.BEARISH:
            candidates = [
                pool
                for pool in pools
                if pool.side == LiquiditySide.BUY_SIDE
                and pool.source_position >= 0
                and pool.status == LiquidityStatus.ACTIVE
            ]
        else:
            return None
        return max(candidates, key=lambda pool: pool.source_position) if candidates else None

    @staticmethod
    def _active_draw(
        pools: list[LiquidityPool],
        *,
        current_price: float,
        order_flow_control: Direction,
    ) -> LiquidityPool | None:
        if order_flow_control == Direction.BULLISH:
            candidates = [
                pool
                for pool in pools
                if pool.side == LiquiditySide.BUY_SIDE
                and pool.status == LiquidityStatus.ACTIVE
                and pool.price > current_price
            ]
        elif order_flow_control == Direction.BEARISH:
            candidates = [
                pool
                for pool in pools
                if pool.side == LiquiditySide.SELL_SIDE
                and pool.status == LiquidityStatus.ACTIVE
                and pool.price < current_price
            ]
        else:
            return None

        if not candidates:
            return None
        external = [
            pool for pool in candidates if pool.liquidity_class == LiquidityClass.EXTERNAL
        ]
        selection = external or candidates
        return min(selection, key=lambda pool: abs(pool.price - current_price))

    @staticmethod
    def _external_price(pools: list[LiquidityPool], *, high: bool) -> float | None:
        values = [
            pool.price
            for pool in pools
            if pool.liquidity_class == LiquidityClass.EXTERNAL
        ]
        if not values:
            return None
        return max(values) if high else min(values)

    @staticmethod
    def _dealing_range_location(
        *,
        current_price: float,
        external_high: float | None,
        external_low: float | None,
    ) -> tuple[float | None, str]:
        if external_high is None or external_low is None or external_high <= external_low:
            return None, "UNRESOLVED"
        equilibrium = (external_high + external_low) / 2.0
        if current_price > equilibrium:
            return equilibrium, "PREMIUM"
        if current_price < equilibrium:
            return equilibrium, "DISCOUNT"
        return equilibrium, "EQUILIBRIUM"

    @staticmethod
    def _time_label(index_value: object, position: int) -> str:
        if hasattr(index_value, "isoformat"):
            try:
                return index_value.isoformat()
            except TypeError:
                pass
        return str(index_value) if index_value is not None else str(position)
