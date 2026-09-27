from __future__ import annotations

from copy import deepcopy

from tradingagents.brokers import (
    NINJATRADER_EQUITY_INDEX_FUTURES,
    BrokerSupervisionPolicy,
    NinjaTraderUniversalReadOnlyAdapter,
    OrchestrationPolicy,
    TradeIntent,
)
from tradingagents.ict.phase28 import (
    LondresPhase28PreSubmitRevalidationEngine,
    Phase28AccountStatus,
    Phase28PreSubmitPolicy,
)

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


def _instrument(root: str, symbol: str, *, max_quantity: int = 200) -> dict:
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
    contracts = {"NQ": "NQ 12-26", "MNQ": "MNQ 12-26"}
    return {
        "schema_version": 1,
        "bridge_status": "CONNECTED",
        "generated_at_ms": NOW_MS - 100,
        "accounts": [
            {
                "account_key": "private-live-a",
                "masked_account": "••••1001",
                "provider": "Broker A",
                "connected": True,
                "currency": "USD",
                "balance": 20_000,
                "equity": 20_000,
                "used_margin": 0,
                "free_margin": 20_000,
            },
            {
                "account_key": "private-live-b",
                "masked_account": "••••2002",
                "provider": "Broker B",
                "connected": True,
                "currency": "USD",
                "balance": 20_000,
                "equity": 20_000,
                "used_margin": 0,
                "free_margin": 20_000,
            },
        ],
        "instruments": {
            symbol: _instrument(root, symbol) for root, symbol in contracts.items()
        },
        "quotes": {
            "NQ 12-26": {
                "bid": 24_999.75,
                "ask": 25_000.00,
                "timestamp_ms": NOW_MS - 1_000,
            },
            "MNQ 12-26": {
                "bid": 24_999.75,
                "ask": 25_000.00,
                "timestamp_ms": NOW_MS - 1_000,
            },
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


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="LONDRES-NQ-001",
        canonical_symbol="NASDAQ",
        direction="BULLISH",
        entry_price=25_000.0,
        stop_price=24_990.0,
        target_price=25_050.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _supervision() -> BrokerSupervisionPolicy:
    return BrokerSupervisionPolicy(
        quote_max_age_ms=5_000,
        tick_value_max_age_ms=60_000,
        max_reconnect_attempts=1,
    )


def _market_policy(
    *, max_spread_ticks: int = 2, max_adverse_entry_deviation_ticks: int = 4
) -> Phase28PreSubmitPolicy:
    return Phase28PreSubmitPolicy(
        max_spread_ticks=max_spread_ticks,
        max_adverse_entry_deviation_ticks=max_adverse_entry_deviation_ticks,
    )


def _aliases(adapter: NinjaTraderUniversalReadOnlyAdapter) -> dict[str, str]:
    return {
        account.masked_account: account.account_alias
        for account in adapter.discover_accounts()
    }


def _account(
    alias: str,
    *,
    fingerprint: str,
    root: str = "NQ",
    contract: str = "NQ 12-26",
    quantity: int = 2,
    max_contracts: int = 5,
    risk_fraction: float = 0.03,
) -> dict:
    return {
        "account_alias": alias,
        "status": "AUTHORIZED_FOR_EXECUTION_HANDOFF",
        "trade_id": "LONDRES-NQ-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "selected_root": root,
        "active_contract": contract,
        "contract_quantity": quantity,
        "entry_price": 25_000.0,
        "stop_price": 24_990.0,
        "target_price": 25_050.0,
        "selected_exit_mode": "FULL_AT_SD_2",
        "execution_handoff_ready": True,
        "order_authorized": True,
        "authorization_fingerprint": fingerprint,
        "phase26_account_plan": {
            "account_alias": alias,
            "max_contracts": max_contracts,
            "policy_state": {
                "risk_fraction": risk_fraction,
                "max_risk_cash": None,
            },
        },
    }


def _plan(
    *accounts: dict,
    policy: OrchestrationPolicy = OrchestrationPolicy.BEST_EFFORT,
) -> dict:
    return {
        "trade_id": "LONDRES-NQ-001",
        "canonical_symbol": "NASDAQ",
        "direction": "BULLISH",
        "policy": policy.value,
        "execution_handoff_ready": True,
        "order_authorized": True,
        "order_submission_enabled": False,
        "accounts": list(accounts),
    }


def _engine_call(
    *,
    payload: dict,
    plan: dict,
    used: tuple[str, ...] = (),
    market_policy: Phase28PreSubmitPolicy | None = None,
) -> dict:
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    return LondresPhase28PreSubmitRevalidationEngine().revalidate(
        intent=_intent(),
        phase27_plan=plan,
        adapter=adapter,
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=market_policy or _market_policy(),
        used_authorization_fingerprints=used,
    )


def test_phase28_ready_path_revalidates_current_quote_equity_and_contract() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = _aliases(adapter)["••••1001"]
    fingerprint = "a" * 64
    result = LondresPhase28PreSubmitRevalidationEngine().revalidate(
        intent=_intent(),
        phase27_plan=_plan(_account(alias, fingerprint=fingerprint)),
        adapter=adapter,
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        pre_submit_policy=_market_policy(),
        used_authorization_fingerprints=(),
    )

    account = result["accounts"][0]
    assert result["status"] == "READY"
    assert result["pre_submit_ready"] is True
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False
    assert result["fingerprint_registry_mutated"] is False
    assert account["status"] == Phase28AccountStatus.READY_FOR_SUBMISSION_ADAPTER_HANDOFF.value
    assert account["current_executable_price"] == 25_000.0
    assert account["projected_cash_risk"] == 400.0
    assert account["projected_equity_risk_fraction"] == 0.02
    assert account["fingerprint_consumed"] is False
    assert len(account["pre_submit_snapshot_fingerprint"]) == 64


def test_phase28_blocks_stale_quote() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = _aliases(adapter)["••••1001"]
    payload["quotes"]["NQ 12-26"]["timestamp_ms"] = NOW_MS - 100_000

    result = _engine_call(
        payload=payload,
        plan=_plan(_account(alias, fingerprint="b" * 64)),
    )
    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase28AccountStatus.BLOCKED_SUPERVISION.value
    assert "BROKER_QUOTE_EXCEEDS_EXPLICIT_MAX_AGE" in account["reason_codes"]


def test_phase28_blocks_when_active_contract_changed_after_phase27() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = _aliases(adapter)["••••1001"]
    payload["instruments"]["NQ 03-27"] = _instrument("NQ", "NQ 03-27")
    payload["quotes"]["NQ 03-27"] = deepcopy(payload["quotes"]["NQ 12-26"])
    payload["rollovers"]["NQ"]["active_contract"] = "NQ 03-27"

    result = _engine_call(
        payload=payload,
        plan=_plan(_account(alias, fingerprint="c" * 64)),
    )
    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase28AccountStatus.BLOCKED_CONTRACT_REVALIDATION.value
    assert "ACTIVE_CONTRACT_CHANGED_OR_ROLLOVER_NO_LONGER_VERIFIED" in account["reason_codes"]


def test_phase28_blocks_used_authorization_fingerprint() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = _aliases(adapter)["••••1001"]
    fingerprint = "d" * 64

    result = _engine_call(
        payload=payload,
        plan=_plan(_account(alias, fingerprint=fingerprint)),
        used=(fingerprint,),
    )
    account = result["accounts"][0]
    assert result["status"] == "BLOCKED"
    assert account["status"] == Phase28AccountStatus.BLOCKED_DUPLICATE_AUTHORIZATION.value
    assert account["pre_submit_snapshot_fingerprint"] is None


def test_phase28_blocks_spread_and_adverse_quote_limits() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = _aliases(adapter)["••••1001"]
    payload["quotes"]["NQ 12-26"].update(bid=24_999.0, ask=25_001.0)

    spread = _engine_call(
        payload=payload,
        plan=_plan(_account(alias, fingerprint="e" * 64)),
        market_policy=_market_policy(max_spread_ticks=2, max_adverse_entry_deviation_ticks=8),
    )
    assert spread["accounts"][0]["status"] == Phase28AccountStatus.BLOCKED_MARKET_REVALIDATION.value
    assert "CURRENT_SPREAD_EXCEEDS_EXPLICIT_PHASE28_LIMIT" in spread["accounts"][0]["reason_codes"]

    payload["quotes"]["NQ 12-26"].update(bid=25_000.75, ask=25_001.0)
    adverse = _engine_call(
        payload=payload,
        plan=_plan(_account(alias, fingerprint="f" * 64)),
        market_policy=_market_policy(max_spread_ticks=2, max_adverse_entry_deviation_ticks=2),
    )
    assert adverse["accounts"][0]["status"] == Phase28AccountStatus.BLOCKED_MARKET_REVALIDATION.value
    assert "CURRENT_QUOTE_EXCEEDS_EXPLICIT_ADVERSE_ENTRY_DEVIATION_LIMIT" in adverse["accounts"][0]["reason_codes"]


def test_phase28_recomputes_risk_from_current_executable_quote_and_equity() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    alias = _aliases(adapter)["••••1001"]
    payload["accounts"][0]["equity"] = 10_000
    payload["accounts"][0]["free_margin"] = 10_000
    payload["quotes"]["NQ 12-26"].update(bid=25_000.75, ask=25_001.0)

    result = _engine_call(
        payload=payload,
        plan=_plan(_account(alias, fingerprint="1" * 64)),
        market_policy=_market_policy(max_spread_ticks=2, max_adverse_entry_deviation_ticks=8),
    )
    account = result["accounts"][0]
    assert account["status"] == Phase28AccountStatus.BLOCKED_RISK_REVALIDATION.value
    assert account["current_executable_price"] == 25_001.0
    assert account["projected_cash_risk"] == 440.0
    assert account["projected_equity_risk_fraction"] == 0.044
    assert "CURRENT_EQUITY_RISK_EXCEEDS_PHASE26_ACCOUNT_RISK_FRACTION" in account["reason_codes"]


def test_phase28_all_or_none_revokes_otherwise_ready_account() -> None:
    payload = _payload()
    adapter = NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload))
    aliases = _aliases(adapter)
    first = _account(aliases["••••1001"], fingerprint="2" * 64, quantity=2)
    second = _account(aliases["••••2002"], fingerprint="3" * 64, quantity=4)

    result = _engine_call(
        payload=payload,
        plan=_plan(first, second, policy=OrchestrationPolicy.ALL_OR_NONE),
    )
    first_result, second_result = result["accounts"]
    assert result["status"] == "BLOCKED"
    assert result["ready_accounts"] == 0
    assert first_result["status"] == Phase28AccountStatus.BLOCKED_BATCH_POLICY.value
    assert first_result["pre_submit_snapshot_fingerprint"] is None
    assert second_result["status"] == Phase28AccountStatus.BLOCKED_RISK_REVALIDATION.value


def test_phase28_has_no_order_submission_surface() -> None:
    engine = LondresPhase28PreSubmitRevalidationEngine()
    assert not hasattr(engine, "place_order")
    assert not hasattr(engine, "submit_order")
    assert not hasattr(engine, "cancel_order")
    assert not hasattr(engine, "flatten")
