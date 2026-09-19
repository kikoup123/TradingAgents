"""Secure read-only cTrader Open API connector.

Phase 18 starts with account and market-data access only.  It deliberately uses
OAuth ``accounts`` scope and refuses tokens that advertise trading permission.
The account environment (demo/live) is required internally for endpoint routing
but is omitted from all public/sanitised status payloads.

The JSON transport uses cTrader's TLS JSON endpoint on port 5036.  It implements
only read-only Open API message types.  No order, amend, close, or other trading
payload is exposed by this module.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlencode

import requests


class CTraderReadOnlyError(RuntimeError):
    """Base error for the fail-closed read-only cTrader connector."""


class CTraderConnectionError(CTraderReadOnlyError):
    """Raised when transport/authentication cannot establish a safe session."""


class CTraderEnvironment(str, Enum):
    LIVE = "live"
    DEMO = "demo"

    @property
    def json_host(self) -> str:
        return "live.ctraderapi.com" if self is CTraderEnvironment.LIVE else "demo.ctraderapi.com"


@dataclass(frozen=True)
class CTraderTokenSet:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "bearer"
    expires_in: int | None = None


@dataclass(frozen=True)
class CTraderSecretConfig:
    """Secret configuration loaded outside LLM-facing state.

    Environment and account id are also hidden from ``repr`` because ordinary
    logs should not reveal whether a connection is demo/live or expose a full
    account identifier.
    """

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    access_token: str = field(repr=False)
    account_id: int = field(repr=False)
    environment: CTraderEnvironment = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, *, prefix: str = "CTRADER_") -> CTraderSecretConfig:
        required = {
            "CLIENT_ID": os.getenv(f"{prefix}CLIENT_ID"),
            "CLIENT_SECRET": os.getenv(f"{prefix}CLIENT_SECRET"),
            "ACCESS_TOKEN": os.getenv(f"{prefix}ACCESS_TOKEN"),
            "ACCOUNT_ID": os.getenv(f"{prefix}ACCOUNT_ID"),
            "ENVIRONMENT": os.getenv(f"{prefix}ENVIRONMENT"),
        }
        missing = [name for name, value in required.items() if value is None or not str(value).strip()]
        if missing:
            raise CTraderReadOnlyError(
                "Missing required cTrader secret configuration: " + ", ".join(sorted(missing))
            )
        try:
            account_id = int(str(required["ACCOUNT_ID"]))
        except ValueError as exc:
            raise CTraderReadOnlyError("CTRADER_ACCOUNT_ID must be an integer") from exc
        try:
            environment = CTraderEnvironment(str(required["ENVIRONMENT"]).strip().lower())
        except ValueError as exc:
            raise CTraderReadOnlyError("CTRADER_ENVIRONMENT must be 'demo' or 'live'") from exc

        return cls(
            client_id=str(required["CLIENT_ID"]),
            client_secret=str(required["CLIENT_SECRET"]),
            access_token=str(required["ACCESS_TOKEN"]),
            account_id=account_id,
            environment=environment,
            refresh_token=os.getenv(f"{prefix}REFRESH_TOKEN"),
        )


@dataclass(frozen=True)
class CTraderAccountSnapshot:
    broker: str | None
    masked_account: str
    currency: str | None
    balance: float
    equity: float
    used_margin: float
    free_margin: float
    money_digits: int
    connected: bool = True

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = "CONNECTED" if self.connected else "DISCONNECTED"
        payload["account_environment"] = "HIDDEN_INTERNAL"
        return payload


@dataclass(frozen=True)
class CTraderSymbolSnapshot:
    symbol_id: int
    symbol: str
    digits: int
    pip_position: int
    min_volume_protocol: int
    max_volume_protocol: int
    step_volume_protocol: int
    sl_distance: int | None
    tp_distance: int | None
    distance_set_in: int | str | None

    @property
    def min_volume_units(self) -> float:
        return self.min_volume_protocol / 100.0

    @property
    def max_volume_units(self) -> float:
        return self.max_volume_protocol / 100.0

    @property
    def step_volume_units(self) -> float:
        return self.step_volume_protocol / 100.0

    @property
    def display_tick_size(self) -> float:
        return 10.0 ** (-self.digits)

    @property
    def pip_size(self) -> float:
        return 10.0 ** (-self.pip_position)


@dataclass(frozen=True)
class CTraderQuoteSnapshot:
    symbol_id: int
    symbol: str
    bid: float | None
    ask: float | None
    timestamp_ms: int | None


class CTraderReadOnlyTransport(Protocol):
    """Minimal transport contract used by the sanitized connector facade."""

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def authenticate_read_only(self, config: CTraderSecretConfig) -> dict[str, Any]: ...

    def read_account(self, account_id: int) -> CTraderAccountSnapshot: ...

    def read_symbol(self, account_id: int, symbol: str) -> CTraderSymbolSnapshot: ...

    def read_quote(self, account_id: int, symbol: str) -> CTraderQuoteSnapshot: ...


class CTraderOAuthClient:
    """OAuth helper that always requests view-only ``accounts`` scope."""

    AUTHORIZE_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
    TOKEN_URL = "https://openapi.ctrader.com/apps/token"

    def __init__(self, *, session: requests.Session | None = None, timeout: float = 15.0) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout

    @classmethod
    def build_authorization_url(cls, *, client_id: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "accounts",
                "product": "web",
            }
        )
        return f"{cls.AUTHORIZE_URL}?{query}"

    def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> CTraderTokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )

    def refresh(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> CTraderTokenSet:
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )

    def _token_request(self, params: dict[str, str]) -> CTraderTokenSet:
        try:
            response = self._session.get(self.TOKEN_URL, params=params, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CTraderConnectionError("cTrader OAuth token request failed") from exc

        if payload.get("errorCode"):
            raise CTraderConnectionError(
                f"cTrader OAuth rejected the request: {payload.get('errorCode')}"
            )
        access_token = payload.get("accessToken")
        if not access_token:
            raise CTraderConnectionError("cTrader OAuth response did not contain an access token")
        return CTraderTokenSet(
            access_token=str(access_token),
            refresh_token=(str(payload["refreshToken"]) if payload.get("refreshToken") else None),
            token_type=str(payload.get("tokenType") or "bearer"),
            expires_in=(int(payload["expiresIn"]) if payload.get("expiresIn") is not None else None),
        )


class CTraderJsonReadOnlyTransport:
    """Synchronous TLS JSON transport restricted to view-only Open API messages."""

    JSON_PORT = 5036
    ERROR_RES = 2142

    def __init__(
        self,
        config: CTraderSecretConfig,
        *,
        timeout: float = 10.0,
        socket_factory: Any = socket.create_connection,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._socket_factory = socket_factory
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._socket: ssl.SSLSocket | None = None
        self._decoder = json.JSONDecoder()
        self._buffer = ""
        self._pending: list[dict[str, Any]] = []
        self._account_descriptor: dict[str, Any] | None = None
        self._symbol_ids: dict[str, int] = {}

    def connect(self) -> None:
        if self._socket is not None:
            return
        host = self._config.environment.json_host
        try:
            raw = self._socket_factory((host, self.JSON_PORT), timeout=self._timeout)
            raw.settimeout(self._timeout)
            self._socket = self._ssl_context.wrap_socket(raw, server_hostname=host)
        except (OSError, ssl.SSLError) as exc:
            raise CTraderConnectionError("Unable to establish TLS connection to cTrader Open API") from exc

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
                self._buffer = ""
                self._pending.clear()

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
        if permission not in (None, 0, "0", "SCOPE_VIEW"):
            raise CTraderReadOnlyError(
                "Read-only connector refuses an access token with trading permission"
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
        is_live = bool(descriptor.get("isLive"))
        expected_live = config.environment is CTraderEnvironment.LIVE
        if is_live != expected_live:
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

    def read_account(self, account_id: int) -> CTraderAccountSnapshot:
        self._require_account(account_id)
        trader_message = self._request(
            2121,
            {"ctidTraderAccountId": account_id},
            expected={2122},
        )
        trader = (trader_message.get("payload") or {}).get("trader") or {}
        money_digits_raw = trader.get("moneyDigits")
        if money_digits_raw is None:
            raise CTraderReadOnlyError("Trader response is missing moneyDigits; refusing to guess balance scale")
        money_digits = int(money_digits_raw)
        scale = 10.0**money_digits
        balance = float(trader["balance"]) / scale

        pnl_message = self._request(
            2187,
            {"ctidTraderAccountId": account_id},
            expected={2188},
        )
        pnl_payload = pnl_message.get("payload") or {}
        pnl_digits_raw = pnl_payload.get("moneyDigits")
        if pnl_digits_raw is None:
            raise CTraderReadOnlyError("Unrealized PnL response is missing moneyDigits")
        pnl_scale = 10.0 ** int(pnl_digits_raw)
        net_unrealized = sum(
            float(position.get("netUnrealizedPnL", 0)) / pnl_scale
            for position in pnl_payload.get("positionUnrealizedPnL") or []
        )

        reconcile = self._request(
            2124,
            {"ctidTraderAccountId": account_id},
            expected={2125},
        )
        positions = (reconcile.get("payload") or {}).get("position") or []
        used_margin = 0.0
        for position in positions:
            raw_margin = position.get("usedMargin")
            if raw_margin is None:
                continue
            position_digits = position.get("moneyDigits")
            if position_digits is None:
                raise CTraderReadOnlyError(
                    "Open position has usedMargin without moneyDigits; refusing to guess margin scale"
                )
            used_margin += float(raw_margin) / (10.0 ** int(position_digits))

        assets_message = self._request(
            2112,
            {"ctidTraderAccountId": account_id},
            expected={2113},
        )
        assets = (assets_message.get("payload") or {}).get("asset") or []
        deposit_asset_id = int(trader.get("depositAssetId", -1))
        currency = next(
            (str(asset.get("name")) for asset in assets if int(asset.get("assetId", -2)) == deposit_asset_id),
            None,
        )
        equity = balance + net_unrealized
        free_margin = equity - used_margin
        descriptor = self._account_descriptor or {}
        return CTraderAccountSnapshot(
            broker=(str(descriptor.get("brokerTitleShort")) if descriptor.get("brokerTitleShort") else None),
            masked_account=_mask_account(descriptor.get("traderLogin") or account_id),
            currency=currency,
            balance=balance,
            equity=equity,
            used_margin=used_margin,
            free_margin=free_margin,
            money_digits=money_digits,
        )

    def read_symbol(self, account_id: int, symbol: str) -> CTraderSymbolSnapshot:
        self._require_account(account_id)
        symbol_id, canonical = self._resolve_symbol(account_id, symbol)
        response = self._request(
            2116,
            {"ctidTraderAccountId": account_id, "symbolId": [symbol_id]},
            expected={2117},
        )
        symbols = (response.get("payload") or {}).get("symbol") or []
        if not symbols:
            raise CTraderReadOnlyError(f"No full cTrader symbol definition returned for {symbol!r}")
        item = symbols[0]
        required = ("digits", "pipPosition", "minVolume", "maxVolume", "stepVolume")
        missing = [name for name in required if item.get(name) is None]
        if missing:
            raise CTraderReadOnlyError(
                f"cTrader symbol definition missing required fields: {', '.join(missing)}"
            )
        return CTraderSymbolSnapshot(
            symbol_id=symbol_id,
            symbol=canonical,
            digits=int(item["digits"]),
            pip_position=int(item["pipPosition"]),
            min_volume_protocol=int(item["minVolume"]),
            max_volume_protocol=int(item["maxVolume"]),
            step_volume_protocol=int(item["stepVolume"]),
            sl_distance=(int(item["slDistance"]) if item.get("slDistance") is not None else None),
            tp_distance=(int(item["tpDistance"]) if item.get("tpDistance") is not None else None),
            distance_set_in=item.get("distanceSetIn"),
        )

    def read_quote(self, account_id: int, symbol: str) -> CTraderQuoteSnapshot:
        self._require_account(account_id)
        symbol_id, canonical = self._resolve_symbol(account_id, symbol)
        self._request(
            2127,
            {
                "ctidTraderAccountId": account_id,
                "symbolId": [symbol_id],
                "subscribeToSpotTimestamp": True,
            },
            expected={2128},
        )
        event = self._wait_for(
            expected={2131},
            predicate=lambda message: int((message.get("payload") or {}).get("symbolId", -1))
            == symbol_id,
        )
        payload = event.get("payload") or {}
        bid = float(payload["bid"]) / 100000.0 if payload.get("bid") is not None else None
        ask = float(payload["ask"]) / 100000.0 if payload.get("ask") is not None else None
        timestamp = int(payload["timestamp"]) if payload.get("timestamp") is not None else None
        return CTraderQuoteSnapshot(
            symbol_id=symbol_id,
            symbol=canonical,
            bid=bid,
            ask=ask,
            timestamp_ms=timestamp,
        )

    def _resolve_symbol(self, account_id: int, symbol: str) -> tuple[int, str]:
        normalized = _normalize_symbol(symbol)
        cached = self._symbol_ids.get(normalized)
        if cached is not None:
            return cached, symbol
        response = self._request(
            2114,
            {"ctidTraderAccountId": account_id, "includeArchivedSymbols": False},
            expected={2115},
        )
        for item in (response.get("payload") or {}).get("symbol") or []:
            name = str(item.get("symbolName") or "")
            if not name:
                continue
            self._symbol_ids[_normalize_symbol(name)] = int(item["symbolId"])
        resolved = self._symbol_ids.get(normalized)
        if resolved is None:
            raise CTraderReadOnlyError(f"Symbol {symbol!r} is not available on the connected account")
        canonical = next(
            (
                str(item.get("symbolName"))
                for item in (response.get("payload") or {}).get("symbol") or []
                if int(item.get("symbolId", -1)) == resolved
            ),
            symbol,
        )
        return resolved, canonical

    def _require_account(self, account_id: int) -> None:
        if self._account_descriptor is None:
            raise CTraderReadOnlyError("cTrader account is not authenticated")
        if account_id != self._config.account_id:
            raise CTraderReadOnlyError("Read request attempted to use an unexpected account")

    def _request(
        self,
        payload_type: int,
        payload: dict[str, Any],
        *,
        expected: set[int],
    ) -> dict[str, Any]:
        message_id = str(uuid.uuid4())
        self._send(
            {
                "clientMsgId": message_id,
                "payloadType": payload_type,
                "payload": payload,
            }
        )
        return self._wait_for(
            expected=expected,
            predicate=lambda message: message.get("clientMsgId") in (None, message_id),
        )

    def _send(self, message: dict[str, Any]) -> None:
        if self._socket is None:
            raise CTraderConnectionError("cTrader transport is not connected")
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
        try:
            self._socket.sendall(encoded)
        except OSError as exc:
            raise CTraderConnectionError("Failed to send cTrader Open API message") from exc

    def _wait_for(
        self,
        *,
        expected: set[int],
        predicate: Any | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            message = self._next_message(deadline)
            payload_type = int(message.get("payloadType", -1))
            if payload_type == self.ERROR_RES:
                error = message.get("payload") or {}
                code = str(error.get("errorCode") or "UNKNOWN")
                description = str(error.get("description") or "")
                detail = f": {description}" if description else ""
                raise CTraderReadOnlyError(f"cTrader Open API error {code}{detail}")
            if payload_type in expected and (predicate is None or predicate(message)):
                return message
            self._pending.append(message)
        raise CTraderConnectionError("Timed out waiting for cTrader Open API response")

    def _next_message(self, deadline: float) -> dict[str, Any]:
        for index, message in enumerate(tuple(self._pending)):
            del self._pending[index]
            return message

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

    def _decode_buffer(self) -> dict[str, Any] | None:
        text = self._buffer.lstrip()
        if not text:
            self._buffer = ""
            return None
        try:
            value, end = self._decoder.raw_decode(text)
        except json.JSONDecodeError:
            return None
        self._buffer = text[end:]
        if not isinstance(value, dict):
            raise CTraderConnectionError("Unexpected non-object JSON message from cTrader")
        return value


class CTraderReadOnlyConnector:
    """Sanitized facade used by TradingAgents.

    LLM-facing consumers receive only masked account identity and account/market
    values.  They never receive access tokens, client secrets, full account ids,
    endpoint hostnames, or the demo/live environment classification.
    """

    def __init__(
        self,
        config: CTraderSecretConfig,
        *,
        transport: CTraderReadOnlyTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or CTraderJsonReadOnlyTransport(config)
        self._connected = False
        self._connection_public: dict[str, Any] = {
            "provider": "cTrader Open API",
            "status": "DISCONNECTED",
            "account": _mask_account(config.account_id),
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def connect(self) -> dict[str, Any]:
        self._transport.connect()
        authenticated = self._transport.authenticate_read_only(self._config)
        self._connected = True
        self._connection_public = {
            "provider": "cTrader Open API",
            "broker": authenticated.get("broker"),
            "status": "CONNECTED",
            "account": authenticated.get("masked_account") or _mask_account(self._config.account_id),
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "oauth_scope": "accounts",
            "order_submission_enabled": False,
        }
        return dict(self._connection_public)

    def close(self) -> None:
        self._transport.close()
        self._connected = False
        self._connection_public["status"] = "DISCONNECTED"

    def public_status(self) -> dict[str, Any]:
        return dict(self._connection_public)

    def account_snapshot(self) -> dict[str, Any]:
        self._require_connected()
        return self._transport.read_account(self._config.account_id).public_dict()

    def symbol_snapshot(self, symbol: str) -> dict[str, Any]:
        self._require_connected()
        return asdict(self._transport.read_symbol(self._config.account_id, symbol))

    def quote_snapshot(self, symbol: str) -> dict[str, Any]:
        self._require_connected()
        return asdict(self._transport.read_quote(self._config.account_id, symbol))

    def _require_connected(self) -> None:
        if not self._connected:
            raise CTraderReadOnlyError("cTrader read-only connector is not connected")


def _mask_account(value: Any) -> str:
    text = str(value)
    if not text:
        return "••••"
    suffix = text[-4:]
    return f"••••{suffix}"


def _normalize_symbol(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())
