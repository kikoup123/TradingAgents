from __future__ import annotations

from copy import deepcopy

from tradingagents.brokers import (
    BrokerSupervisionPolicy,
    BrokerSymbolMap,
    BrokerType,
    CTraderUniversalReadOnlyAdapter,
    MT5UniversalReadOnlyAdapter,
    OrchestrationPolicy,
    TradeIntent,
)
from tradingagents.ict import (
    LondresPhase29BrokerParityEngine,
    ManagedBrokerAccount,
    Phase29BrokerBinding,
    Phase29BrokerVenue,
)

NOW_MS = 1_800_000_000_000


class FakeFPConnector:
    def public_status(self) -> dict:
        return {
            "provider": "cTrader Open API",
            "broker": "FP Markets",
            "status": "CONNECTED",
            "account": "••••7788",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def connect(self) -> dict:
        return self.public_status()

    def close(self) -> None:
        return None

    def account_snapshot(self) -> dict:
        return {
            "broker": "FP Markets",
            "masked_account": "••••7788",
            "currency": "USD",
            "balance": 20_000.0,
            "equity": 20_000.0,
            "used_margin": 0.0,
            "free_margin": 20_000.0,
            "status": "CONNECTED",
        }

    def symbol_snapshot(self, symbol: str) -> dict:
        return {
            "symbol_id": 42,
            "symbol": symbol,
            "digits": 1,
            "pip_position": 1,
            "min_volume_protocol": 100,
            "max_volume_protocol": 100_000,
            "step_volume_protocol": 100,
        }

    def tick_value_snapshot(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "account_currency": "USD",
            "tick_size": 0.1,
            "tick_value_account_currency": 0.1,
            "tick_value_source": "CTRADER_OPEN_API_CONVERSION_CHAIN",
            "conversion_timestamp_ms": NOW_MS - 500,
            "valuation_model": "CTRADER_LINEAR_VOLUME_UNIT_CONSERVATIVE_LOSS_FX",
        }

    def quote_snapshot(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "bid": 24_999.9,
            "ask": 25_000.0,
            "timestamp_ms": NOW_MS - 1_000,
        }


class MemoryMT5Bridge:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def public_status(self) -> dict:
        return {
            "status": self.payload.get("bridge_status", "DISCONNECTED"),
            "provider": "MetaTrader 5 test bridge",
            "broker": self.payload.get("terminal", {}).get("company"),
            "server": self.payload.get("terminal", {}).get("server"),
            "read_only": True,
            "order_submission_enabled": False,
        }

    def snapshot(self) -> dict:
        return self.payload

    def reconnect(self) -> dict:
        return self.public_status()


def _mt5_payload(*, company: str = "Vantage Global Prime") -> dict:
    return {
        "schema_version": 1,
        "bridge_status": "CONNECTED",
        "generated_at_ms": NOW_MS - 100,
        "terminal": {"company": company, "server": "Vantage-Live", "connected": True},
        "account": {
            "account_key": "vantage-live-account-key",
            "masked_account": "••••4321",
            "provider": company,
            "server": "Vantage-Live",
            "connected": True,
            "trade_mode": "REAL",
            "currency": "USD",
            "balance": 10_000.0,
            "equity": 10_000.0,
            "used_margin": 0.0,
            "free_margin": 10_000.0,
        },
        "instruments": {
            "NAS100": {
                "symbol": "NAS100",
                "canonical_symbol": "NASDAQ",
                "tick_size": 0.1,
                "point": 0.1,
                "tick_value_loss": 0.1,
                "tick_value_currency": "USD",
                "tick_value_timestamp_ms": NOW_MS - 500,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
                "metadata_verified": True,
            }
        },
        "quotes": {
            "NAS100": {"bid": 24_999.9, "ask": 25_000.0, "timestamp_ms": NOW_MS - 1_000}
        },
    }


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="phase29-nasdaq-short",
        canonical_symbol="NASDAQ",
        direction="BEARISH",
        entry_price=25_000.0,
        stop_price=25_010.0,
        target_price=24_950.0,
        selected_exit_mode="FULL_AT_SD_2",
    )


def _supervision() -> BrokerSupervisionPolicy:
    return BrokerSupervisionPolicy(
        quote_max_age_ms=5_000,
        tick_value_max_age_ms=60_000,
        max_reconnect_attempts=1,
    )


