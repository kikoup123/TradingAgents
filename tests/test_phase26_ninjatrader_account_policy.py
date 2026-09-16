from __future__ import annotations

from tradingagents.brokers import BrokerSupervisionPolicy, OrchestrationPolicy, TradeIntent
from tradingagents.ict.phase26 import LondresPhase26NinjaTraderAccountPolicyEngine, NinjaTraderAccountRuleProfile, NinjaTraderTradeRuleContext

NOW_MS = 1_800_000_000_000


class FakePhase24:
    def __init__(self, prepared: dict[str, float], blocked: set[str] | None = None) -> None:
        self.prepared = prepared
        self.blocked = blocked or set()
        self.last_bindings = ()

    def prepare(self, *, intent, bindings, now_ms, supervision_policy, policy):
        del intent, now_ms, supervision_policy, policy
        self.last_bindings = tuple(bindings)
        accounts = []
        for binding in bindings:
            ready = binding.account_alias not in self.blocked
            accounts.append({"account_alias": binding.account_alias, "status": "READY" if ready else "BLOCKED_SUPERVISION", "preparation_ready": ready, "phase23_account_plan": ({"prepared_volume": self.prepared[binding.account_alias], "account_equity": 10_000.0} if ready else None)})
        return {"accounts": accounts}


def _intent() -> TradeIntent:
    return TradeIntent(trade_id="phase26-live", canonical_symbol="NASDAQ", direction="BEARISH", entry_price=25_000.0, stop_price=25_010.0, target_price=24_950.0, selected_exit_mode="FULL_AT_SD_2")


def _supervision() -> BrokerSupervisionPolicy:
    return BrokerSupervisionPolicy(quote_max_age_ms=5_000, tick_value_max_age_ms=60_000, max_reconnect_attempts=1)


def _profile(alias: str, *, root: str = "NQ", cap: int = 10, allowed_roots: tuple[str, ...] | None = None, **kwargs) -> NinjaTraderAccountRuleProfile:
    return NinjaTraderAccountRuleProfile(account_alias=alias, root_map={"NASDAQ": root}, risk_fraction=0.03, allowed_roots=allowed_roots or (root,), max_contracts_by_root={root: cap}, **kwargs)


def test_three_live_accounts_keep_independent_roots_and_caps() -> None:
    phase24 = FakePhase24({"A": 2.0, "B": 4.0, "C": 8.0})
    engine = LondresPhase26NinjaTraderAccountPolicyEngine(phase24)
    result = engine.prepare(intent=_intent(), profiles=(_profile("A", root="NQ", cap=2), _profile("B", root="NQ", cap=5), _profile("C", root="MNQ", cap=10)), rule_context=NinjaTraderTradeRuleContext(), now_ms=NOW_MS, supervision_policy=_supervision())
    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    by_alias = {item["account_alias"]: item for item in result["accounts"]}
    assert by_alias["A"]["max_contracts"] == 2
    assert by_alias["B"]["max_contracts"] == 5
    assert by_alias["C"]["selected_root"] == "MNQ"
    assert by_alias["C"]["prepared_contracts"] == 8.0
    assert all(item["policy_state"]["risk_base_mode"] == "CURRENT_BROKER_ACCOUNT_EQUITY" for item in result["accounts"])


def test_contract_cap_violation_blocks_without_silent_resizing() -> None:
    engine = LondresPhase26NinjaTraderAccountPolicyEngine(FakePhase24({"A": 3.0}))
    result = engine.prepare(intent=_intent(), profiles=(_profile("A", cap=2),), rule_context=NinjaTraderTradeRuleContext(), now_ms=NOW_MS, supervision_policy=_supervision())
    account = result["accounts"][0]
    assert account["status"] == "BLOCKED_MAX_CONTRACTS"
    assert account["prepared_contracts"] == 3.0
    assert account["max_contracts"] == 2


def test_disallowed_root_fails_before_phase24() -> None:
    phase24 = FakePhase24({"A": 1.0})
    engine = LondresPhase26NinjaTraderAccountPolicyEngine(phase24)
    result = engine.prepare(intent=_intent(), profiles=(_profile("A", root="NQ", allowed_roots=("MNQ",)),), rule_context=NinjaTraderTradeRuleContext(), now_ms=NOW_MS, supervision_policy=_supervision())
    assert result["accounts"][0]["status"] == "BLOCKED_ROOT_NOT_ALLOWED"
    assert phase24.last_bindings == ()


def test_session_news_and_holding_rules_fail_closed() -> None:
    profile = _profile("A", allowed_sessions=("NY_AM",), news_trading_allowed=False, overnight_holding_allowed=False, weekend_holding_allowed=False)
    engine = LondresPhase26NinjaTraderAccountPolicyEngine(FakePhase24({"A": 1.0}))
    missing = engine.prepare(intent=_intent(), profiles=(profile,), rule_context=NinjaTraderTradeRuleContext(), now_ms=NOW_MS, supervision_policy=_supervision())
    assert missing["accounts"][0]["status"] == "BLOCKED_RULE_CONTEXT"
    violation = engine.prepare(intent=_intent(), profiles=(profile,), rule_context=NinjaTraderTradeRuleContext(session="NY_AM", high_impact_news_window=True, will_hold_overnight=False, will_hold_weekend=False), now_ms=NOW_MS, supervision_policy=_supervision())
    assert violation["accounts"][0]["status"] == "BLOCKED_RULE_VIOLATION"


def test_best_effort_isolates_blocked_live_account_but_all_or_none_blocks_batch() -> None:
    phase24 = FakePhase24({"A": 1.0, "B": 1.0}, blocked={"B"})
    engine = LondresPhase26NinjaTraderAccountPolicyEngine(phase24)
    profiles = (_profile("A"), _profile("B"))
    best = engine.prepare(intent=_intent(), profiles=profiles, rule_context=NinjaTraderTradeRuleContext(), now_ms=NOW_MS, supervision_policy=_supervision(), policy=OrchestrationPolicy.BEST_EFFORT)
    strict = engine.prepare(intent=_intent(), profiles=profiles, rule_context=NinjaTraderTradeRuleContext(), now_ms=NOW_MS, supervision_policy=_supervision(), policy=OrchestrationPolicy.ALL_OR_NONE)
    assert best["status"] == "PARTIAL_READY"
    assert best["batch_ready_for_future_execution"] is True
    assert strict["status"] == "BLOCKED"
    assert strict["batch_ready_for_future_execution"] is False
