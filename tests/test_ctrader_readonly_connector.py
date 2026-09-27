from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tradingagents.brokers.ctrader_readonly import (
    CTraderAccountSnapshot,
    CTraderEnvironment,
    CTraderJsonReadOnlyTransport,
    CTraderOAuthClient,
    CTraderQuoteSnapshot,
    CTraderReadOnlyConnector,
    CTraderReadOnlyError,
    CTraderSecretConfig,
    CTraderSymbolSnapshot,
)


def _config(environment: CTraderEnvironment = CTraderEnvironment.DEMO) -> CTraderSecretConfig:
    return CTraderSecretConfig(
        client_id="test-client-id",
        client_secret="test-secret-value",
        access_token="test-access-value",
        refresh_token="test-refresh-value",
        account_id=123456789,
        environment=environment,
    )


class FakeTransport:
    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def authenticate_read_only(self, config: CTraderSecretConfig) -> dict:
        assert config.account_id == 123456789
        return {"connected": True, "broker": "FP Markets", "masked_account": "••••6789"}

    def read_account(self, account_id: int) -> CTraderAccountSnapshot:
        assert account_id == 123456789
        return CTraderAccountSnapshot(
            broker="FP Markets",
            masked_account="••••6789",
            currency="EUR",
            balance=10_000.0,
            equity=10_125.5,
            used_margin=1_000.0,
            free_margin=9_125.5,
            money_digits=2,
        )

    def read_symbol(self, account_id: int, symbol: str) -> CTraderSymbolSnapshot:
        assert account_id == 123456789
        return CTraderSymbolSnapshot(
            symbol_id=42,
            symbol=symbol,
            digits=2,
            pip_position=1,
            min_volume_protocol=100,
            max_volume_protocol=1_000_000,
            step_volume_protocol=100,
            sl_distance=10,
            tp_distance=10,
            distance_set_in="SYMBOL_DISTANCE_IN_POINTS",
        )

    def read_quote(self, account_id: int, symbol: str) -> CTraderQuoteSnapshot:
        assert account_id == 123456789
        return CTraderQuoteSnapshot(
            symbol_id=42,
            symbol=symbol,
            bid=2500.1,
            ask=2500.3,
            timestamp_ms=1_700_000_000_000,
        )


class StubJsonTransport(CTraderJsonReadOnlyTransport):
    def __init__(self, config: CTraderSecretConfig, responses: list[dict]) -> None:
        super().__init__(config)
        self.responses = responses
        self._account_descriptor = {
            "ctidTraderAccountId": config.account_id,
            "traderLogin": 99887766,
            "brokerTitleShort": "FP Markets",
            "isLive": config.environment is CTraderEnvironment.LIVE,
        }

    def _request(self, payload_type: int, payload: dict, *, expected: set[int]) -> dict:
        del payload_type, payload, expected
        return self.responses.pop(0)


def test_secret_config_repr_does_not_leak_sensitive_values() -> None:
    config = _config(CTraderEnvironment.LIVE)
    rendered = repr(config)
    assert "test-secret-value" not in rendered
    assert "test-access-value" not in rendered
    assert "test-refresh-value" not in rendered
    assert "123456789" not in rendered
    assert "live" not in rendered.lower()


def test_oauth_authorization_url_is_accounts_scope_only() -> None:
    url = CTraderOAuthClient.build_authorization_url(
        client_id="abc",
        redirect_uri="https://example.test/callback",
    )
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["accounts"]
    assert query["client_id"] == ["abc"]
    assert query["product"] == ["web"]
    assert "trading" not in url


def test_connector_public_state_masks_account_and_hides_environment() -> None:
    connector = CTraderReadOnlyConnector(_config(CTraderEnvironment.LIVE), transport=FakeTransport())
    status = connector.connect()
    account = connector.account_snapshot()
    assert status["status"] == "CONNECTED"
    assert status["account"] == "••••6789"
    assert status["account_environment"] == "HIDDEN_INTERNAL"
    assert status["read_only"] is True
    assert status["order_submission_enabled"] is False
    assert "LIVE" not in str(status).upper()
    assert "123456789" not in str(status)
    assert account["masked_account"] == "••••6789"
    assert account["account_environment"] == "HIDDEN_INTERNAL"


def test_account_snapshot_uses_money_digits() -> None:
    config = _config()
    responses = [
        {"payload": {"trader": {"balance": 1_000_000, "moneyDigits": 2, "depositAssetId": 7}}},
        {
            "payload": {
                "moneyDigits": 2,
                "positionUnrealizedPnL": [
                    {"positionId": 1, "netUnrealizedPnL": 12_550},
                    {"positionId": 2, "netUnrealizedPnL": -2_500},
                ],
            }
        },
        {
            "payload": {
                "position": [
                    {"positionId": 1, "usedMargin": 50_000, "moneyDigits": 2},
                    {"positionId": 2, "usedMargin": 25_000, "moneyDigits": 2},
                ]
            }
        },
        {"payload": {"asset": [{"assetId": 7, "name": "EUR"}]}},
    ]
    snapshot = StubJsonTransport(config, responses).read_account(config.account_id)
    assert snapshot.balance == pytest.approx(10_000.0)
    assert snapshot.equity == pytest.approx(10_100.5)
    assert snapshot.used_margin == pytest.approx(750.0)
    assert snapshot.free_margin == pytest.approx(9_350.5)
    assert snapshot.currency == "EUR"
    assert snapshot.masked_account == "••••7766"


def test_read_only_auth_rejects_trade_permission_scope() -> None:
    config = _config()

    class TradeScopeTransport(CTraderJsonReadOnlyTransport):
        def connect(self) -> None:
            return None

        def _request(self, payload_type: int, payload: dict, *, expected: set[int]) -> dict:
            del payload, expected
            if payload_type == 2100:
                return {"payloadType": 2101, "payload": {}}
            if payload_type == 2149:
                return {
                    "payloadType": 2150,
                    "payload": {"permissionScope": "SCOPE_TRADE", "ctidTraderAccount": []},
                }
            raise AssertionError("unexpected request")

    with pytest.raises(CTraderReadOnlyError, match="trading permission"):
        TradeScopeTransport(config).authenticate_read_only(config)


def test_symbol_protocol_volume_is_hundredths_of_unit() -> None:
    snapshot = CTraderSymbolSnapshot(
        symbol_id=1,
        symbol="XAUUSD",
        digits=2,
        pip_position=1,
        min_volume_protocol=100,
        max_volume_protocol=1_000_000,
        step_volume_protocol=100,
        sl_distance=None,
        tp_distance=None,
        distance_set_in=None,
    )
    assert snapshot.min_volume_units == pytest.approx(1.0)
    assert snapshot.step_volume_units == pytest.approx(1.0)
    assert snapshot.max_volume_units == pytest.approx(10_000.0)
    assert snapshot.display_tick_size == pytest.approx(0.01)
    assert snapshot.pip_size == pytest.approx(0.1)
