"""Phase 18: sanitized read-only cTrader broker data acquisition."""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.brokers import CTraderReadOnlyConnector


class LondresPhase18ReadOnlyBrokerEngine:
    """Expose real broker/account/quote context without enabling execution.

    Demo/live classification, full account ids, OAuth tokens and application
    credentials stay inside the connector.  The returned context is safe to
    place in AgentState and contains no broker-order capability.
    """

    def __init__(self, connector: CTraderReadOnlyConnector) -> None:
        self.connector = connector

    def analyze(self, *, symbols: Sequence[str] = ()) -> dict:
        status = self.connector.public_status()
        if status.get("status") != "CONNECTED":
            status = self.connector.connect()

        account = self.connector.account_snapshot()
        symbol_states: dict[str, dict] = {}
        quote_states: dict[str, dict] = {}
        for symbol in symbols:
            symbol_states[symbol] = self.connector.symbol_snapshot(symbol)
            quote_states[symbol] = self.connector.quote_snapshot(symbol)

        return {
            "phase": "LONDRES_CTRADER_READ_ONLY_BROKER_DATA",
            "broker_connection": status,
            "broker_account": account,
            "broker_symbols": symbol_states,
            "broker_quotes": quote_states,
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
            "order_authorized": False,
            "broker_order_placed": False,
        }

    @staticmethod
    def state_update(context: dict) -> dict:
        return {
            "broker_connection_state": context["broker_connection"],
            "broker_account_state": context["broker_account"],
            "broker_symbol_state": context["broker_symbols"],
            "broker_quote_state": context["broker_quotes"],
        }
