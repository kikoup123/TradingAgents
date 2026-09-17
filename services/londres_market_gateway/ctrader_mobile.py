from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel


def _load_standalone_ctrader_readonly() -> Any:
    """Load the hardened connector without executing tradingagents.brokers.__init__.

    The broker package currently re-exports execution and risk modules eagerly,
    which creates a circular import when a lightweight gateway process imports
    only the read-only cTrader transport. The connector itself is standalone and
    depends only on the standard library plus requests, so loading that source
    file directly keeps this service isolated from execution code.
    """

    module_path = (
        Path(__file__).resolve().parents[2]
        / "tradingagents"
        / "brokers"
        / "ctrader_readonly.py"
    )
    module_name = "_londres_ctrader_readonly_standalone"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    spec = spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the read-only cTrader connector")
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


_ctrader = _load_standalone_ctrader_readonly()
CTraderConnectionError = _ctrader.CTraderConnectionError
CTraderEnvironment = _ctrader.CTraderEnvironment
CTraderJsonReadOnlyTransport = _ctrader.CTraderJsonReadOnlyTransport
CTraderOAuthClient = _ctrader.CTraderOAuthClient
CTraderReadOnlyConnector = _ctrader.CTraderReadOnlyConnector
CTraderReadOnlyError = _ctrader.CTraderReadOnlyError
CTraderSecretConfig = _ctrader.CTraderSecretConfig
CTraderTokenSet = _ctrader.CTraderTokenSet

router = APIRouter(prefix="/v1/brokers/ctrader", tags=["cTrader"])

_FLOW_COOKIE = "londres_ctrader_oauth_flow"
_HANDOFF_TTL_SECONDS = 120
_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 180
_SCHEME_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*$")


class CTraderMobileConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CTraderMobileConfiguration:
    client_id: str
    client_secret: str
    redirect_uri: str
    session_secret: str
    ios_callback_scheme: str = "londrestradingai"

    @classmethod
    def from_env(cls) -> CTraderMobileConfiguration:
        values = {
            "CTRADER_CLIENT_ID": os.getenv("CTRADER_CLIENT_ID", "").strip(),
            "CTRADER_CLIENT_SECRET": os.getenv("CTRADER_CLIENT_SECRET", "").strip(),
            "CTRADER_REDIRECT_URI": os.getenv("CTRADER_REDIRECT_URI", "").strip(),
            "CTRADER_SESSION_SECRET": os.getenv("CTRADER_SESSION_SECRET", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise CTraderMobileConfigurationError(
                "Missing cTrader mobile gateway configuration: " + ", ".join(sorted(missing))
            )

        redirect = urlparse(values["CTRADER_REDIRECT_URI"])
        is_local_http = redirect.scheme == "http" and redirect.hostname in {
            "127.0.0.1",
            "localhost",
        }
        if redirect.scheme != "https" and not is_local_http:
            raise CTraderMobileConfigurationError(
                "CTRADER_REDIRECT_URI must use HTTPS outside localhost development"
            )
        if not redirect.netloc:
            raise CTraderMobileConfigurationError("CTRADER_REDIRECT_URI must be an absolute URL")

        scheme = os.getenv("CTRADER_IOS_CALLBACK_SCHEME", "londrestradingai").strip().lower()
        if not _SCHEME_PATTERN.fullmatch(scheme):
            raise CTraderMobileConfigurationError("CTRADER_IOS_CALLBACK_SCHEME is invalid")
        if len(values["CTRADER_SESSION_SECRET"]) < 32:
            raise CTraderMobileConfigurationError(
                "CTRADER_SESSION_SECRET must contain at least 32 characters of random secret material"
            )

        return cls(
            client_id=values["CTRADER_CLIENT_ID"],
            client_secret=values["CTRADER_CLIENT_SECRET"],
            redirect_uri=values["CTRADER_REDIRECT_URI"],
            session_secret=values["CTRADER_SESSION_SECRET"],
            ios_callback_scheme=scheme,
        )


@dataclass(frozen=True)
class CTraderAuthorizedAccount:
    account_id: int
    trader_login: str
    broker: str | None
    environment: Any


@dataclass(frozen=True)
class CTraderBrokerSession:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_in: int | None
    issued_at: int
    accounts: tuple[CTraderAuthorizedAccount, ...]


class CTraderBrokerSessionCodec:
    """Encrypt broker credentials into an opaque value persisted by iOS Keychain."""

    def __init__(self, secret: str) -> None:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)
        self._account_key_secret = hashlib.sha256(
            ("account-key:" + secret).encode("utf-8")
        ).digest()

    def seal_flow(self) -> str:
        payload = {"v": 1, "iat": int(time.time()), "nonce": secrets.token_urlsafe(24)}
        return self._fernet.encrypt(_json_bytes(payload)).decode("ascii")

    def verify_flow(self, value: str) -> None:
        try:
            raw = self._fernet.decrypt(value.encode("ascii"), ttl=600)
            payload = json.loads(raw)
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CTraderReadOnlyError("Invalid or expired OAuth flow") from exc
        if payload.get("v") != 1 or not payload.get("nonce"):
            raise CTraderReadOnlyError("Invalid OAuth flow payload")

    def seal_session(self, session: CTraderBrokerSession) -> str:
        payload = {
            "v": 1,
            "iat": session.issued_at,
            "accessToken": session.access_token,
            "refreshToken": session.refresh_token,
            "tokenType": session.token_type,
            "expiresIn": session.expires_in,
            "accounts": [
                {
                    "accountId": account.account_id,
                    "traderLogin": account.trader_login,
                    "broker": account.broker,
                    "environment": account.environment.value,
                }
                for account in session.accounts
            ],
        }
        return self._fernet.encrypt(_json_bytes(payload)).decode("ascii")

    def open_session(self, token: str) -> CTraderBrokerSession:
        try:
            raw = self._fernet.decrypt(
                token.encode("ascii"),
                ttl=_SESSION_MAX_AGE_SECONDS,
            )
            payload = json.loads(raw)
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CTraderReadOnlyError("Invalid or expired broker session") from exc

        if payload.get("v") != 1:
            raise CTraderReadOnlyError("Unsupported broker session version")
        accounts_payload = payload.get("accounts")
        if not isinstance(accounts_payload, list):
            raise CTraderReadOnlyError("Broker session account list is malformed")

        accounts: list[CTraderAuthorizedAccount] = []
        try:
            for item in accounts_payload:
                accounts.append(
                    CTraderAuthorizedAccount(
                        account_id=int(item["accountId"]),
                        trader_login=str(item["traderLogin"]),
                        broker=(str(item["broker"]) if item.get("broker") else None),
                        environment=CTraderEnvironment(str(item["environment"])),
                    )
                )
            return CTraderBrokerSession(
                access_token=str(payload["accessToken"]),
                refresh_token=(
                    str(payload["refreshToken"]) if payload.get("refreshToken") else None
                ),
                token_type=str(payload.get("tokenType") or "bearer"),
                expires_in=(
                    int(payload["expiresIn"]) if payload.get("expiresIn") is not None else None
                ),
                issued_at=int(payload["iat"]),
                accounts=tuple(accounts),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CTraderReadOnlyError("Broker session payload is malformed") from exc

    def account_key(self, account: CTraderAuthorizedAccount) -> str:
        message = f"{account.environment.value}:{account.account_id}".encode()
        digest = hmac.new(self._account_key_secret, message, hashlib.sha256).digest()[:18]
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def public_accounts(self, session: CTraderBrokerSession) -> list[dict[str, Any]]:
        return [
            {
                "accountKey": self.account_key(account),
                "broker": account.broker,
                "account": _mask_account(account.trader_login),
                "readOnly": True,
                "oauthScope": "accounts",
                "orderSubmissionEnabled": False,
            }
            for account in session.accounts
        ]


class _CTraderAccountDiscoveryTransport(CTraderJsonReadOnlyTransport):
    """Read-only account discovery before a concrete account has been selected."""

    def discover(self) -> list[dict[str, Any]]:
        self.connect()
        self._request(
            2100,
            {"clientId": self._config.client_id, "clientSecret": self._config.client_secret},
            expected={2101},
        )
        response = self._request(
            2149,
            {"accessToken": self._config.access_token},
            expected={2150},
        )
        payload = response.get("payload") or {}
        permission = payload.get("permissionScope")
        if permission not in (0, "0", "SCOPE_VIEW"):
            raise CTraderReadOnlyError(
                "Mobile broker gateway requires an explicitly verified view-only cTrader token"
            )
        accounts = payload.get("ctidTraderAccount") or []
        return [dict(item) for item in accounts if isinstance(item, dict)]


class CompleteOAuthRequest(BaseModel):
    code: str


_HANDOFFS: dict[str, tuple[float, str]] = {}
_HANDOFF_LOCK = threading.Lock()


def _configuration_or_503() -> CTraderMobileConfiguration:
    try:
        return CTraderMobileConfiguration.from_env()
    except CTraderMobileConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="cTrader connection is not configured on this gateway yet",
        ) from exc


def _codec(config: CTraderMobileConfiguration) -> CTraderBrokerSessionCodec:
    return CTraderBrokerSessionCodec(config.session_secret)


def _discover_authorized_accounts(
    config: CTraderMobileConfiguration,
    access_token: str,
) -> tuple[CTraderAuthorizedAccount, ...]:
    discovered: dict[tuple[Any, int], CTraderAuthorizedAccount] = {}
    failures: list[Exception] = []

    for environment in (CTraderEnvironment.DEMO, CTraderEnvironment.LIVE):
        secret_config = CTraderSecretConfig(
            client_id=config.client_id,
            client_secret=config.client_secret,
            access_token=access_token,
            account_id=0,
            environment=environment,
        )
        transport = _CTraderAccountDiscoveryTransport(secret_config)
        try:
            descriptors = transport.discover()
            for descriptor in descriptors:
                account_id_raw = descriptor.get("ctidTraderAccountId")
                if account_id_raw is None:
                    continue
                descriptor_environment = (
                    CTraderEnvironment.LIVE
                    if bool(descriptor.get("isLive"))
                    else CTraderEnvironment.DEMO
                )
                if descriptor_environment is not environment:
                    continue
                account_id = int(account_id_raw)
                trader_login = str(descriptor.get("traderLogin") or account_id)
                discovered[(environment, account_id)] = CTraderAuthorizedAccount(
                    account_id=account_id,
                    trader_login=trader_login,
                    broker=(
                        str(descriptor["brokerTitleShort"])
                        if descriptor.get("brokerTitleShort")
                        else None
                    ),
                    environment=environment,
                )
        except (CTraderConnectionError, CTraderReadOnlyError, OSError) as exc:
            failures.append(exc)
        finally:
            transport.close()

    if not discovered:
        if failures:
            raise CTraderConnectionError(
                "Unable to discover authorized cTrader accounts"
            ) from failures[0]
        raise CTraderReadOnlyError("No cTrader accounts were authorized for this application")
    return tuple(discovered.values())


def _create_session(
    tokens: Any,
    accounts: tuple[CTraderAuthorizedAccount, ...],
) -> CTraderBrokerSession:
    return CTraderBrokerSession(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_type=tokens.token_type,
        expires_in=tokens.expires_in,
        issued_at=int(time.time()),
        accounts=accounts,
    )


def _put_handoff(session_token: str) -> str:
    code = secrets.token_urlsafe(32)
    now = time.time()
    with _HANDOFF_LOCK:
        expired = [key for key, (deadline, _) in _HANDOFFS.items() if deadline <= now]
        for key in expired:
            _HANDOFFS.pop(key, None)
        _HANDOFFS[code] = (now + _HANDOFF_TTL_SECONDS, session_token)
    return code


def _pop_handoff(code: str) -> str:
    now = time.time()
    with _HANDOFF_LOCK:
        item = _HANDOFFS.pop(code, None)
    if item is None or item[0] <= now:
        raise HTTPException(status_code=400, detail="OAuth handoff is invalid or expired")
    return item[1]


def _session_from_header(
    broker_session: str | None,
    config: CTraderMobileConfiguration,
) -> tuple[CTraderBrokerSessionCodec, CTraderBrokerSession]:
    if not broker_session:
        raise HTTPException(status_code=401, detail="Broker session is required")
    codec = _codec(config)
    try:
        return codec, codec.open_session(broker_session)
    except CTraderReadOnlyError as exc:
        raise HTTPException(status_code=401, detail="Broker session is invalid or expired") from exc


def _account_for_key(
    codec: CTraderBrokerSessionCodec,
    session: CTraderBrokerSession,
    account_key: str,
) -> CTraderAuthorizedAccount:
    for account in session.accounts:
        if hmac.compare_digest(codec.account_key(account), account_key):
            return account
    raise HTTPException(status_code=404, detail="Broker account was not found in this session")


def _mobile_redirect(config: CTraderMobileConfiguration, **query: str) -> RedirectResponse:
    target = f"{config.ios_callback_scheme}://ctrader/complete?{urlencode(query)}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


def _mask_account(value: Any) -> str:
    text = str(value)
    return f"••••{text[-4:]}" if text else "••••"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


@router.get("/availability")
def ctrader_availability() -> dict[str, Any]:
    try:
        CTraderMobileConfiguration.from_env()
    except CTraderMobileConfigurationError:
        return {"configured": False, "readOnly": True, "oauthScope": "accounts"}
    return {"configured": True, "readOnly": True, "oauthScope": "accounts"}


@router.get("/start")
def start_ctrader_oauth() -> Response:
    config = _configuration_or_503()
    codec = _codec(config)
    authorization_url = CTraderOAuthClient.build_authorization_url(
        client_id=config.client_id,
        redirect_uri=config.redirect_uri,
    )
    response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        _FLOW_COOKIE,
        codec.seal_flow(),
        max_age=600,
        httponly=True,
        secure=urlparse(config.redirect_uri).scheme == "https",
        samesite="lax",
        path="/v1/brokers/ctrader",
    )
    return response


