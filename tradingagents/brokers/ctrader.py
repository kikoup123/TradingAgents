"""Production-facing hardened cTrader read-only facade.

This module wraps the Phase 18 JSON transport with event-safe response
correlation.  Unrelated asynchronous messages are parked while a synchronous
request waits for its own response, instead of being repeatedly re-consumed.
"""

from __future__ import annotations

import socket
import time
from typing import Any

from .ctrader_readonly import (
    CTraderAccountSnapshot,
    CTraderConnectionError,
    CTraderEnvironment,
    CTraderJsonReadOnlyTransport as _BaseJsonReadOnlyTransport,
    CTraderOAuthClient,
    CTraderQuoteSnapshot,
    CTraderReadOnlyConnector as _BaseReadOnlyConnector,
    CTraderReadOnlyError,
    CTraderReadOnlyTransport,
    CTraderSecretConfig,
    CTraderSymbolSnapshot,
    CTraderTokenSet,
)


class CTraderJsonReadOnlyTransport(_BaseJsonReadOnlyTransport):
    """Event-safe read-only JSON transport."""

    def _wait_for(
        self,
        *,
        expected: set[int],
        predicate: Any | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            pending_match = self._pop_pending_match(expected=expected, predicate=predicate)
            if pending_match is not None:
                return pending_match

            message = self._next_network_message(deadline)
            payload_type = int(message.get("payloadType", -1))
            if payload_type == self.ERROR_RES:
                self._raise_protocol_error(message)
            if payload_type in expected and (predicate is None or predicate(message)):
                return message
            self._pending.append(message)
        raise CTraderConnectionError("Timed out waiting for cTrader Open API response")

    def _pop_pending_match(
        self,
        *,
        expected: set[int],
        predicate: Any | None,
    ) -> dict[str, Any] | None:
        for index, message in enumerate(self._pending):
            payload_type = int(message.get("payloadType", -1))
            if payload_type == self.ERROR_RES:
                del self._pending[index]
                self._raise_protocol_error(message)
            if payload_type in expected and (predicate is None or predicate(message)):
                del self._pending[index]
                return message
        return None

    def _next_network_message(self, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            decoded = self._decode_buffer()
            if decoded is not None:
                return decoded
            if self._socket is None:
                raise CTraderConnectionError("cTrader transport disconnected")
            try:
                chunk = self._socket.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:
                raise CTraderConnectionError("Failed while receiving cTrader Open API data") from exc
            if not chunk:
                raise CTraderConnectionError("cTrader Open API connection closed by remote host")
            self._buffer += chunk.decode("utf-8")
        raise CTraderConnectionError("Timed out waiting for cTrader Open API data")

    @staticmethod
    def _raise_protocol_error(message: dict[str, Any]) -> None:
        error = message.get("payload") or {}
        code = str(error.get("errorCode") or "UNKNOWN")
        description = str(error.get("description") or "")
        detail = f": {description}" if description else ""
        raise CTraderReadOnlyError(f"cTrader Open API error {code}{detail}")


class CTraderReadOnlyConnector(_BaseReadOnlyConnector):
    """Public Phase 18 connector using the hardened JSON transport by default."""

    def __init__(
        self,
        config: CTraderSecretConfig,
        *,
        transport: CTraderReadOnlyTransport | None = None,
    ) -> None:
        super().__init__(
            config,
            transport=transport or CTraderJsonReadOnlyTransport(config),
        )


__all__ = [
    "CTraderAccountSnapshot",
    "CTraderConnectionError",
    "CTraderEnvironment",
    "CTraderJsonReadOnlyTransport",
    "CTraderOAuthClient",
    "CTraderQuoteSnapshot",
    "CTraderReadOnlyConnector",
    "CTraderReadOnlyError",
    "CTraderReadOnlyTransport",
    "CTraderSecretConfig",
    "CTraderSymbolSnapshot",
    "CTraderTokenSet",
]
