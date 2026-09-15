from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    TRANSITION = "TRANSITION"
    UNCONFIRMED = "UNCONFIRMED"


class RangeRole(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class RangeStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"


class WeeklyProfileType(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    CLASSIC_EXPANSION = "CLASSIC_EXPANSION"
    MIDWEEK_REVERSAL = "MIDWEEK_REVERSAL"
    THURSDAY_REVERSAL = "THURSDAY_REVERSAL"
    FRIDAY_REVERSAL = "FRIDAY_REVERSAL"


class ProfileStatus(str, Enum):
    DEVELOPING = "DEVELOPING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


class DayType(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    RANGE = "RANGE"
    REVERSAL_CANDIDATE = "REVERSAL_CANDIDATE"
    CONTINUATION_CANDIDATE = "CONTINUATION_CANDIDATE"
    RETRACEMENT_CANDIDATE = "RETRACEMENT_CANDIDATE"
    RETURN_TO_RANGE_CANDIDATE = "RETURN_TO_RANGE_CANDIDATE"
    WAITING_FOR_DRAW_STATUS = "WAITING_FOR_DRAW_STATUS"


class DailyDelivery(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    OLHC = "OLHC"
    OHLC = "OHLC"


class DailyPhase(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    OPENING = "OPENING"
    MANIPULATION = "MANIPULATION"
    REVERSAL_FORMATION = "REVERSAL_FORMATION"
    EXPANSION = "EXPANSION"
    OBJECTIVE_REACHED = "OBJECTIVE_REACHED"
    RETRACEMENT = "RETRACEMENT"
    INVALIDATED = "INVALIDATED"


class H4ProfileType(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    LONDON_REVERSAL = "LONDON_REVERSAL"
    SIX_AM_CONTINUATION = "SIX_AM_CONTINUATION"
    SIX_AM_REVERSAL = "SIX_AM_REVERSAL"
    NY_CONTINUATION = "NY_CONTINUATION"
    NY_REVERSAL = "NY_REVERSAL"


class H4Phase(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    PRE_DRIVER = "PRE_DRIVER"
    DRIVER_CONTINUATION = "DRIVER_CONTINUATION"
    DRIVER_REVERSAL = "DRIVER_REVERSAL"
    POST_DRIVER_EXPANSION = "POST_DRIVER_EXPANSION"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"


class H4LocationContext(str, Enum):
    UNKNOWN = "UNKNOWN"
    IRL = "IRL"
    ERL_TO_IRL = "ERL_TO_IRL"
    OPR = "OPR"
    OB_CONTINUATION = "OB_CONTINUATION"


@dataclass
class OrderFlowRange:
    direction: Direction
    role: RangeRole
    source_position: int
    source_time: str
    low: float
    high: float
    source_open: float
    source_close: float
    status: RangeStatus = RangeStatus.CANDIDATE
    confirmed_position: int | None = None
    confirmed_time: str | None = None
    invalidated_position: int | None = None
    invalidated_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderFlowResult:
    timeframe: str
    control: Direction
    latest_event: OrderFlowRange | None
    active_support_ranges: list[OrderFlowRange] = field(default_factory=list)
    active_resistance_ranges: list[OrderFlowRange] = field(default_factory=list)
    confirmed_events: list[OrderFlowRange] = field(default_factory=list)
    transition_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "control": self.control.value,
            "latest_event": self.latest_event.to_dict() if self.latest_event else None,
            "active_support_ranges": [item.to_dict() for item in self.active_support_ranges],
            "active_resistance_ranges": [item.to_dict() for item in self.active_resistance_ranges],
            "confirmed_events": [item.to_dict() for item in self.confirmed_events],
            "transition_reason": self.transition_reason,
        }


@dataclass
class IOFCResult:
    expected_direction: Direction
    confirmed: bool
    confirmation_range: OrderFlowRange | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_direction": self.expected_direction.value,
            "confirmed": self.confirmed,
            "confirmation_range": (
                self.confirmation_range.to_dict() if self.confirmation_range else None
            ),
            "reason": self.reason,
        }


@dataclass
class WeeklyExtreme:
    extreme_type: str
    day: str
    price: float
    protected: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WeeklyProfileResult:
    profile: WeeklyProfileType
    status: ProfileStatus
    direction: Direction
    current_day: str
    current_day_type: DayType
    week_phase: str
    weekly_extreme: WeeklyExtreme | None
    expected_daily_delivery: str | None
    expected_next_phase: str | None
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "status": self.status.value,
            "direction": self.direction.value,
            "current_day": self.current_day,
            "current_day_type": self.current_day_type.value,
            "week_phase": self.week_phase,
            "weekly_extreme": self.weekly_extreme.to_dict() if self.weekly_extreme else None,
            "expected_daily_delivery": self.expected_daily_delivery,
            "expected_next_phase": self.expected_next_phase,
            "reason_codes": list(self.reason_codes),
        }


@dataclass
class DailyProfileResult:
    trading_day: str
    direction: Direction
    day_type: DayType
    expected_delivery: DailyDelivery
    observed_delivery: DailyDelivery
    status: ProfileStatus
    phase: DailyPhase
    daily_open: float
    daily_high: float
    daily_low: float
    current_close: float
    high_time: str
    low_time: str
    protected_extreme: bool | None
    expected_next_phase: str | None
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trading_day": self.trading_day,
            "direction": self.direction.value,
            "day_type": self.day_type.value,
            "expected_delivery": self.expected_delivery.value,
            "observed_delivery": self.observed_delivery.value,
            "status": self.status.value,
            "phase": self.phase.value,
            "daily_open": self.daily_open,
            "daily_high": self.daily_high,
            "daily_low": self.daily_low,
            "current_close": self.current_close,
            "high_time": self.high_time,
            "low_time": self.low_time,
            "protected_extreme": self.protected_extreme,
            "expected_next_phase": self.expected_next_phase,
            "reason_codes": list(self.reason_codes),
        }


@dataclass
class H4CandleState:
    label: str
    start_time: str
    end_time: str
    open: float
    high: float
    low: float
    close: float
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class H4ProfileResult:
    profile: H4ProfileType
    status: ProfileStatus
    direction: Direction
    phase: H4Phase
    active_h4: str
    driver_h4: str
    expected_driver_action: str
    reversal_before_driver: bool | None
    location_context: H4LocationContext
    candles: list[H4CandleState]
    expected_next_phase: str | None
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "status": self.status.value,
            "direction": self.direction.value,
            "phase": self.phase.value,
            "active_h4": self.active_h4,
            "driver_h4": self.driver_h4,
            "expected_driver_action": self.expected_driver_action,
            "reversal_before_driver": self.reversal_before_driver,
            "location_context": self.location_context.value,
            "candles": [item.to_dict() for item in self.candles],
            "expected_next_phase": self.expected_next_phase,
            "reason_codes": list(self.reason_codes),
        }
