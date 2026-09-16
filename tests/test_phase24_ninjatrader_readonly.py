from __future__ import annotations

import json
from copy import deepcopy

import pytest

from tradingagents.brokers import (
    NINJATRADER_EQUITY_INDEX_FUTURES,
    AccountClassification,
    AccountClassificationSource,
    BrokerSupervisionPolicy,
    FuturesContractResolutionStatus,
    NinjaTraderJsonBridgeTransport,
    NinjaTraderUniversalReadOnlyAdapter,
    OrchestrationPolicy,
    PropFirmRiskLimits,
    TradeIntent,
    parse_ninjatrader_contract_symbol,
)
from tradingagents.ict import LondresPhase24NinjaTraderReadOnlyEngine, NinjaTraderAccountBinding

NOW_MS = 1_800_000_000_000


class MemoryBridge:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def public_status(self) -> dict:
        return {
            "status": self.payload.get("bridge_status", "DISCONNECTED"),
            "provider": "NinjaTrader test bridge",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def snapshot(self) -> dict:
        return self.payload

    def reconnect(self) -> dict:
        return self.public_status()


def _instrument(root: str, symbol: str, *, max_quantity: int = 20) -> dict:
    spec = NINJATRADER_EQUITY_INDEX_FUTURES[root]
    return {
        "symbol": symbol,
        "root": root,
        "canonical_symbol": spec.canonical_symbol,
        "tick_size": spec.tick_size,
        "point_value": spec.point_value_usd,
        "tick_value": spec.tick_value_usd,
        "currency": "USD",
        "max_quantity": max_quantity,
        "metadata_verified": True,
        "metadata_source": "NINJATRADER_MASTER_INSTRUMENT",
    }


def _payload() -> dict:
    contracts = {
        "NQ": "NQ 12-26",
        "MNQ": "MNQ 12-26",
        "ES": "ES 12-26",
        "MES": "MES 12-26",
        "YM": "YM 12-26",
        "MYM": "MYM 12-26",
    }
    return {
        "schema_version": 1,
        "bridge_status": "CONNECTED",
        "generated_at_ms": NOW_MS - 100,
        "accounts": [
            {
                "account_key": "private-real-a",
                "masked_account": "••••1001",
                "provider": "Broker A",
                "provider_classification": "PERSONAL",
                "provider_classification_verified": True,
                "connected": True,
                "currency": "USD",
                "balance": 20_000,
                "equity": 20_000,
                "used_margin": 0,
                "free_margin": 20_000,
            },
            {
                "account_key": "private-real-b",
                "masked_account": "••••2002",
                "provider": "Broker B",
                "provider_classification_verified": False,
                "connected": True,
                "currency": "USD",
                "balance": 40_000,
                "equity": 40_000,
                "used_margin": 0,
                "free_margin": 40_000,
            },
            {
                "account_key": "private-prop-c",
                "masked_account": "••••3003",
                "provider": "Provider C",
                "provider_classification": "PROP_FIRM",
                "provider_classification_verified": True,
                "connected": True,
                "currency": "USD",
                "balance": 100_000,
                "equity": 100_000,
                "used_margin": 0,
                "free_margin": 100_000,
            },
        ],
        "instruments": {
            symbol: _instrument(root, symbol, max_quantity=(50 if root.startswith("M") else 20))
            for root, symbol in contracts.items()
        },
        "quotes": {
            symbol: {"bid": 24_999.75, "ask": 25_000.00, "timestamp_ms": NOW_MS - 1_000}
            for symbol in contracts.values()
        },
        "rollovers": {
            root: {
                "active_contract": symbol,
                "verified": True,
                "source": "NINJATRADER_INSTRUMENT_MANAGER",
                "as_of_ms": NOW_MS - 2_000,
            }
            for root, symbol in contracts.items()
        },
    }


def _aliases(adapter: NinjaTraderUniversalReadOnlyAdapter) -> dict[str, str]:
    return {account.masked_account: account.account_alias for account in adapter.discover_accounts()}


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="phase24-nq-short",
        canonical_symbol="NASDAQ",
        direction="BEARISH",
        entry_price=25_000.0,
        stop_price=25_010.0,
        target_price=24_950.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _policy() -> BrokerSupervisionPolicy:
    return BrokerSupervisionPolicy(
        quote_max_age_ms=5_000,
        tick_value_max_age_ms=60_000,
        max_reconnect_attempts=1,
    )


def test_exchange_specs_for_nq_es_ym_and_micros_are_exact() -> None:
    expected = {
        "NQ": ("NASDAQ", 0.25, 20.0, 5.0),
        "MNQ": ("NASDAQ", 0.25, 2.0, 0.50),
        "ES": ("SP500", 0.25, 50.0, 12.50),
        "MES": ("SP500", 0.25, 5.0, 1.25),
        "YM": ("DOW", 1.0, 5.0, 5.0),
        "MYM": ("DOW", 1.0, 0.50, 0.50),
    }
    for root, values in expected.items():
        spec = NINJATRADER_EQUITY_INDEX_FUTURES[root]
        assert (spec.canonical_symbol, spec.tick_size, spec.point_value_usd, spec.tick_value_usd) == values


def test_quarterly_contract_parser_rejects_non_quarter_months() -> None:
    parsed = parse_ninjatrader_contract_symbol("NQ 12-26")
    assert parsed.root == "NQ"
    assert parsed.month == 12
    assert parsed.year == 2026
    with pytest.raises(ValueError):
        parse_ninjatrader_contract_symbol("NQ 11-26")


def test_adapter_discovers_three_accounts_and_masks_private_keys() -> None:
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(_payload()))
    accounts = adapter.discover_accounts()
    assert len(accounts) == 3
    assert {item.masked_account for item in accounts} == {"••••1001", "••••2002", "••••3003"}
    assert all(item.account_alias.startswith("NT-") for item in accounts)
    assert all("private-" not in item.account_alias for item in accounts)
    by_mask = {item.masked_account: item for item in accounts}
    assert by_mask["••••1001"].detected_classification is AccountClassification.PERSONAL
    assert by_mask["••••2002"].detected_classification is None
    assert by_mask["••••3003"].detected_classification is AccountClassification.PROP_FIRM
    assert adapter.public_status()["account_environment"] == "HIDDEN_INTERNAL"
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "submit_order")