@router.get("/callback")
def ctrader_oauth_callback(
    code: str = Query(min_length=1, max_length=4096),
    flow_cookie: str | None = Cookie(default=None, alias=_FLOW_COOKIE),
) -> Response:
    config = _configuration_or_503()
    codec = _codec(config)
    if not flow_cookie:
        return _mobile_redirect(config, error="oauth_flow_missing")
    try:
        codec.verify_flow(flow_cookie)
        tokens = CTraderOAuthClient().exchange_code(
            client_id=config.client_id,
            client_secret=config.client_secret,
            code=code,
            redirect_uri=config.redirect_uri,
        )
        accounts = _discover_authorized_accounts(config, tokens.access_token)
        broker_session = codec.seal_session(_create_session(tokens, accounts))
        handoff_code = _put_handoff(broker_session)
        response = _mobile_redirect(config, code=handoff_code)
    except (CTraderConnectionError, CTraderReadOnlyError):
        response = _mobile_redirect(config, error="authorization_failed")
    response.delete_cookie(_FLOW_COOKIE, path="/v1/brokers/ctrader")
    return response


@router.post("/complete")
def complete_ctrader_oauth(request: CompleteOAuthRequest) -> dict[str, Any]:
    config = _configuration_or_503()
    session_token = _pop_handoff(request.code)
    codec = _codec(config)
    try:
        session = codec.open_session(session_token)
    except CTraderReadOnlyError as exc:
        raise HTTPException(status_code=400, detail="OAuth handoff is invalid") from exc
    return {
        "connected": True,
        "provider": "cTrader Open API",
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
        "brokerSessionToken": session_token,
        "accounts": codec.public_accounts(session),
    }


