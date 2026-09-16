"""Broker-specific live execution contracts for Londres Phase 33.

The strategy stack never talks directly to provider APIs. Phase 33 binds one
immutable Phase 32 command to one explicit execution adapter and requires the
adapter to distinguish definitive acknowledgement/rejection from ambiguous
network outcomes. Ambiguous submissions must be reconciled; they are never
blindly retried.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from .contracts import BrokerType


class BrokerExecutionOutcome(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True)
class BrokerExecutionCapabilities:
    broker_type: BrokerType
    venue: str
    execution_enabled: bool
    supports_market_orders: bool
    supports_server_side_stop: bool
    supports_server_side_target: bool
    supports_reconciliation: bool

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["broker_type"] = self.broker_type.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


@dataclass(frozen=True)
class BrokerExecutionReceipt:
    command_id: str
    account_alias: str
    venue: str
    broker_type: BrokerType
    client_order_label: str
    outcome: BrokerExecutionOutcome
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    filled_volume: float | None = None
    average_fill_price: float | None = None
    stop_protection_active: bool = False
    target_protection_active: bool = False
    provider_code: str | None = None
    provider_message: str | None = None
    submitted_at_ms: int | None = None
    acknowledged_at_ms: int | None = None
    definite_no_fill: bool = False

    def __post_init__(self) -> None:
        if not self.command_id.strip():
            raise ValueError("command_id is required")
        if not self.account_alias.strip():
            raise ValueError("account_alias is required")
        if not self.venue.strip():
            raise ValueError("venue is required")
        if not self.client_order_label.strip():
            raise ValueError("client_order_label is required")
        if self.filled_volume is not None and self.filled_volume < 0:
            raise ValueError("filled_volume cannot be negative")
        if self.average_fill_price is not None and self.average_fill_price <= 0:
            raise ValueError("average_fill_price must be > 0 when supplied")
        for name, value in {
            "submitted_at_ms": self.submitted_at_ms,
            "acknowledged_at_ms": self.acknowledged_at_ms,
        }.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer when supplied")

    @property
    def protective_orders_ready(self) -> bool:
        return self.stop_protection_active and self.target_protection_active

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["broker_type"] = self.broker_type.value
        payload["outcome"] = self.outcome.value
        payload["account_scope"] = "BROKERAGE_ACCOUNTS"
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


class BrokerExecutionAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...

    @property
    def broker_type(self) -> BrokerType: ...

    @property
    def venue(self) -> str: ...

    def execution_capabilities(self) -> BrokerExecutionCapabilities: ...

    def submit_market(
        self,
        command: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt: ...

    def reconcile(
        self,
        command: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt: ...
