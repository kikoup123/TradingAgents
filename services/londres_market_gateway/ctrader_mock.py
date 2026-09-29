from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from .ctrader_mobile import (
    CompleteOAuthRequest,
    CTraderAuthorizedAccount,
    CTraderBrokerSession,
    CTraderBrokerSessionCodec,
    CTraderEnvironment,
    _account_for_key,
    _mask_account,
    _pop_handoff,
    _put_handoff,
)

router = APIRouter(prefix="/v1/brokers/ctrader/mock", tags=["cTrader Mock"])

_SCHEME_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CTraderMockConfiguration:
    session_secret: str
    ios_callback_scheme: str

    @classmethod
    def from_env(cls) -> CTraderMockConfiguration:
        enabled = os.getenv("CTRADER_MOCK_MODE", "").strip().lower() in _TRUE_VALUES
        if not enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

        secret = (
            os.getenv("CTRADER_MOCK_SESSION_SECRET", "").strip()
            or os.getenv("CTRADER_SESSION_SECRET", "").strip()
        )
        if len(secret) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="cTrader mock mode requires a 32+ character session secret",
            )

        scheme = os.getenv("CTRADER_IOS_CALLBACK_SCHEME", "londrestradingai").strip().lower()
        if not _SCHEME_PATTERN.fullmatch(scheme):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="cTrader mock callback scheme is invalid",
            )
        return cls(session_secret=secret, ios_callback_scheme=scheme)


def _mock_session() -> CTraderBrokerSession:
    return CTraderBrokerSession(
        access_token="mock-read-only-access-token",
        refresh_token="mock-read-only-refresh-token",
        token_type="bearer",
        expires_in=2_628_000,
        issued_at=int(time.time()),
        accounts=(
            CTraderAuthorizedAccount(
                account_id=900_000_001,
                trader_login="99001234",
                broker="Trading Sand Mock",
                environment=CTraderEnvironment.DEMO,
            ),
        ),
    )


def _codec(config: CTraderMockConfiguration) -> CTraderBrokerSessionCodec:
    return CTraderBrokerSessionCodec(config.session_secret)


def _session_from_header(
    broker_session: str | None,
    config: CTraderMockConfiguration,
) -> tuple[CTraderBrokerSessionCodec, CTraderBrokerSession]:
    if not broker_session:
        raise HTTPException(status_code=401, detail="Broker session is required")
    codec = _codec(config)
    try:
        session = codec.open_session(broker_session)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Broker session is invalid or expired") from exc
    return codec, session


def _mobile_redirect(config: CTraderMockConfiguration, **query: str) -> RedirectResponse:
    target = f"{config.ios_callback_scheme}://ctrader/complete?{urlencode(query)}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


@router.get("/availability")
def mock_ctrader_availability() -> dict[str, Any]:
    CTraderMockConfiguration.from_env()
    return {
        "configured": True,
        "mock": True,
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
    }


@router.get("/start")
def mock_start_ctrader_oauth() -> RedirectResponse:
    """Simulate the browser handoff without contacting cTrader.

    This route exists only when CTRADER_MOCK_MODE is explicitly enabled. It
    creates synthetic, encrypted read-only state and immediately redirects back
    to the app using the same one-time handoff contract as the production flow.
    """

    config = CTraderMockConfiguration.from_env()
    codec = _codec(config)
    token = codec.seal_session(_mock_session())
    handoff_code = _put_handoff(token)
    return _mobile_redirect(config, code=handoff_code)


@router.post("/complete")
def mock_complete_ctrader_oauth(request: CompleteOAuthRequest) -> dict[str, Any]:
    config = CTraderMockConfiguration.from_env()
    token = _pop_handoff(request.code)
    codec = _codec(config)
    try:
        session = codec.open_session(token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="OAuth handoff is invalid") from exc
    return {
        "connected": True,
        "provider": "cTrader Mock Read Only",
        "mock": True,
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
        "brokerSessionToken": token,
        "accounts": codec.public_accounts(session),
    }


@router.get("/status")
def mock_ctrader_status(
    broker_session: str | None = Header(default=None, alias="X-Londres-Broker-Session"),
) -> dict[str, Any]:
    config = CTraderMockConfiguration.from_env()
    if not broker_session:
        return {
            "connected": False,
            "provider": "cTrader Mock Read Only",
            "mock": True,
            "readOnly": True,
            "oauthScope": "accounts",
            "orderSubmissionEnabled": False,
            "accounts": [],
        }
    codec, session = _session_from_header(broker_session, config)
    return {
        "connected": True,
        "provider": "cTrader Mock Read Only",
        "mock": True,
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
        "accounts": codec.public_accounts(session),
    }


@router.post("/refresh")
def mock_refresh_ctrader_session(
    broker_session: str | None = Header(default=None, alias="X-Londres-Broker-Session"),
) -> dict[str, Any]:
    config = CTraderMockConfiguration.from_env()
    codec, session = _session_from_header(broker_session, config)
    refreshed = CTraderBrokerSession(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        token_type=session.token_type,
        expires_in=session.expires_in,
        issued_at=int(time.time()),
        accounts=session.accounts,
    )
    return {
        "connected": True,
        "mock": True,
        "brokerSessionToken": codec.seal_session(refreshed),
        "accounts": codec.public_accounts(refreshed),
        "readOnly": True,
        "oauthScope": "accounts",
        "orderSubmissionEnabled": False,
    }


@router.get("/account")
def mock_ctrader_account_snapshot(
    account_key: str = Query(min_length=8, max_length=128),
    broker_session: str | None = Header(default=None, alias="X-Londres-Broker-Session"),
) -> dict[str, Any]:
    config = CTraderMockConfiguration.from_env()
    codec, session = _session_from_header(broker_session, config)
    account = _account_for_key(codec, session, account_key)
    masked = _mask_account(account.trader_login)

    snapshot = {
        "broker": account.broker,
        "masked_account": masked,
        "currency": "EUR",
        "balance": 5_000.00,
        "equity": 5_125.50,
        "used_margin": 250.00,
        "free_margin": 4_875.50,
        "money_digits": 2,
        "connected": True,
        "status": "CONNECTED",
        "account_environment": "HIDDEN_INTERNAL",
    }
    return {
        "connection": {
            "provider": "cTrader Mock Read Only",
            "broker": account.broker,
            "status": "CONNECTED",
            "account": masked,
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "oauth_scope": "accounts",
            "order_submission_enabled": False,
        },
        "account": {
            "accountKey": account_key,
            "broker": account.broker,
            "account": masked,
            "readOnly": True,
        },
        "snapshot": snapshot,
        "mock": True,
        "orderSubmissionEnabled": False,
    }