@router.get("/status")
def ctrader_status(
    broker_session: str | None = Header(default=None, alias="X-Londres-Broker-Session"),
) -> dict[str, Any]:
    config = _configuration_or_503()
    if not broker_session:
        return {
            "connected": False,
            "provider": "cTrader Open API",
            "readOnly": True,
            "oauthScope": "accounts",
            "orderSubmissionEnabled": False,
            "accounts": [],
        }
    codec, session = _session_from_header(broker_session, config)
    return {
        "connected": True,
        "provider": "cTrader Open API",
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
        "accounts": codec.public_accounts(session),
    }


@router.post("/refresh")
def refresh_ctrader_session(
    broker_session: str | None = Header(default=None, alias="X-Londres-Broker-Session"),
) -> dict[str, Any]:
    config = _configuration_or_503()
    codec, session = _session_from_header(broker_session, config)
    if not session.refresh_token:
        raise HTTPException(status_code=401, detail="cTrader reauthorization is required")
    try:
        tokens = CTraderOAuthClient().refresh(
            client_id=config.client_id,
            client_secret=config.client_secret,
            refresh_token=session.refresh_token,
        )
    except CTraderConnectionError as exc:
        raise HTTPException(status_code=502, detail="cTrader token refresh failed") from exc

    refreshed = CTraderBrokerSession(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_type=tokens.token_type,
        expires_in=tokens.expires_in,
        issued_at=int(time.time()),
        accounts=session.accounts,
    )
    return {
        "connected": True,
        "brokerSessionToken": codec.seal_session(refreshed),
        "accounts": codec.public_accounts(refreshed),
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
    }


@router.get("/account")
def ctrader_account_snapshot(
    account_key: str = Query(min_length=8, max_length=128),
    broker_session: str | None = Header(default=None, alias="X-Londres-Broker-Session"),
) -> dict[str, Any]:
    config = _configuration_or_503()
    codec, session = _session_from_header(broker_session, config)
    account = _account_for_key(codec, session, account_key)

    secret_config = CTraderSecretConfig(
        client_id=config.client_id,
        client_secret=config.client_secret,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        account_id=account.account_id,
        environment=account.environment,
    )
    connector = CTraderReadOnlyConnector(secret_config)
    try:
        public_connection = connector.connect()
        snapshot = connector.account_snapshot()
    except (CTraderConnectionError, CTraderReadOnlyError) as exc:
        raise HTTPException(status_code=502, detail="cTrader account read failed") from exc
    finally:
        connector.close()

    return {
        "connection": public_connection,
        "account": {
            "accountKey": account_key,
            "broker": account.broker,
            "account": _mask_account(account.trader_login),
            "readOnly": True,
        },
        "snapshot": snapshot,
        "orderSubmissionEnabled": False,
    }
