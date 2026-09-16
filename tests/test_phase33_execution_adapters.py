from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tradingagents.brokers.ctrader import CTraderEnvironment, CTraderSecretConfig
from tradingagents.brokers.ctrader_execution import (
    CTraderExecutionAdapter,
    CTraderJsonExecutionTransport,
    CTraderTradingOAuth,
)
from tradingagents.brokers.ctrader_readonly import CTraderReadOnlyError
from tradingagents.brokers.mt5_execution import (
    MT5ExecutionIPCError,
    MT5FileExecutionTransport,
    VantageMT5ExecutionAdapter,
    deterministic_mt5_magic,
)
from tradingagents.brokers.ninjatrader_execution import (
    NinjaTraderExecutionAdapter,
    NinjaTraderExecutionIPCError,
    NinjaTraderFileExecutionTransport,
)

ROOT = Path(__file__).resolve().parents[1]


def _ctrader_config() -> CTraderSecretConfig:
    return CTraderSecretConfig(
        client_id="client",
        client_secret="secret",
        access_token="trade-token",
        account_id=123456,
        environment=CTraderEnvironment.DEMO,
    )


def _command() -> dict:
    return {
        "schema_version": 1,
        "command_id": "a" * 64,
        "client_order_label": "L32-AAAAAAAAAAAAAAAAAAAAAAAA",
        "trade_id": "P33-IPC",
        "account_alias": "NT-ALPHA",
        "venue": "NINJATRADER",
        "broker_type": "NINJATRADER",
        "broker_symbol": "NQ 12-26",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "execution_style": "MARKET_ON_SIGNAL",
        "exact_volume": 2.0,
        "volume_unit": "contracts",
        "selected_risk_fraction": 0.03,
        "intended_entry_price": 25000.0,
        "phase31_executable_price": 25000.25,
        "stop_price": 24990.0,
        "target_price": 25050.0,
        "selected_exit_mode": "FULL_AT_SD_2",
        "phase30_authorization_fingerprint": "b" * 64,
        "phase31_pre_submit_fingerprint": "c" * 64,
        "quote_timestamp_ms": 1_800_000_000_000,
        "shadow_mode": True,
    }


def test_ctrader_execution_oauth_requests_trading_scope() -> None:
    url = CTraderTradingOAuth.build_authorization_url(
        client_id="client",
        redirect_uri="https://localhost/callback",
    )
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["trading"]
    assert query["client_id"] == ["client"]
    assert "accounts" not in query["scope"]


def test_ctrader_protocol_volume_is_exact_and_never_rounded() -> None:
    assert CTraderJsonExecutionTransport._protocol_volume(40.0) == 4000
    assert CTraderJsonExecutionTransport._protocol_volume(0.01) == 1
    with pytest.raises(CTraderReadOnlyError):
        CTraderJsonExecutionTransport._protocol_volume(0.001)


def test_execution_adapters_are_disabled_by_default(tmp_path) -> None:
    ctrader = CTraderExecutionAdapter(_ctrader_config(), account_alias="FP-ALPHA")
    mt5 = VantageMT5ExecutionAdapter(account_alias="MT5-ALPHA", bridge_root=tmp_path / "mt5")
    ninja = NinjaTraderExecutionAdapter(account_alias="NT-ALPHA", bridge_root=tmp_path / "nt")
    assert ctrader.execution_capabilities().execution_enabled is False
    assert mt5.execution_capabilities().execution_enabled is False
    assert ninja.execution_capabilities().execution_enabled is False


def test_mt5_magic_is_stable_positive_31_bit() -> None:
    value = deterministic_mt5_magic("a" * 64)
    assert value == deterministic_mt5_magic("a" * 64)
    assert 0 <= value <= 0x7FFFFFFF
    assert value != deterministic_mt5_magic("b" * 64)


