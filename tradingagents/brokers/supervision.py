"""Fail-closed broker connection and market-data supervision.

Phase 22 supervises a universal read-only broker adapter without adding any
order-submission capability. A supervision check is an application-level
heartbeat: it verifies connectivity, account readability, quote freshness and
broker-native tick-value freshness. Disconnected adapters may expose an optional
``reconnect`` method; if present, the supervisor can retry deterministically.

Freshness thresholds are explicit policy inputs. No quote age, reconnect count,
or valuation age is silently invented by the strategy engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .contracts import BrokerAdapter, canonicalize_symbol


class BrokerSupervisionStatus(str, Enum):
    HEALTHY = "HEALTHY"
    RECONNECTED = "RECONNECTED"
    DISCONNECTED = "DISCONNECTED"
    ADAPTER_ERROR = "ADAPTER_ERROR"
    WAIT_FOR_QUOTE = "WAIT_FOR_QUOTE"
    STALE_QUOTE = "STALE_QUOTE"
    WAIT_FOR_VERIFIED_TICK_VALUE = "WAIT_FOR_VERIFIED_TICK_VALUE"
    STALE_TICK_VALUE = "STALE_TICK_VALUE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"


@dataclass(frozen=True)
class BrokerSupervisionPolicy:
    """Explicit supervision thresholds supplied by the execution environment."""

    quote_max_age_ms: int
    tick_value_max_age_ms: int
    max_reconnect_attempts: int
    future_timestamp_tolerance_ms: int = 0

    def __post_init__(self) -> None:
        if self.quote_max_age_ms <= 0:
            raise ValueError("quote_max_age_ms must be > 0")
        if self.tick_value_max_age_ms <= 0:
            raise ValueError("tick_value_max_age_ms must be > 0")
        if self.max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts cannot be negative")
        if self.future_timestamp_tolerance_ms < 0:
            raise ValueError("future_timestamp_tolerance_ms cannot be negative")


@dataclass(frozen=True)
class BrokerSupervisionResult:
    status: BrokerSupervisionStatus
    adapter_id: str
    broker_type: str
    account_alias: str
    canonical_symbol: str
    broker_symbol: str
    masked_account: str | None
    account_currency: str | None
    connected: bool
    heartbeat_ok: bool
    heartbeat_timestamp_ms: int
    quote_bid: float | None
    quote_ask: float | None
    quote_timestamp_ms: int | None
    quote_age_ms: int | None
    tick_value_account_currency: float | None
    tick_value_currency: str | None
    tick_value_timestamp_ms: int | None
    tick_value_age_ms: int | None
    tick_value_source: str | None
    reconnect_attempted: bool
    reconnect_attempts: int
    reconnect_succeeded: bool
    execution_data_ready: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["account_environment"] = "HIDDEN_INTERNAL"
        payload["read_only"] = True
        payload["order_submission_enabled"] = False
        payload["order_authorized"] = False
        payload["broker_order_placed"] = False
        return payload


class BrokerConnectionSupervisor:
    """Supervise one broker/account/symbol data path and fail closed on staleness."""

    def check(
        self,
        *,
        adapter: BrokerAdapter,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
        now_ms: int,
        policy: BrokerSupervisionPolicy,
    ) -> BrokerSupervisionResult:
        if now_ms < 0:
            raise ValueError("now_ms cannot be negative")

        reconnect_attempts = 0
        reconnect_succeeded = False
        connected = self._public_connected(adapter)

        if not connected and policy.max_reconnect_attempts > 0:
            reconnect_attempts, reconnect_succeeded = self._try_reconnect(
                adapter=adapter,
                attempts=policy.max_reconnect_attempts,
            )
            connected = self._public_connected(adapter)

        if not connected:
            return self._result(
                status=BrokerSupervisionStatus.DISCONNECTED,
                adapter=adapter,
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
                now_ms=now_ms,
                connected=False,
                reconnect_attempts=reconnect_attempts,
                reconnect_succeeded=reconnect_succeeded,
                reason_codes=(
                    "BROKER_CONNECTION_NOT_AVAILABLE",
                    "EXECUTION_DATA_FAILS_CLOSED_WHILE_DISCONNECTED",
                ),
            )

        try:
            account = adapter.account_snapshot(account_alias)
            quote = adapter.quote_snapshot(
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
            )
            instrument = adapter.instrument_snapshot(
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
            )
        except Exception:
            if policy.max_reconnect_attempts > reconnect_attempts:
                extra_attempts, extra_success = self._try_reconnect(
                    adapter=adapter,
                    attempts=policy.max_reconnect_attempts - reconnect_attempts,
                )
                reconnect_attempts += extra_attempts
                reconnect_succeeded = reconnect_succeeded or extra_success
                if extra_success:
                    try:
                        account = adapter.account_snapshot(account_alias)
                        quote = adapter.quote_snapshot(
                            account_alias=account_alias,
                            canonical_symbol=canonical_symbol,
                            broker_symbol=broker_symbol,
                        )
                        instrument = adapter.instrument_snapshot(
                            account_alias=account_alias,
                            canonical_symbol=canonical_symbol,
                            broker_symbol=broker_symbol,
                        )
                    except Exception:
                        return self._adapter_error(
                            adapter=adapter,
                            account_alias=account_alias,
                            canonical_symbol=canonical_symbol,
                            broker_symbol=broker_symbol,
                            now_ms=now_ms,
                            reconnect_attempts=reconnect_attempts,
                            reconnect_succeeded=reconnect_succeeded,
                        )
                else:
                    return self._adapter_error(
                        adapter=adapter,
                        account_alias=account_alias,
                        canonical_symbol=canonical_symbol,
                        broker_symbol=broker_symbol,
                        now_ms=now_ms,
                        reconnect_attempts=reconnect_attempts,
                        reconnect_succeeded=reconnect_succeeded,
                    )
            else:
                return self._adapter_error(
                    adapter=adapter,
                    account_alias=account_alias,
                    canonical_symbol=canonical_symbol,
                    broker_symbol=broker_symbol,
                    now_ms=now_ms,
                    reconnect_attempts=reconnect_attempts,
                    reconnect_succeeded=reconnect_succeeded,
                )

        if not account.connected:
            return self._result(
                status=BrokerSupervisionStatus.DISCONNECTED,
                adapter=adapter,
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
                now_ms=now_ms,
                connected=False,
                masked_account=account.masked_account,
                account_currency=account.currency,
                reconnect_attempts=reconnect_attempts,
                reconnect_succeeded=reconnect_succeeded,
                reason_codes=("BROKER_ACCOUNT_SNAPSHOT_REPORTS_DISCONNECTED",),
            )

        if quote.bid is None or quote.ask is None or quote.timestamp_ms is None:
            return self._result(
                status=BrokerSupervisionStatus.WAIT_FOR_QUOTE,
                adapter=adapter,
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
                now_ms=now_ms,
                connected=True,
                masked_account=account.masked_account,
                account_currency=account.currency,
                quote_bid=quote.bid,
                quote_ask=quote.ask,
                quote_timestamp_ms=quote.timestamp_ms,
                instrument=instrument,
                reconnect_attempts=reconnect_attempts,
                reconnect_succeeded=reconnect_succeeded,
                reason_codes=("COMPLETE_TIMESTAMPED_BID_ASK_QUOTE_REQUIRED",),
            )

        quote_age = self._age_ms(
            now_ms=now_ms,
            timestamp_ms=quote.timestamp_ms,
            tolerance_ms=policy.future_timestamp_tolerance_ms,
        )
        if quote_age is None:
            return self._result(
                status=BrokerSupervisionStatus.INVALID_TIMESTAMP,
                adapter=adapter,
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
                now_ms=now_ms,
                connected=True,
                masked_account=account.masked_account,
                account_currency=account.currency,
                quote_bid=quote.bid,
                quote_ask=quote.ask,
                quote_timestamp_ms=quote.timestamp_ms,
                instrument=instrument,
                reconnect_attempts=reconnect_attempts,
                reconnect_succeeded=reconnect_succeeded,
                reason_codes=("BROKER_QUOTE_TIMESTAMP_IS_IN_THE_FUTURE_BEYOND_TOLERANCE",),
            )
        if quote_age > policy.quote_max_age_ms:
            return self._result(
                status=BrokerSupervisionStatus.STALE_QUOTE,
                adapter=adapter,
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
                now_ms=now_ms,
                connected=True,
                masked_account=account.masked_account,
                account_currency=account.currency,
                quote_bid=quote.bid,
                quote_ask=quote.ask,
                quote_timestamp_ms=quote.timestamp_ms,
                quote_age_ms=quote_age,
                instrument=instrument,
                reconnect_attempts=reconnect_attempts,
                reconnect_succeeded=reconnect_succeeded,
                reason_codes=("BROKER_QUOTE_EXCEEDS_EXPLICIT_MAX_AGE",),
            )

        if instrument.tick_value_account_currency is None:
            return self._result(
                status=BrokerSupervisionStatus.WAIT_FOR_VERIFIED_TICK_VALUE,
                adapter=adapter,
                account_alias=account_alias,
                canonical_symbol=canonical_symbol,
                broker_symbol=broker_symbol,
                now_ms=now_ms,
                connected=True,
                masked_account=account.masked_account,
                account_currency=account.currency,
                quote_bid=quote.bid,
                quote_ask=quote.ask,
                quote_timestamp_ms=quote.timestamp_ms,
                quote_age_ms=quote_age,
                instrument=instrument,
                reconnect_attempts=reconnect_attempts,
                reconnect_succeeded=reconnect_succeeded,
                reason_codes=("VERIFIED_ACCOUNT_CURRENCY_TICK_VALUE_REQUIRED",),
            )

        tick_age: int | None = None
        if instrument.tick_value_timestamp_ms is not None:
            tick_age = self._age_ms(
                now_ms=now_ms,
                timestamp_ms=instrument.tick_value_timestamp_ms,
                tolerance_ms=policy.future_timestamp_tolerance_ms,
            )
            if tick_age is None:
                return self._result(
                    status=BrokerSupervisionStatus.INVALID_TIMESTAMP,
                    adapter=adapter,
                    account_alias=account_alias,
                    canonical_symbol=canonical_symbol,
                    broker_symbol=broker_symbol,
                    now_ms=now_ms,
                    connected=True,
                    masked_account=account.masked_account,
                    account_currency=account.currency,
                    quote_bid=quote.bid,
                    quote_ask=quote.ask,
                    quote_timestamp_ms=quote.timestamp_ms,
                    quote_age_ms=quote_age,
                    instrument=instrument,
                    reconnect_attempts=reconnect_attempts,
                    reconnect_succeeded=reconnect_succeeded,
                    reason_codes=(
                        "BROKER_TICK_VALUE_TIMESTAMP_IS_IN_THE_FUTURE_BEYOND_TOLERANCE",
                    ),
                )
            if tick_age > policy.tick_value_max_age_ms:
                return self._result(
                    status=BrokerSupervisionStatus.STALE_TICK_VALUE,
                    adapter=adapter,
                    account_alias=account_alias,
                    canonical_symbol=canonical_symbol,
                    broker_symbol=broker_symbol,
                    now_ms=now_ms,
                    connected=True,
                    masked_account=account.masked_account,
                    account_currency=account.currency,
                    quote_bid=quote.bid,
                    quote_ask=quote.ask,
                    quote_timestamp_ms=quote.timestamp_ms,
                    quote_age_ms=quote_age,
                    tick_value_age_ms=tick_age,
                    instrument=instrument,
                    reconnect_attempts=reconnect_attempts,
                    reconnect_succeeded=reconnect_succeeded,
                    reason_codes=("BROKER_TICK_VALUE_EXCEEDS_EXPLICIT_MAX_AGE",),
                )

        final_status = (
            BrokerSupervisionStatus.RECONNECTED
            if reconnect_succeeded
            else BrokerSupervisionStatus.HEALTHY
        )
        reasons = [
            "BROKER_ACCOUNT_READABLE",
            "BROKER_QUOTE_FRESH",
            "BROKER_TICK_VALUE_VERIFIED",
            "SUPERVISION_HEARTBEAT_PASSED",
        ]
        if instrument.tick_value_timestamp_ms is None:
            reasons.append("TICK_VALUE_HAS_NO_MARKET_DEPENDENT_TIMESTAMP")
        if reconnect_succeeded:
            reasons.append("BROKER_CONNECTION_RECOVERED_BY_SUPERVISOR")

        return self._result(
            status=final_status,
            adapter=adapter,
            account_alias=account_alias,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            now_ms=now_ms,
            connected=True,
            masked_account=account.masked_account,
            account_currency=account.currency,
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            quote_timestamp_ms=quote.timestamp_ms,
            quote_age_ms=quote_age,
            tick_value_age_ms=tick_age,
            instrument=instrument,
            reconnect_attempts=reconnect_attempts,
            reconnect_succeeded=reconnect_succeeded,
            execution_data_ready=True,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _public_connected(adapter: BrokerAdapter) -> bool:
        try:
            return str(adapter.public_status().get("status") or "").upper() == "CONNECTED"
        except Exception:
            return False

    @staticmethod
    def _try_reconnect(*, adapter: BrokerAdapter, attempts: int) -> tuple[int, bool]:
        reconnect = getattr(adapter, "reconnect", None)
        if not callable(reconnect):
            return 0, False
        attempted = 0
        for _ in range(attempts):
            attempted += 1
            try:
                status = reconnect()
            except Exception:
                continue
            if str((status or {}).get("status") or "").upper() == "CONNECTED":
                return attempted, True
        return attempted, False

    @staticmethod
    def _age_ms(*, now_ms: int, timestamp_ms: int, tolerance_ms: int) -> int | None:
        timestamp = int(timestamp_ms)
        if timestamp > now_ms + tolerance_ms:
            return None
        return max(0, now_ms - timestamp)

    def _adapter_error(
        self,
        *,
        adapter: BrokerAdapter,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
        now_ms: int,
        reconnect_attempts: int,
        reconnect_succeeded: bool,
    ) -> BrokerSupervisionResult:
        return self._result(
            status=BrokerSupervisionStatus.ADAPTER_ERROR,
            adapter=adapter,
            account_alias=account_alias,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            now_ms=now_ms,
            connected=self._public_connected(adapter),
            reconnect_attempts=reconnect_attempts,
            reconnect_succeeded=reconnect_succeeded,
            reason_codes=(
                "BROKER_DATA_READ_FAILED_AFTER_ALLOWED_RECOVERY_ATTEMPTS",
                "EXECUTION_DATA_FAILS_CLOSED_ON_ADAPTER_ERROR",
            ),
        )

    @staticmethod
    def _result(
        *,
        status: BrokerSupervisionStatus,
        adapter: BrokerAdapter,
        account_alias: str,
        canonical_symbol: str,
        broker_symbol: str,
        now_ms: int,
        connected: bool,
        reconnect_attempts: int,
        reconnect_succeeded: bool,
        reason_codes: tuple[str, ...],
        masked_account: str | None = None,
        account_currency: str | None = None,
        quote_bid: float | None = None,
        quote_ask: float | None = None,
        quote_timestamp_ms: int | None = None,
        quote_age_ms: int | None = None,
        tick_value_age_ms: int | None = None,
        instrument: Any | None = None,
        execution_data_ready: bool = False,
    ) -> BrokerSupervisionResult:
        return BrokerSupervisionResult(
            status=status,
            adapter_id=adapter.adapter_id,
            broker_type=adapter.broker_type.value,
            account_alias=account_alias,
            canonical_symbol=canonicalize_symbol(canonical_symbol),
            broker_symbol=broker_symbol,
            masked_account=masked_account,
            account_currency=account_currency,
            connected=connected,
            heartbeat_ok=execution_data_ready,
            heartbeat_timestamp_ms=now_ms,
            quote_bid=quote_bid,
            quote_ask=quote_ask,
            quote_timestamp_ms=quote_timestamp_ms,
            quote_age_ms=quote_age_ms,
            tick_value_account_currency=(
                instrument.tick_value_account_currency if instrument is not None else None
            ),
            tick_value_currency=(instrument.tick_value_currency if instrument is not None else None),
            tick_value_timestamp_ms=(
                instrument.tick_value_timestamp_ms if instrument is not None else None
            ),
            tick_value_age_ms=tick_value_age_ms,
            tick_value_source=(instrument.tick_value_source if instrument is not None else None),
            reconnect_attempted=reconnect_attempts > 0,
            reconnect_attempts=reconnect_attempts,
            reconnect_succeeded=reconnect_succeeded,
            execution_data_ready=execution_data_ready,
            reason_codes=reason_codes,
        )