def _bindings(mt5_payload: dict | None = None) -> tuple[Phase29BrokerBinding, Phase29BrokerBinding]:
    fp_adapter = CTraderUniversalReadOnlyAdapter(FakeFPConnector(), adapter_id="fpmarkets-ctrader")
    fp = ManagedBrokerAccount(
        account_alias="FP-LIVE",
        adapter=fp_adapter,
        symbol_map=BrokerSymbolMap(BrokerType.CTRADER, {"NASDAQ": "US100"}),
        risk_fraction=0.03,
    )

    mt5_adapter = MT5UniversalReadOnlyAdapter(MemoryMT5Bridge(mt5_payload or _mt5_payload()))
    mt5_alias = mt5_adapter.discover_accounts()[0].account_alias
    vantage = ManagedBrokerAccount(
        account_alias=mt5_alias,
        adapter=mt5_adapter,
        symbol_map=BrokerSymbolMap(BrokerType.MT5, {"NASDAQ": "NAS100"}),
        risk_fraction=0.03,
    )
    return (
        Phase29BrokerBinding(Phase29BrokerVenue.FP_MARKETS_CTRADER, fp),
        Phase29BrokerBinding(Phase29BrokerVenue.VANTAGE_MT5, vantage),
    )


def test_fp_markets_ctrader_and_vantage_mt5_reach_same_read_only_boundary() -> None:
    result = LondresPhase29BrokerParityEngine().prepare(
        intent=_intent(),
        bindings=_bindings(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["status"] == "READY"
    assert result["ready_accounts"] == 2
    by_venue = {item["venue"]: item for item in result["accounts"]}
    fp = by_venue["FP_MARKETS_CTRADER"]
    vantage = by_venue["VANTAGE_MT5"]
    assert fp["broker_name"] == "FP Markets"
    assert "Vantage" in vantage["broker_name"]
    assert fp["supervision_state"]["execution_data_ready"] is True
    assert vantage["supervision_state"]["execution_data_ready"] is True
    assert fp["phase23_account_plan"]["account_equity"] == 20_000.0
    assert vantage["phase23_account_plan"]["account_equity"] == 10_000.0
    assert fp["phase23_account_plan"]["prepared_volume"] == 60.0
    assert vantage["phase23_account_plan"]["prepared_volume"] == 30.0
    assert result["replication_mode"] == "TRADE_INTENT_NOT_RAW_LOT_COPYING"
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False


def test_vantage_provider_identity_mismatch_fails_closed() -> None:
    bindings = _bindings(_mt5_payload(company="Other Broker Ltd"))
    result = LondresPhase29BrokerParityEngine().prepare(
        intent=_intent(),
        bindings=bindings,
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    vantage = next(item for item in result["accounts"] if item["venue"] == "VANTAGE_MT5")
    assert vantage["status"] == "BLOCKED_PROVIDER_IDENTITY"
    assert vantage["preparation_ready"] is False
    assert result["status"] == "PARTIAL_READY"


def test_vantage_stale_quote_isolated_in_best_effort() -> None:
    payload = deepcopy(_mt5_payload())
    payload["quotes"]["NAS100"]["timestamp_ms"] = NOW_MS - 100_000
    result = LondresPhase29BrokerParityEngine().prepare(
        intent=_intent(),
        bindings=_bindings(payload),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    vantage = next(item for item in result["accounts"] if item["venue"] == "VANTAGE_MT5")
    assert vantage["status"] == "BLOCKED_SUPERVISION"
    assert vantage["supervision_state"]["status"] == "STALE_QUOTE"
    assert result["status"] == "PARTIAL_READY"
    assert result["ready_accounts"] == 1


def test_all_or_none_revokes_fp_when_vantage_blocks() -> None:
    payload = deepcopy(_mt5_payload())
    payload["quotes"]["NAS100"]["timestamp_ms"] = NOW_MS - 100_000
    result = LondresPhase29BrokerParityEngine().prepare(
        intent=_intent(),
        bindings=_bindings(payload),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    fp = next(item for item in result["accounts"] if item["venue"] == "FP_MARKETS_CTRADER")
    assert result["status"] == "BLOCKED"
    assert result["ready_accounts"] == 0
    assert fp["status"] == "BLOCKED_BATCH_POLICY"
    assert fp["preparation_ready"] is False


def test_phase29_has_no_execution_surface() -> None:
    engine = LondresPhase29BrokerParityEngine()
    assert not hasattr(engine, "place_order")
    assert not hasattr(engine, "submit_order")
    assert not hasattr(engine, "amend_order")
    assert not hasattr(engine, "cancel_order")
    result = engine.prepare(
        intent=_intent(),
        bindings=_bindings(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["execution_enabled"] is False
    assert result["order_submission_enabled"] is False
    assert result["order_authorized"] is False
