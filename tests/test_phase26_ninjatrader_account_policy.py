from __future__ import annotations

from copy import deepcopy

from tradingagents.brokers import (
    NINJATRADER_EQUITY_INDEX_FUTURES,
    AccountClassification,
    AccountClassificationSource,
    BrokerSupervisionPolicy,
    NinjaTraderUniversalReadOnlyAdapter,
    OrchestrationPolicy,
    PropFirmRiskLimits,
    TradeIntent,
)
from tradingagents.ict import (
    LondresPhase24NinjaTraderReadOnlyEngine,
    LondresPhase26NinjaTraderAccountPolicyEngine,
    NinjaTraderAccountRuleProfile,
    NinjaTraderTradeRuleContext,
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


def _instrument(root: str, symbol: str, *, max_quantity: int) -> dict:
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
            "NQ 12-26": _instrument("NQ", "NQ 12-26", max_quantity=20),
            "MNQ 12-26": _instrument("MNQ", "MNQ 12-26", max_quantity=50),
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


def _adapter(payload: dict | None = None) -> NinjaTraderUniversalReadOnlyAdapter:
    return NinjaTraderUniversalReadOnlyAdapter(MemoryBridge(payload or _payload()))


def _aliases(adapter: NinjaTraderUniversalReadOnlyAdapter) -> dict[str, str]:
    return {account.masked_account: account.account_alias for account in adapter.discover_accounts()}


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="phase26-nq-short",
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


def _context() -> NinjaTraderTradeRuleContext:
    return NinjaTraderTradeRuleContext(
        session="NEW_YORK",
        high_impact_news_window=False,
        will_hold_overnight=False,
        will_hold_weekend=False,
    )


def _profiles(
    adapter: NinjaTraderUniversalReadOnlyAdapter,
    *,
    prop_cap: int = 3,
    configure_real_b: bool = True,
    prop_limits: PropFirmRiskLimits | None = None,
) -> tuple[NinjaTraderAccountRuleProfile, ...]:
    aliases = _aliases(adapter)
    limits = prop_limits or PropFirmRiskLimits(
        daily_loss_limit=3_000,
        daily_loss_used=500,
        remaining_drawdown_buffer=1_800,
        nominal_account_size=100_000,
    )
    return (
        NinjaTraderAccountRuleProfile(
            account_alias=aliases["••••1001"],
            root_map={"NASDAQ": "NQ"},
            risk_fraction=0.03,
            allowed_roots=("NQ",),
            max_contracts_by_root={"NQ": 4},
            allowed_sessions=("NEW_YORK",),
            news_trading_allowed=False,
            overnight_holding_allowed=False,
            weekend_holding_allowed=False,
        ),
        NinjaTraderAccountRuleProfile(
            account_alias=aliases["••••2002"],
            root_map={"NASDAQ": "NQ"},
            risk_fraction=0.03,
            allowed_roots=("NQ",),
            max_contracts_by_root={"NQ": 8},
            configured_classification=(
                AccountClassification.PERSONAL if configure_real_b else None
            ),
            classification_source=(
                AccountClassificationSource.USER_CONFIRMED_CONFIG
                if configure_real_b
                else AccountClassificationSource.UNKNOWN
            ),
        ),
        NinjaTraderAccountRuleProfile(
            account_alias=aliases["••••3003"],
            root_map={"NASDAQ": "MNQ"},
            risk_fraction=0.03,
            allowed_roots=("MNQ",),
            max_contracts_by_root={"MNQ": prop_cap},
            prop_limits=limits,
            news_trading_allowed=False,
            overnight_holding_allowed=False,
            weekend_holding_allowed=False,
        ),
    )


def _engine(adapter: NinjaTraderUniversalReadOnlyAdapter) -> LondresPhase26NinjaTraderAccountPolicyEngine:
    return LondresPhase26NinjaTraderAccountPolicyEngine(
        LondresPhase24NinjaTraderReadOnlyEngine(adapter)
    )


def test_two_personal_and_one_prop_use_independent_account_caps_and_roots() -> None:
    adapter = _adapter()
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=_profiles(adapter),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    plans = {item["account_alias"]: item for item in result["accounts"]}
    aliases = _aliases(adapter)
    assert plans[aliases["••••1001"]]["selected_root"] == "NQ"
    assert plans[aliases["••••1001"]]["prepared_contracts"] == 3.0
    assert plans[aliases["••••2002"]]["prepared_contracts"] == 6.0
    assert plans[aliases["••••3003"]]["selected_root"] == "MNQ"
    assert plans[aliases["••••3003"]]["prepared_contracts"] == 2.0
    prop_phase23 = plans[aliases["••••3003"]]["phase24_account_plan"]["phase23_account_plan"]
    assert prop_phase23["risk_base"] == 1_800
    assert prop_phase23["nominal_account_size"] == 100_000
    assert result["order_submission_enabled"] is False
    assert result["broker_order_placed"] is False


def test_best_effort_blocks_only_prop_when_risk_sized_contracts_exceed_prop_cap() -> None:
    adapter = _adapter()
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=_profiles(adapter, prop_cap=1),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.BEST_EFFORT,
    )
    assert result["status"] == "PARTIAL_READY"
    assert result["ready_accounts"] == 2
    prop = next(item for item in result["accounts"] if item["selected_root"] == "MNQ")
    assert prop["status"] == "BLOCKED_MAX_CONTRACTS"
    assert prop["prepared_contracts"] == 2.0
    assert prop["max_contracts"] == 1
    assert "PHASE26_DOES_NOT_SILENTLY_RESIZE_TO_ACCOUNT_MAXIMUM" in prop["reason_codes"]