def test_ninjatrader_ipc_request_is_immutable(tmp_path) -> None:
    transport = NinjaTraderFileExecutionTransport(tmp_path, timeout_seconds=0.01)
    command = _command()
    envelope = {
        "schema_version": 1,
        "operation": "SUBMIT_MARKET",
        "command_id": command["command_id"],
        "requested_at_ms": 100,
        "command": command,
    }
    path = transport.requests / f"{command['command_id']}.SUBMIT_MARKET.json"
    transport._atomic_create_or_verify(path, envelope)
    transport._atomic_create_or_verify(path, envelope)
    changed = json.loads(json.dumps(envelope))
    changed["command"]["exact_volume"] = 3.0
    with pytest.raises(NinjaTraderExecutionIPCError):
        transport._atomic_create_or_verify(path, changed)


def test_mt5_ipc_request_is_immutable(tmp_path) -> None:
    transport = MT5FileExecutionTransport(tmp_path, timeout_seconds=0.01)
    command = _command()
    command.update(
        {
            "account_alias": "MT5-ALPHA",
            "venue": "VANTAGE_MT5",
            "broker_type": "MT5",
            "broker_symbol": "NAS100",
            "exact_volume": 1.0,
            "volume_unit": "lots",
        }
    )
    envelope = {
        "schema_version": 1,
        "operation": "SUBMIT_MARKET",
        "command_id": command["command_id"],
        "requested_at_ms": 100,
        "command": command,
    }
    path = transport.requests / f"{command['command_id']}.SUBMIT_MARKET.json"
    transport._atomic_create_or_verify(path, envelope)
    transport._atomic_create_or_verify(path, envelope)
    changed = json.loads(json.dumps(envelope))
    changed["command"]["exact_volume"] = 2.0
    with pytest.raises(MT5ExecutionIPCError):
        transport._atomic_create_or_verify(path, changed)


def test_mt5_execution_host_source_has_preflight_exact_fill_policy_and_reconciliation() -> None:
    source = (ROOT / "scripts" / "mt5_execution_bridge.py").read_text(encoding="utf-8")
    for token in (
        "--enable-execution",
        "order_check",
        "order_send",
        "TRADE_RETCODE_DONE",
        "TRADE_RETCODE_DONE_PARTIAL",
        "TRADE_RETCODE_TIMEOUT",
        "TRADE_RETCODE_CONNECTION",
        "SYMBOL_FILLING_FOK",
        "SYMBOL_FILLING_IOC",
        "ORDER_FILLING_RETURN",
        "positions_get",
        "history_orders_get",
        "history_deals_get",
        "SUBMITTING",
        "HIDDEN_INTERNAL",
        "bridge_execution.sqlite",
    ):
        assert token in source
    assert "resizing forbidden" in source


def test_ninjatrader_execution_bridge_source_has_claim_submit_fill_protection_and_reconcile() -> None:
    source = (ROOT / "ninjatrader" / "LondresExecutionBridge.cs").read_text(encoding="utf-8")
    for token in (
        "Account.CreateOrder",
        ".Submit(",
        "OrderUpdate",
        "ExecutionUpdate",
        "OrderType.Market",
        "OrderType.StopMarket",
        "OrderType.Limit",
        "FileMode.CreateNew",
        "Reconcile(",
        "HIDDEN_INTERNAL",
        "execution_enabled",
        "L33OCO-",
    ):
        assert token in source
    assert "Guid.NewGuid" not in source


def test_local_execution_responses_do_not_serialize_private_environment_fields() -> None:
    mt5_source = (ROOT / "scripts" / "mt5_execution_bridge.py").read_text(encoding="utf-8")
    ninja_source = (ROOT / "ninjatrader" / "LondresExecutionBridge.cs").read_text(encoding="utf-8")
    assert '"account_environment": "HIDDEN_INTERNAL"' in mt5_source
    assert 'AccountEnvironment = "HIDDEN_INTERNAL"' in ninja_source
    assert '"server": server' not in mt5_source
    assert '"login": login' not in mt5_source