def test_verified_rollover_resolves_exact_contract_and_never_micro_substitutes() -> None:
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(_payload()))
    nq = adapter.resolve_active_contract("NQ")
    mnq = adapter.resolve_active_contract("MNQ")
    assert nq.status is FuturesContractResolutionStatus.READY
    assert nq.active_contract == "NQ 12-26"
    assert mnq.active_contract == "MNQ 12-26"
    assert nq.active_contract != mnq.active_contract


def test_unverified_rollover_fails_closed() -> None:
    payload = _payload()
    payload["rollovers"]["NQ"]["verified"] = False
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    result = adapter.resolve_active_contract("NQ")
    assert result.status is FuturesContractResolutionStatus.CONTRACT_ROLLOVER_UNVERIFIED
    assert result.active_contract is None


def test_runtime_contract_metadata_must_match_exchange_registry() -> None:
    payload = _payload()
    payload["instruments"]["NQ 12-26"]["tick_value"] = 4.0
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    result = adapter.resolve_active_contract("NQ")
    assert result.status is FuturesContractResolutionStatus.CONTRACT_SPEC_MISMATCH


def test_json_bridge_transport_is_read_only_and_reloads(tmp_path) -> None:
    path = tmp_path / "ninjatrader-readonly.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    transport = NinjaTraderJsonBridgeTransport(path)
    assert transport.public_status()["status"] == "CONNECTED"
    assert transport.public_status()["order_submission_enabled"] is False
    payload = _payload()
    payload["bridge_status"] = "DISCONNECTED"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert transport.reconnect()["status"] == "DISCONNECTED"


