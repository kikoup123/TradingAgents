from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from services.londres_market_gateway.ctrader_mobile import CompleteOAuthRequest
from services.londres_market_gateway.ctrader_mock import (
    mock_complete_ctrader_oauth,
    mock_ctrader_account_snapshot,
    mock_ctrader_status,
    mock_refresh_ctrader_session,
    mock_start_ctrader_oauth,
    router,
)


def _enable_mock(monkeypatch) -> None:
    monkeypatch.setenv("CTRADER_MOCK_MODE", "true")
    monkeypatch.setenv(
        "CTRADER_MOCK_SESSION_SECRET",
        "mock-only-0123456789abcdef0123456789abcdef0123456789",
    )
    monkeypatch.setenv("CTRADER_IOS_CALLBACK_SCHEME", "londrestradingai")


def test_mock_gateway_is_hidden_unless_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("CTRADER_MOCK_MODE", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        mock_start_ctrader_oauth()
    assert exc_info.value.status_code == 404


def test_mock_flow_exercises_mobile_handoff_status_and_account_without_ctrader(monkeypatch) -> None:
    _enable_mock(monkeypatch)

    start = mock_start_ctrader_oauth()
    location = start.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "londrestradingai"
    assert parsed.netloc == "ctrader"
    assert parsed.path == "/complete"
    handoff_code = parse_qs(parsed.query)["code"][0]

    completed = mock_complete_ctrader_oauth(CompleteOAuthRequest(code=handoff_code))
    assert completed["connected"] is True
    assert completed["mock"] is True
    assert completed["readOnly"] is True
    assert completed["oauthScope"] == "accounts"
    assert completed["orderSubmissionEnabled"] is False
    assert len(completed["accounts"]) == 1

    account = completed["accounts"][0]
    token = completed["brokerSessionToken"]
    rendered = str(completed)
    assert account["account"] == "••••1234"
    assert account["readOnly"] is True
    assert account["orderSubmissionEnabled"] is False
    assert "900000001" not in rendered
    assert "99001234" not in rendered
    assert "demo" not in rendered.lower()

    status_payload = mock_ctrader_status(token)
    assert status_payload["connected"] is True
    assert status_payload["accounts"][0]["accountKey"] == account["accountKey"]

    account_payload = mock_ctrader_account_snapshot(account["accountKey"], token)
    snapshot = account_payload["snapshot"]
    assert snapshot["masked_account"] == "••••1234"
    assert snapshot["currency"] == "EUR"
    assert snapshot["balance"] == pytest.approx(5_000.0)
    assert snapshot["equity"] == pytest.approx(5_125.5)
    assert snapshot["account_environment"] == "HIDDEN_INTERNAL"
    assert account_payload["orderSubmissionEnabled"] is False

    refreshed = mock_refresh_ctrader_session(token)
    assert refreshed["connected"] is True
    assert refreshed["readOnly"] is True
    assert refreshed["orderSubmissionEnabled"] is False
    assert refreshed["brokerSessionToken"]


def test_mock_router_contains_no_execution_endpoint() -> None:
    route_paths = {route.path.lower() for route in router.routes}
    assert route_paths == {
        "/v1/brokers/ctrader/mock/availability",
        "/v1/brokers/ctrader/mock/start",
        "/v1/brokers/ctrader/mock/complete",
        "/v1/brokers/ctrader/mock/status",
        "/v1/brokers/ctrader/mock/refresh",
        "/v1/brokers/ctrader/mock/account",
    }
    assert not any(
        any(term in path for term in ("order", "position", "execute", "trade"))
        for path in route_paths
    )
