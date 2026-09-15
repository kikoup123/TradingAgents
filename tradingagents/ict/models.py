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
