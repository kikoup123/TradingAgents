from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from services.londres_market_gateway.ctrader_mobile import (
    CTraderAuthorizedAccount,
    CTraderBrokerSession,
    CTraderBrokerSessionCodec,
    CTraderEnvironment,
    CTraderMobileConfiguration,
    ctrader_availability,
    start_ctrader_oauth,
)


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("CTRADER_CLIENT_ID", "client-123")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "server-only-client-secret")
    monkeypatch.setenv(
        "CTRADER_REDIRECT_URI",
        "https://gateway.example.test/v1/brokers/ctrader/callback",
    )
    monkeypatch.setenv(
        "CTRADER_SESSION_SECRET",
        "0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    monkeypatch.setenv("CTRADER_IOS_CALLBACK_SCHEME", "londrestradingai")


def test_mobile_configuration_requires_server_side_values(monkeypatch) -> None:
    _configure(monkeypatch)
    config = CTraderMobileConfiguration.from_env()
    assert config.client_id == "client-123"
    assert config.redirect_uri.endswith("/v1/brokers/ctrader/callback")
    assert config.ios_callback_scheme == "londrestradingai"


def test_start_redirect_is_accounts_scope_only(monkeypatch) -> None:
    _configure(monkeypatch)
    response = start_ctrader_oauth()
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    assert query["scope"] == ["accounts"]
    assert query["client_id"] == ["client-123"]
    assert query["product"] == ["web"]
    assert "trading" not in location
    assert "londres_ctrader_oauth_flow=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_encrypted_mobile_session_hides_ctrader_credentials_and_account_routing() -> None:
    codec = CTraderBrokerSessionCodec(
        "0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    session = CTraderBrokerSession(
        access_token="access-token-value",
        refresh_token="refresh-token-value",
        token_type="bearer",
        expires_in=2_628_000,
        issued_at=1_800_000_000,
        accounts=(
            CTraderAuthorizedAccount(
                account_id=123456789,
                trader_login="99887766",
                broker="FP Markets",
                environment=CTraderEnvironment.LIVE,
            ),
        ),
    )

    sealed = codec.seal_session(session)
    assert "access-token-value" not in sealed
    assert "refresh-token-value" not in sealed
    assert "123456789" not in sealed
    assert "99887766" not in sealed
    assert "live" not in sealed.lower()

    reopened = codec.open_session(sealed)
    assert reopened.access_token == "access-token-value"
    assert reopened.refresh_token == "refresh-token-value"
    assert reopened.accounts[0].account_id == 123456789
    assert reopened.accounts[0].environment is CTraderEnvironment.LIVE

    public = codec.public_accounts(reopened)
    rendered = str(public)
    assert public[0]["account"] == "••••7766"
    assert public[0]["readOnly"] is True
    assert public[0]["orderSubmissionEnabled"] is False
    assert "123456789" not in rendered
    assert "99887766" not in rendered
    assert "live" not in rendered.lower()


def test_availability_is_fail_closed_when_credentials_are_missing(monkeypatch) -> None:
    for name in (
        "CTRADER_CLIENT_ID",
        "CTRADER_CLIENT_SECRET",
        "CTRADER_REDIRECT_URI",
        "CTRADER_SESSION_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    status = ctrader_availability()
    assert status == {"configured": False, "readOnly": True, "oauthScope": "accounts"}