def test_two_personal_and_one_prop_account_prepare_same_trade_independently() -> None:
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(_payload()))
    aliases = _aliases(adapter)
    engine = LondresPhase24NinjaTraderReadOnlyEngine(adapter)
    bindings = (
        NinjaTraderAccountBinding(
            account_alias=aliases["••••1001"],
            root_map={"NASDAQ": "NQ"},
            risk_fraction=0.03,
        ),
        NinjaTraderAccountBinding(
            account_alias=aliases["••••2002"],
            root_map={"NASDAQ": "NQ"},
            risk_fraction=0.03,
            configured_classification=AccountClassification.PERSONAL,
            classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
        ),
        NinjaTraderAccountBinding(
            account_alias=aliases["••••3003"],
            root_map={"NASDAQ": "MNQ"},
            risk_fraction=0.03,
            prop_limits=PropFirmRiskLimits(
                daily_loss_limit=3_000,
                daily_loss_used=500,
                remaining_drawdown_buffer=1_800,
                nominal_account_size=100_000,
            ),
        ),
    )
    result = engine.prepare(
        intent=_intent(),
        bindings=bindings,
        now_ms=NOW_MS,
        supervision_policy=_policy(),
    )
    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    plans = {item["masked_account"]: item for item in result["accounts"]}

    real_a = plans["••••1001"]["phase23_account_plan"]
    real_b = plans["••••2002"]["phase23_account_plan"]
    prop = plans["••••3003"]["phase23_account_plan"]
    assert real_a["risk_base"] == 20_000
    assert real_b["risk_base"] == 40_000
    assert prop["actual_account_equity"] == 100_000
    assert prop["risk_base"] == 1_800
    assert prop["nominal_account_size"] == 100_000
    assert prop["phase20_account_plan"]["account_equity"] == 1_800
    assert prop["phase20_account_plan"]["prepared_volume"] == 2.0
    assert plans["••••1001"]["active_contract"] == "NQ 12-26"
    assert plans["••••3003"]["active_contract"] == "MNQ 12-26"
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False


def test_unknown_and_conflicting_classification_fail_closed() -> None:
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(_payload()))
    aliases = _aliases(adapter)
    engine = LondresPhase24NinjaTraderReadOnlyEngine(adapter)

    unknown = NinjaTraderAccountBinding(
        account_alias=aliases["••••2002"],
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
    )
    conflict = NinjaTraderAccountBinding(
        account_alias=aliases["••••3003"],
        root_map={"NASDAQ": "MNQ"},
        risk_fraction=0.03,
        configured_classification=AccountClassification.PERSONAL,
        classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
        prop_limits=PropFirmRiskLimits(daily_loss_limit=3_000),
    )
    result = engine.prepare(
        intent=_intent(),
        bindings=(unknown, conflict),
        now_ms=NOW_MS,
        supervision_policy=_policy(),
    )
    assert result["status"] == "BLOCKED"
    assert result["ready_accounts"] == 0
    assert all(item["status"] == "BLOCKED_PHASE23" for item in result["accounts"])


def test_best_effort_allows_healthy_accounts_but_all_or_none_blocks_batch() -> None:
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(_payload()))
    aliases = _aliases(adapter)
    engine = LondresPhase24NinjaTraderReadOnlyEngine(adapter)
    healthy = NinjaTraderAccountBinding(
        account_alias=aliases["••••1001"],
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
    )
    unknown = NinjaTraderAccountBinding(
        account_alias=aliases["••••2002"],
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
    )
    best_effort = engine.prepare(
        intent=_intent(),
        bindings=(healthy, unknown),
        now_ms=NOW_MS,
        supervision_policy=_policy(),
        policy=OrchestrationPolicy.BEST_EFFORT,
    )
    all_or_none = engine.prepare(
        intent=_intent(),
        bindings=(healthy, unknown),
        now_ms=NOW_MS,
        supervision_policy=_policy(),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    assert best_effort["status"] == "PARTIAL_READY"
    assert best_effort["batch_ready_for_future_execution"] is True
    assert all_or_none["status"] == "BLOCKED"
    assert all_or_none["batch_ready_for_future_execution"] is False


def test_stale_quote_blocks_phase24_before_account_risk_preparation() -> None:
    payload = deepcopy(_payload())
    payload["quotes"]["NQ 12-26"]["timestamp_ms"] = NOW_MS - 100_000
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    aliases = _aliases(adapter)
    engine = LondresPhase24NinjaTraderReadOnlyEngine(adapter)
    binding = NinjaTraderAccountBinding(
        account_alias=aliases["••••1001"],
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
    )
    result = engine.prepare(
        intent=_intent(),
        bindings=(binding,),
        now_ms=NOW_MS,
        supervision_policy=_policy(),
    )
    account = result["accounts"][0]
    assert account["status"] == "BLOCKED_SUPERVISION"
    assert account["supervision_state"]["status"] == "STALE_QUOTE"
    assert account["phase23_account_plan"] is None
