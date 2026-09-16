"""Fail-closed cTrader Open API execution adapter for Londres Phase 33.

This module is deliberately separate from the existing view-only connector. It
requires an access token whose cTrader permission scope is explicitly verified
as SCOPE_TRADE and an explicit local ``execution_enabled=True`` gate.
"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping
from urllib.parse import urlencode

from .contracts import BrokerType
from .ctrader import (
    CTraderConnectionError,
    CTraderEnvironment,
    CTraderJsonReadOnlyTransport,
    CTraderReadOnlyError,
    CTraderSecretConfig,
)
from .execution import (
    BrokerExecutionCapabilities,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)


class CTraderTradingOAuth:
    AUTHORIZE_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"

    @classmethod
    def build_authorization_url(cls, *, client_id: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "trading",
                "product": "web",
            }
        )
        return f"{cls.AUTHORIZE_URL}?{query}"


class CTraderJsonExecutionTransport(CTraderJsonReadOnlyTransport):
    """Hardened JSON transport requiring cTrader full trading permission."""

    EXECUTION_EVENT = 2126

    def authenticate_trading(self, config: CTraderSecretConfig) -> dict[str, Any]:
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
        if permission not in (1, "1", "SCOPE_TRADE"):
            raise CTraderReadOnlyError(
                "Phase 33 cTrader execution requires explicitly verified SCOPE_TRADE"
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
            raise CTraderReadOnlyError("Configured cTrader account is not granted to this token")
        expected_live = config.environment is CTraderEnvironment.LIVE
        if bool(descriptor.get("isLive")) != expected_live:
            raise CTraderReadOnlyError("cTrader endpoint/account environment mismatch")
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
            "account_environment": "HIDDEN_INTERNAL",
        }

    def submit_market_command(
        self,
        *,
        config: CTraderSecretConfig,
        command: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_account(config.account_id)
        symbol_id, _ = self._resolve_symbol(config.account_id, str(command["broker_symbol"]))
        volume_protocol = self._protocol_volume(float(command["exact_volume"]))
        client_msg_id = f"L33-{str(command['command_id'])[:56]}"
        payload = {
            "ctidTraderAccountId": config.account_id,
            "symbolId": symbol_id,
            "orderType": 1,
            "tradeSide": 1 if command["direction"] == "BULLISH" else 2,
            "volume": volume_protocol,
            "stopLoss": float(command["stop_price"]),
            "takeProfit": float(command["target_price"]),
            "label": str(command["client_order_label"]),
            "comment": f"Londres {str(command['command_id'])[:24]}",
            "clientOrderId": str(command["command_id"])[:50],
        }
        self._send(
            {
                "clientMsgId": client_msg_id,
                "payloadType": 2106,
                "payload": payload,
            }
        )

        deadline = time.monotonic() + self._timeout
        last_event: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            event = self._wait_for(
                expected={self.EXECUTION_EVENT},
                predicate=lambda message: self._event_matches(
                    message,
                    client_msg_id=client_msg_id,
                    command=command,
                ),
            )
            last_event = event
            event_payload = event.get("payload") or {}
            execution_type = int(event_payload.get("executionType", -1))
            if execution_type == 3:
                return self._normalized_event(event_payload, command=command, outcome="FILLED")
            if execution_type == 7:
                return self._normalized_event(event_payload, command=command, outcome="REJECTED")
            if execution_type == 11:
                continue
            if execution_type == 2:
                continue
        raise CTraderConnectionError(
            "Timed out after cTrader order request without a terminal execution event: "
            + str(last_event is not None)
        )

    def reconcile_command(
        self,
        *,
        config: CTraderSecretConfig,
        command: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_account(config.account_id)
        symbol_id, _ = self._resolve_symbol(config.account_id, str(command["broker_symbol"]))
        response = self._request(
            2124,
            {"ctidTraderAccountId": config.account_id},
            expected={2125},
        )
        payload = response.get("payload") or {}
        for position in payload.get("position") or []:
            trade_data = position.get("tradeData") or {}
            if not self._trade_data_matches(
                trade_data,
                command=command,
                symbol_id=symbol_id,
            ):
                continue
            volume_units = float(trade_data.get("volume", 0)) / 100.0
            stop_loss = position.get("stopLoss")
            take_profit = position.get("takeProfit")
            return {
                "outcome": "FILLED",
                "order_id": None,
                "position_id": position.get("positionId"),
                "filled_volume": volume_units,
                "fill_price": position.get("price"),
                "stop_active": self._same_price(stop_loss, command["stop_price"]),
                "target_active": self._same_price(take_profit, command["target_price"]),
                "provider_code": "CTRADER_RECONCILE_POSITION_MATCH",
            }
        for order in payload.get("order") or []:
            trade_data = order.get("tradeData") or {}
            client_order_id = str(order.get("clientOrderId") or "")
            label = str(trade_data.get("label") or "")
            if client_order_id != str(command["command_id"])[:50] and label != str(
                command["client_order_label"]
            ):
                continue
            status = int(order.get("orderStatus", -1))
            if status == 3:
                return {
                    "outcome": "REJECTED",
                    "order_id": order.get("orderId"),
                    "position_id": None,
                    "filled_volume": float(order.get("executedVolume") or 0) / 100.0,
                    "fill_price": order.get("executionPrice"),
                    "stop_active": False,
                    "target_active": False,
                    "provider_code": "CTRADER_RECONCILE_REJECTED_ORDER",
                }
            return {
                "outcome": "PENDING_OR_UNKNOWN",
                "order_id": order.get("orderId"),
                "position_id": None,
                "filled_volume": float(order.get("executedVolume") or 0) / 100.0,
                "fill_price": order.get("executionPrice"),
                "stop_active": False,
                "target_active": False,
                "provider_code": "CTRADER_RECONCILE_ORDER_STILL_PRESENT",
            }
        return {
            "outcome": "NOT_FOUND",
            "order_id": None,
            "position_id": None,
            "filled_volume": None,
            "fill_price": None,
            "stop_active": False,
            "target_active": False,
            "provider_code": "CTRADER_RECONCILE_NO_CURRENT_MATCH",
        }

    @staticmethod
    def _protocol_volume(volume_units: float) -> int:
        if not math.isfinite(volume_units) or volume_units <= 0:
            raise CTraderReadOnlyError("cTrader exact volume must be finite and > 0")
        raw = volume_units * 100.0
        rounded = round(raw)
        if not math.isclose(raw, rounded, abs_tol=1e-9):
            raise CTraderReadOnlyError("cTrader exact volume is not representable in 0.01 units")
        return int(rounded)

    @staticmethod
    def _event_matches(
        message: dict[str, Any],
        *,
        client_msg_id: str,
        command: Mapping[str, Any],
    ) -> bool:
        if message.get("clientMsgId") == client_msg_id:
            return True
        payload = message.get("payload") or {}
        order = payload.get("order") or {}
        position = payload.get("position") or {}
        deal = payload.get("deal") or {}
        label = str(command["client_order_label"])
        order_trade = order.get("tradeData") or {}
        position_trade = position.get("tradeData") or {}
        return bool(
            str(order.get("clientOrderId") or "") == str(command["command_id"])[:50]
            or str(order_trade.get("label") or "") == label
            or str(position_trade.get("label") or "") == label
            or str(deal.get("label") or "") == label
        )

    def _normalized_event(
        self,
        payload: dict[str, Any],
        *,
        command: Mapping[str, Any],
        outcome: str,
    ) -> dict[str, Any]:
        order = payload.get("order") or {}
        position = payload.get("position") or {}
        deal = payload.get("deal") or {}
        executed = order.get("executedVolume")
        if executed is None:
            executed = deal.get("filledVolume")
        volume = float(executed or 0) / 100.0
        price = order.get("executionPrice")
        if price is None:
            price = deal.get("executionPrice")
        return {
            "outcome": outcome,
            "order_id": order.get("orderId") or deal.get("orderId"),
            "position_id": position.get("positionId") or deal.get("positionId"),
            "filled_volume": volume,
            "fill_price": price,
            "stop_active": self._same_price(position.get("stopLoss"), command["stop_price"]),
            "target_active": self._same_price(
                position.get("takeProfit"), command["target_price"]
            ),
            "provider_code": str(payload.get("errorCode") or f"CTRADER_{outcome}"),
        }

    @staticmethod
    def _trade_data_matches(
        trade_data: Mapping[str, Any],
        *,
        command: Mapping[str, Any],
        symbol_id: int,
    ) -> bool:
        expected_side = 1 if command["direction"] == "BULLISH" else 2
        expected_volume = CTraderJsonExecutionTransport._protocol_volume(
            float(command["exact_volume"])
        )
        return bool(
            int(trade_data.get("symbolId", -1)) == symbol_id
            and int(trade_data.get("tradeSide", -1)) == expected_side
            and int(trade_data.get("volume", -1)) == expected_volume
            and str(trade_data.get("label") or "") == str(command["client_order_label"])
        )

    @staticmethod
    def _same_price(left: Any, right: Any) -> bool:
        if left is None or right is None:
            return False
        try:
            return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False


class CTraderExecutionAdapter:
    """Direct cTrader Phase 33 adapter with explicit live-execution gate."""

    VENUE = "FP_MARKETS_CTRADER"

    def __init__(
        self,
        config: CTraderSecretConfig,
        *,
        account_alias: str,
        execution_enabled: bool = False,
        adapter_id: str = "ctrader-execution",
        transport: CTraderJsonExecutionTransport | None = None,
    ) -> None:
        if not account_alias.strip():
            raise ValueError("account_alias is required")
        self._config = config
        self._account_alias = account_alias
        self._execution_enabled = bool(execution_enabled)
        self._adapter_id = adapter_id
        self._transport = transport or CTraderJsonExecutionTransport(config)
        self._authenticated = False

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def broker_type(self) -> BrokerType:
        return BrokerType.CTRADER

    @property
    def venue(self) -> str:
        return self.VENUE

    def execution_capabilities(self) -> BrokerExecutionCapabilities:
        return BrokerExecutionCapabilities(
            broker_type=self.broker_type,
            venue=self.venue,
            execution_enabled=self._execution_enabled,
            supports_market_orders=True,
            supports_server_side_stop=True,
            supports_server_side_target=True,
            supports_reconciliation=True,
        )

    def submit_market(
        self,
        command: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        self._require_enabled(command)
        self._authenticate()
        try:
            result = self._transport.submit_market_command(config=self._config, command=command)
        except CTraderConnectionError as exc:
            return self._receipt(
                command,
                outcome=BrokerExecutionOutcome.AMBIGUOUS,
                now_ms=now_ms,
                provider_code="CTRADER_SUBMIT_CONNECTION_UNCERTAIN",
                provider_message=str(exc),
            )
        except CTraderReadOnlyError as exc:
            return self._receipt(
                command,
                outcome=BrokerExecutionOutcome.REJECTED,
                now_ms=now_ms,
                provider_code="CTRADER_PRE_SUBMIT_VALIDATION_REJECTED",
                provider_message=str(exc),
                definite_no_fill=True,
            )
        return self._result_receipt(command, result=result, now_ms=now_ms)

    def reconcile(
        self,
        command: Mapping[str, Any],
        *,
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        self._require_enabled(command)
        self._authenticate()
        try:
            result = self._transport.reconcile_command(config=self._config, command=command)
        except (CTraderConnectionError, CTraderReadOnlyError) as exc:
            return self._receipt(
                command,
                outcome=BrokerExecutionOutcome.RECONCILIATION_REQUIRED,
                now_ms=now_ms,
                provider_code="CTRADER_RECONCILIATION_UNAVAILABLE",
                provider_message=str(exc),
            )
        return self._result_receipt(command, result=result, now_ms=now_ms)

    def close(self) -> None:
        self._transport.close()
        self._authenticated = False

    def _authenticate(self) -> None:
        if self._authenticated:
            return
        self._transport.authenticate_trading(self._config)
        self._authenticated = True

    def _require_enabled(self, command: Mapping[str, Any]) -> None:
        if not self._execution_enabled:
            raise CTraderReadOnlyError("cTrader execution adapter is not explicitly enabled")
        if str(command.get("account_alias") or "") != self._account_alias:
            raise CTraderReadOnlyError("cTrader command account alias does not match binding")
        if str(command.get("venue") or "") != self.venue:
            raise CTraderReadOnlyError("cTrader command venue mismatch")
        if str(command.get("broker_type") or "") != self.broker_type.value:
            raise CTraderReadOnlyError("cTrader command broker type mismatch")
        if str(command.get("execution_style") or "") != "MARKET_ON_SIGNAL":
            raise CTraderReadOnlyError("cTrader Phase 33 supports MARKET_ON_SIGNAL only")

    def _result_receipt(
        self,
        command: Mapping[str, Any],
        *,
        result: Mapping[str, Any],
        now_ms: int,
    ) -> BrokerExecutionReceipt:
        outcome_name = str(result.get("outcome") or "")
        if outcome_name == "FILLED":
            outcome = BrokerExecutionOutcome.ACKNOWLEDGED
        elif outcome_name == "REJECTED":
            outcome = BrokerExecutionOutcome.REJECTED
        elif outcome_name == "NOT_FOUND":
            outcome = BrokerExecutionOutcome.NOT_FOUND
        else:
            outcome = BrokerExecutionOutcome.RECONCILIATION_REQUIRED
        filled = result.get("filled_volume")
        definite_no_fill = bool(outcome is BrokerExecutionOutcome.REJECTED and float(filled or 0) == 0)
        return self._receipt(
            command,
            outcome=outcome,
            now_ms=now_ms,
            broker_order_id=result.get("order_id"),
            broker_position_id=result.get("position_id"),
            filled_volume=(float(filled) if filled is not None else None),
            fill_price=(
                float(result["fill_price"]) if result.get("fill_price") is not None else None
            ),
            stop_active=bool(result.get("stop_active")),
            target_active=bool(result.get("target_active")),
            provider_code=str(result.get("provider_code") or "CTRADER"),
            definite_no_fill=definite_no_fill,
        )

    def _receipt(
        self,
        command: Mapping[str, Any],
        *,
        outcome: BrokerExecutionOutcome,
        now_ms: int,
        broker_order_id: Any = None,
        broker_position_id: Any = None,
        filled_volume: float | None = None,
        fill_price: float | None = None,
        stop_active: bool = False,
        target_active: bool = False,
        provider_code: str | None = None,
        provider_message: str | None = None,
        definite_no_fill: bool = False,
    ) -> BrokerExecutionReceipt:
        return BrokerExecutionReceipt(
            command_id=str(command["command_id"]),
            account_alias=self._account_alias,
            venue=self.venue,
            broker_type=self.broker_type,
            client_order_label=str(command["client_order_label"]),
            outcome=outcome,
            broker_order_id=(str(broker_order_id) if broker_order_id is not None else None),
            broker_position_id=(
                str(broker_position_id) if broker_position_id is not None else None
            ),
            filled_volume=filled_volume,
            average_fill_price=fill_price,
            stop_protection_active=stop_active,
            target_protection_active=target_active,
            provider_code=provider_code,
            provider_message=provider_message,
            submitted_at_ms=now_ms,
            acknowledged_at_ms=(now_ms if outcome is BrokerExecutionOutcome.ACKNOWLEDGED else None),
            definite_no_fill=definite_no_fill,
        )
