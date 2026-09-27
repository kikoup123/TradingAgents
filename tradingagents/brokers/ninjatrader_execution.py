"""NinjaTrader 8 Phase 33 execution adapter using local file IPC.

NinjaTrader order APIs execute inside a Windows NinjaTrader AddOn. The Python
agent writes an immutable claimed command to that AddOn's inbox and receives a
sanitized receipt. No NinjaTrader account credentials or demo/live label enters
public agent state.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from .contracts import BrokerType
from .execution import (
    BrokerExecutionCapabilities,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)
from .ninjatrader import NinjaTraderBridgeError


class NinjaTraderExecutionIPCError(RuntimeError):
    """Raised when the NinjaTrader execution bridge IPC cannot be trusted."""


class NinjaTraderFileExecutionTransport:
    def __init__(self, root: str | Path, *, timeout_seconds: float = 10.0) -> None:
        self.root = Path(root).expanduser()
        self.requests = self.root / "requests"
        self.responses = self.root / "responses"
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.timeout_seconds = float(timeout_seconds)

    def request(
        self,
        *,
        operation: str,
        command: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        command_id = str(command.get("command_id") or "")
        if not command_id:
            raise NinjaTraderExecutionIPCError("NinjaTrader command_id is required")
        op = operation.strip().upper()
        if op not in {"SUBMIT_MARKET", "RECONCILE"}:
            raise NinjaTraderExecutionIPCError("Unsupported NinjaTrader IPC operation")
        envelope = {
            "schema_version": 1,
            "operation": op,
            "command_id": command_id,
            "requested_at_ms": now_ms,
            "command": dict(command),
        }
        request_path = self.requests / f"{command_id}.{op}.json"
        response_path = self.responses / f"{command_id}.{op}.json"
        self._atomic_create_or_verify(request_path, envelope)
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if response_path.exists():
                return self._read_response(response_path, command_id=command_id, operation=op)
            time.sleep(0.05)
        raise TimeoutError("Timed out waiting for NinjaTrader execution bridge response")

    @staticmethod
    def _atomic_create_or_verify(path: Path, payload: dict[str, Any]) -> None:
        text = _canonical_json(payload)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != text:
                raise NinjaTraderExecutionIPCError(
                    "NinjaTrader command request path contains different payload"
                )
            return
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            if path.read_text(encoding="utf-8") != text:
                raise NinjaTraderExecutionIPCError(
                    "Concurrent NinjaTrader request conflict"
                ) from exc
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    @staticmethod
    def _read_response(path: Path, *, command_id: str, operation: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NinjaTraderExecutionIPCError("NinjaTrader execution response unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise NinjaTraderExecutionIPCError("Unsupported NinjaTrader execution response schema")
        if payload.get("command_id") != command_id or payload.get("operation") != operation:
            raise NinjaTraderExecutionIPCError("NinjaTrader execution response identity mismatch")
        receipt = payload.get("receipt")
        if not isinstance(receipt, dict):
            raise NinjaTraderExecutionIPCError("NinjaTrader execution response receipt missing")
        return receipt


class NinjaTraderExecutionAdapter:
    VENUE = "NINJATRADER"

    def __init__(
        self,
        *,
        account_alias: str,
        bridge_root: str | Path,
        execution_enabled: bool = False,
        adapter_id: str = "ninjatrader-execution",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not account_alias.strip():
            raise ValueError("account_alias is required")
        self._account_alias = account_alias
        self._execution_enabled = bool(execution_enabled)
        self._adapter_id = adapter_id
        self._transport = NinjaTraderFileExecutionTransport(
            bridge_root,
            timeout_seconds=timeout_seconds,
        )

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.NINJATRADER

    @property
    def venue(self) -> str:
        return self.VENUE

    def execution_capabilities(self) -> BrokerExecutionCapabilities:
        return BrokerExecutionCapabilities(
            broker_type=self.broker_type,
            venue=self.venue,
            execution_enabled=self._execution_enabled,
            supports_market_orders=True,
            supports_server_side_stop=True,
            supports_server_side_target=True,
            supports_reconciliation=True,
        )

    def submit_market(
        self,
        command: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        self._require_enabled(command)
        try:
            result = self._transport.request(
                operation="SUBMIT_MARKET",
                command=command,
                now_ms=now_ms,
            )
        except TimeoutError as exc:
            return self._receipt(
                command,
                outcome=BrokerExecutionOutcome.AMBIGUOUS,
                now_ms=now_ms,
                provider_code="NINJATRADER_EXECUTION_BRIDGE_TIMEOUT",
                provider_message=str(exc),
            )
        except NinjaTraderExecutionIPCError as exc:
            return self._receipt(
                command,
                outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
                now_ms=now_ms,
                provider_code="NINJATRADER_EXECUTION_IPC_INTEGRITY_ERROR",
                provider_message=str(exc),
            )
        return self._result_receipt(command, result=result, now_ms=now_ms)

    def reconcile(
        self,
        command: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        self._require_enabled(command)
        try:
            result = self._transport.request(
                operation="RECONCILE",
                command=command,
                now_ms=now_ms,
            )
        except (TimeoutError, NinjaTraderExecutionIPCError) as exc:
            return self._receipt(
                command,
                outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
                now_ms=now_ms,
                provider_code="NINJATRADER_RECONCILIATION_UNAVAILABLE",
                provider_message=str(exc),
            )
        return self._result_receipt(command, result=result, now_ms=now_ms)

    def _require_enabled(self, command: Mapping[str, Any]) -> None:
        if not self._execution_enabled:
            raise NinjaTraderBridgeError("NinjaTrader execution adapter is not explicitly enabled")
        if str(command.get("account_alias") or "") != self._account_alias:
            raise NinjaTraderBridgeError("NinjaTrader execution command account alias mismatch")
        if str(command.get("venue") or "") != self.venue:
            raise NinjaTraderBridgeError("NinjaTrader execution command venue mismatch")
        if str(command.get("broker_type") or "") != self.broker_type.value:
            raise NinjaTraderBridgeError("NinjaTrader execution command broker type mismatch")
        if str(command.get("volume_unit") or "") != "contracts":
            raise NinjaTraderBridgeError("NinjaTrader Phase 33 requires contract volume")
        volume = float(command.get("exact_volume") or 0.0)
        if volume <= 0 or not volume.is_integer():
            raise NinjaTraderBridgeError("NinjaTrader exact contract quantity must be integer")

    def _result_receipt(
        self,
        command: Mapping[str, Any],
        *,
        result: Mapping[str, Any],
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        raw = str(result.get("outcome") or "RECONCILIATION_REQUIRED")
        try:
            outcome = BrokerExecutionOutcome(raw)
        except ValueError:
            outcome = BrokerExecutionOutcome.RECONCILIATION_REQUIRED
        return self._receipt(
            command,
            outcome=outcome,
            now_ms=now_ms,
            broker_order_id=result.get("broker_order_id"),
            broker_position_id=result.get("broker_position_id"),
            filled_volume=(
                float(result["filled_volume"]) if result.get("filled_volume") is not None else None
            ),
            fill_price=(
                float(result["average_fill_price"])
                if result.get("average_fill_price") is not None
                else None
            ),
            stop_active=bool(result.get("stop_protection_active")),
            target_active=bool(result.get("target_protection_active")),
            provider_code=(str(result["provider_code"]) if result.get("provider_code") else None),
            provider_message=(
                str(result["provider_message"]) if result.get("provider_message") else None
            ),
            definite_no_fill=bool(result.get("definite_no_fill")),
        )

    def _receipt(
        self,
        command: Mapping[str, Any],
        *,
        outcome: BrokerExecutionOutcome,
        now_ms: int,
        broker_order_id: Any = None,
        broker_position_id: Any = None,
        filled_volume: float | None = None,
        fill_price: float | None = None,
        stop_active: bool = False,
        target_active: bool = False,
        provider_code: str | None = None,
        provider_message: str | None = None,
        definite_no_fill: bool = False,
    ) -> BrokerExecutionReceipt:
        return BrokerExecutionReceipt(
            command_id=str(command["command_id"]),
            account_alias=self._account_alias,
            venue=self.venue,
            broker_type=self.broker_type,
            client_order_label=str(command["client_order_label"]),
            outcome=outcome,
            broker_order_id=(str(broker_order_id) if broker_order_id is not None else None),
            broker_position_id=(
                str(broker_position_id) if broker_position_id is not None else None
            ),
            filled_volume=filled_volume,
            average_fill_price=fill_price,
            stop_protection_active=stop_active,
            target_protection_active=target_active,
            provider_code=provider_code,
            provider_message=provider_message,
            submitted_at_ms=now_ms,
            acknowledged_at_ms=(now_ms if outcome is BrokerExecutionOutcome.ACKNOWLEDGED else None),
            definite_no_fill=definite_no_fill,
        )


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
