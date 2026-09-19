from __future__ import annotations

from tradingagents.brokers import (
    CTraderEnvironment,
    CTraderJsonReadOnlyTransport,
    CTraderSecretConfig,
)


def _config() -> CTraderSecretConfig:
    return CTraderSecretConfig(
        client_id="test-client",
        client_secret="test-secret",
        access_token="test-access",
        account_id=123456,
        environment=CTraderEnvironment.DEMO,
    )


class CorrelationTransport(CTraderJsonReadOnlyTransport):
    def __init__(self) -> None:
        super().__init__(_config(), timeout=0.5)
        self.network_messages = [
            {"payloadType": 2128, "clientMsgId": "request-1", "payload": {}},
        ]

    def _next_network_message(self, deadline: float) -> dict:
        del deadline
        return self.network_messages.pop(0)


def test_unrelated_async_message_is_parked_until_requested() -> None:
    transport = CorrelationTransport()
    transport._pending.append(
        {
            "payloadType": 2131,
            "payload": {"symbolId": 42, "bid": 100000, "ask": 100100},
        }
    )

    response = transport._wait_for(
        expected={2128},
        predicate=lambda message: message.get("clientMsgId") == "request-1",
    )

    assert response["payloadType"] == 2128
    assert len(transport._pending) == 1

    spot = transport._wait_for(
        expected={2131},
        predicate=lambda message: (message.get("payload") or {}).get("symbolId") == 42,
    )
    assert spot["payloadType"] == 2131
    assert transport._pending == []
