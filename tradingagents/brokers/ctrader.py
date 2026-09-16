"""Production-facing hardened cTrader read-only facade.

This module wraps the Phase 18 JSON transport with event-safe response
correlation. Unrelated asynchronous messages are parked while a synchronous
request waits for its own response, instead of being repeatedly re-consumed.

Phase 21 also resolves cTrader tick value per volume unit in the account deposit
currency from broker-provided symbol geometry, conversion chains and live
bid/ask events. It remains read-only and exposes no order submission method.
"""

from __future__ import annotations

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
    _mask_account,
)
from .ctrader_valuation import CTraderConversionLeg, CTraderTickValueSnapshot, resolve_linear_tick_value


class CTraderJsonReadOnlyTransport(_BaseJsonReadOnlyTransport):
    """Event-safe read-only JSON transport with strict scope verification."""

    def authenticate_read_only(self, config: CTraderSecretConfig) -> dict[str, Any]:
        if config is not self._config and config != self._config:
            raise CTraderReadOnlyError("Transport configuration mismatch")
        self.connect()
        self._request(
            2100,
            {"clientId": config.client_id, "clientSecret": config.client_secret},
            expected={2101},
        )
        account_list = self._request(
            2149,
            {"accessToken": config.access_token},
            expected={2150},
        )
        payload = account_list.get("payload") or {}
        permission = payload.get("permissionScope")
        if permission not in (0, "0", "SCOPE_VIEW"):
            raise CTraderReadOnlyError(
                "Read-only connector requires an explicitly verified view-only permission scope"
            )

        accounts = payload.get("ctidTraderAccount") or []
        descriptor = next(
            (
                item
                for item in accounts
                if int(item.get("ctidTraderAccountId", -1)) == config.account_id
            ),
            None,
        )
        if descriptor is None:
            raise CTraderReadOnlyError("Configured account is not granted to this access token")
        expected_live = config.environment is CTraderEnvironment.LIVE
        if bool(descriptor.get("isLive")) != expected_live:
            raise CTraderReadOnlyError("Configured cTrader endpoint does not match the account environment")

        auth = self._request(
            2102,
            {
                "ctidTraderAccountId": config.account_id,
                "accessToken": config.access_token,
            },
            expected={2103},
        )
        if int((auth.get("payload") or {}).get("ctidTraderAccountId", -1)) != config.account_id:
            raise CTraderReadOnlyError("cTrader authenticated an unexpected account")
        self._account_descriptor = dict(descriptor)
        return {
            "connected": True,
            "broker": descriptor.get("brokerTitleShort"),
            "masked_account": _mask_account(descriptor.get("traderLogin") or config.account_id),
        }

    def read_tick_value(self, account_id: int, symbol: str) -> CTraderTickValueSnapshot:
        """Resolve one-tick cash risk for one cTrader volume unit.

        Open API symbol volume is expressed in hundredths of a unit. The public
        connector normalizes it back to units, so the risk engine needs the cash
        value of one display tick for exactly one such unit. For a linear cTrader
        symbol this is ``tick_size`` in quote-asset terms, converted into the
        account deposit asset through cTrader's own conversion chain.
        """

        self._require_account(account_id)
        symbol_snapshot = self.read_symbol(account_id, symbol)

        trader_message = self._request(
            2121,
            {"ctidTraderAccountId": account_id},
            expected={2122},
        )
        trader = (trader_message.get("payload") or {}).get("trader") or {}
        deposit_asset_raw = trader.get("depositAssetId")
        if deposit_asset_raw is None:
            raise CTraderReadOnlyError("Trader response is missing depositAssetId")
        deposit_asset_id = int(deposit_asset_raw)

        assets_message = self._request(
            2112,
            {"ctidTraderAccountId": account_id},
            expected={2113},
        )
        assets = (assets_message.get("payload") or {}).get("asset") or []
        account_currency = next(
            (
                str(asset.get("name"))
                for asset in assets
                if int(asset.get("assetId", -1)) == deposit_asset_id
            ),
            None,
        )
        if not account_currency:
            raise CTraderReadOnlyError("Account deposit asset name is unavailable")

        symbols_message = self._request(
            2114,
            {"ctidTraderAccountId": account_id, "includeArchivedSymbols": False},
            expected={2115},
        )
        light_symbols = (symbols_message.get("payload") or {}).get("symbol") or []
        light_symbol = next(
            (
                item
                for item in light_symbols
                if int(item.get("symbolId", -1)) == symbol_snapshot.symbol_id
            ),
            None,
        )
        if light_symbol is None:
            raise CTraderReadOnlyError("Resolved symbol is missing from cTrader light-symbol list")
        quote_asset_raw = light_symbol.get("quoteAssetId")
        if quote_asset_raw is None:
            raise CTraderReadOnlyError("cTrader light symbol is missing quoteAssetId")
        quote_asset_id = int(quote_asset_raw)

        legs: list[CTraderConversionLeg] = []
        if quote_asset_id != deposit_asset_id:
            conversion_message = self._request(
                2118,
                {
                    "ctidTraderAccountId": account_id,
                    "firstAssetId": quote_asset_id,
                    "lastAssetId": deposit_asset_id,
                },
                expected={2119},
            )
            conversion_symbols = (conversion_message.get("payload") or {}).get("symbol") or []
            if not conversion_symbols:
                raise CTraderReadOnlyError("cTrader returned no conversion chain for tick valuation")

            conversion_ids = [int(item["symbolId"]) for item in conversion_symbols]
            self._request(
                2127,
                {
                    "ctidTraderAccountId": account_id,
                    "symbolId": conversion_ids,
                    "subscribeToSpotTimestamp": True,
                },
                expected={2128},
            )
            for item in conversion_symbols:
                symbol_id = int(item["symbolId"])
                base_raw = item.get("baseAssetId")
                quote_raw = item.get("quoteAssetId")
                if base_raw is None or quote_raw is None:
                    raise CTraderReadOnlyError(
                        "cTrader conversion symbol is missing baseAssetId or quoteAssetId"
                    )
                event = self._wait_for(
                    expected={2131},
                    predicate=lambda message, sid=symbol_id: int(
                        (message.get("payload") or {}).get("symbolId", -1)
                    )
                    == sid,
                )
                payload = event.get("payload") or {}
                bid_raw = payload.get("bid")
                ask_raw = payload.get("ask")
                if bid_raw is None or ask_raw is None:
                    raise CTraderReadOnlyError(
                        "cTrader conversion spot event requires both bid and ask"
                    )
                legs.append(
                    CTraderConversionLeg(
                        symbol_id=symbol_id,
                        symbol=str(item.get("symbolName") or symbol_id),
                        base_asset_id=int(base_raw),
                        quote_asset_id=int(quote_raw),
                        bid=float(bid_raw) / 100000.0,
                        ask=float(ask_raw) / 100000.0,
                        timestamp_ms=(
                            int(payload["timestamp"]) if payload.get("timestamp") is not None else None
                        ),
                    )
                )

        try:
            return resolve_linear_tick_value(
                symbol=symbol_snapshot.symbol,
                account_currency=account_currency,
                tick_size=symbol_snapshot.display_tick_size,
                quote_asset_id=quote_asset_id,
                deposit_asset_id=deposit_asset_id,
                conversion_legs=legs,
            )
        except ValueError as exc:
            raise CTraderReadOnlyError("Unable to verify cTrader account-currency tick value") from exc

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
            except TimeoutError:
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
    """Public read-only connector using the hardened JSON transport by default."""

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

    def tick_value_snapshot(self, symbol: str) -> dict[str, Any]:
        """Return sanitized, broker-verified tick valuation metadata."""

        self._require_connected()
        resolver = getattr(self._transport, "read_tick_value", None)
        if resolver is None:
            raise CTraderReadOnlyError("Configured cTrader transport cannot resolve tick value")
        snapshot = resolver(self._config.account_id, symbol)
        if not isinstance(snapshot, CTraderTickValueSnapshot):
            raise CTraderReadOnlyError("cTrader transport returned an invalid tick-value snapshot")
        return snapshot.public_dict()


__all__ = [
    "CTraderAccountSnapshot",
    "CTraderConnectionError",
    "CTraderConversionLeg",
    "CTraderEnvironment",
    "CTraderJsonReadOnlyTransport",
    "CTraderOAuthClient",
    "CTraderQuoteSnapshot",
    "CTraderReadOnlyConnector",
    "CTraderReadOnlyError",
    "CTraderReadOnlyTransport",
    "CTraderSecretConfig",
    "CTraderSymbolSnapshot",
    "CTraderTickValueSnapshot",
    "CTraderTokenSet",
]