def test_all_or_none_blocks_batch_when_one_prop_account_violates_cap() -> None:
    adapter = _adapter()
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=_profiles(adapter, prop_cap=1),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    assert result["status"] == "BLOCKED"
    assert result["batch_ready_for_future_execution"] is False
    assert result["ready_accounts"] == 2


def test_unknown_account_classification_still_fails_closed_through_phase24() -> None:
    adapter = _adapter()
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=_profiles(adapter, configure_real_b=False),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    aliases = _aliases(adapter)
    real_b = next(item for item in result["accounts"] if item["account_alias"] == aliases["••••2002"])
    assert real_b["status"] == "BLOCKED_PHASE24"
    assert result["status"] == "PARTIAL_READY"


def test_disallowed_root_is_blocked_before_phase24_preparation() -> None:
    adapter = _adapter()
    profile = _profiles(adapter)[0]
    bad = NinjaTraderAccountRuleProfile(
        account_alias=profile.account_alias,
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
        allowed_roots=("MNQ",),
        max_contracts_by_root={"NQ": 4, "MNQ": 10},
    )
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=(bad,),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    account = result["accounts"][0]
    assert account["status"] == "BLOCKED_ROOT_NOT_ALLOWED"
    assert account["phase24_account_plan"] is None


def test_prop_exhausted_daily_or_drawdown_buffer_blocks_account() -> None:
    adapter = _adapter()
    daily_exhausted = _profiles(
        adapter,
        prop_limits=PropFirmRiskLimits(
            daily_loss_limit=3_000,
            daily_loss_used=3_000,
            remaining_drawdown_buffer=2_000,
        ),
    )[2]
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=(daily_exhausted,),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PHASE24"

    drawdown_exhausted = _profiles(
        adapter,
        prop_limits=PropFirmRiskLimits(
            daily_loss_limit=3_000,
            daily_loss_used=500,
            remaining_drawdown_buffer=0,
        ),
    )[2]
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=(drawdown_exhausted,),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PHASE24"


def test_news_context_and_unsupported_consistency_rules_fail_closed() -> None:
    adapter = _adapter()
    base = _profiles(adapter)[2]
    no_news_context = NinjaTraderAccountRuleProfile(
        account_alias=base.account_alias,
        root_map=base.root_map,
        risk_fraction=base.risk_fraction,
        allowed_roots=base.allowed_roots,
        max_contracts_by_root=base.max_contracts_by_root,
        prop_limits=base.prop_limits,
        news_trading_allowed=False,
    )
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=(no_news_context,),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_RULE_CONTEXT"

    unsupported = NinjaTraderAccountRuleProfile(
        account_alias=base.account_alias,
        root_map=base.root_map,
        risk_fraction=base.risk_fraction,
        allowed_roots=base.allowed_roots,
        max_contracts_by_root=base.max_contracts_by_root,
        prop_limits=base.prop_limits,
        consistency_rule_required=True,
    )
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=(unsupported,),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_UNSUPPORTED_REQUIRED_RULE"


def test_stale_quote_from_phase24_still_blocks_phase26() -> None:
    payload = deepcopy(_payload())
    payload["quotes"]["NQ 12-26"]["timestamp_ms"] = NOW_MS - 100_000
    adapter = _adapter(payload)
    result = _engine(adapter).prepare(
        intent=_intent(),
        profiles=(_profiles(adapter)[0],),
        rule_context=_context(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PHASE24"
