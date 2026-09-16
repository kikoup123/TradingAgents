from __future__ import annotations

from tradingagents.brokers import (
    AccountClassification,
    AccountClassificationSource,
    BrokerSupervisionPolicy,
    InMemoryPropFirmRuleCache,
    OrchestrationPolicy,
    PropFirmComplianceEngine,
    PropFirmLiveMetrics,
    PropFirmRiskLimits,
    PropFirmRuleCacheEntry,
    PropFirmRuleCacheKey,
    PropFirmRuleSnapshot,
    TradeIntent,
)
from tradingagents.ict.phase26 import NinjaTraderAccountRuleProfile, NinjaTraderTradeRuleContext
from tradingagents.ict.phase29 import LondresPhase29PropComplianceEngine, Phase29AccountStatus

NOW_MS = 1_800_000_000_000


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


def _personal(alias: str) -> NinjaTraderAccountRuleProfile:
    return NinjaTraderAccountRuleProfile(
        account_alias=alias,
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
        allowed_roots=("NQ",),
        max_contracts_by_root={"NQ": 5},
        configured_classification=AccountClassification.PERSONAL,
        classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
    )


def _prop(alias: str) -> NinjaTraderAccountRuleProfile:
    return NinjaTraderAccountRuleProfile(
        account_alias=alias,
        root_map={"NASDAQ": "MNQ"},
        risk_fraction=0.03,
        allowed_roots=("MNQ",),
        max_contracts_by_root={"MNQ": 5},
        configured_classification=AccountClassification.PROP_FIRM,
        classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
        prop_limits=PropFirmRiskLimits(
            daily_loss_limit=3_000,
            daily_loss_used=500,
            remaining_drawdown_buffer=1_800,
            nominal_account_size=100_000,
        ),
    )


def _snapshot(*, consistency: bool = False) -> PropFirmRuleSnapshot:
    return PropFirmRuleSnapshot(
        provider_id="TOPSTEP",
        provider_name="Topstep",
        official_domains=("topstep.com", "help.topstep.com"),
        retrieved_at_ms=NOW_MS - 1_000,
        source_urls=("https://help.topstep.com/en/articles/example",),
        program_name="Trading Combine",
        account_size="50K",
        allowed_roots=("MNQ",),
        max_contracts_by_root={"MNQ": 2},
        daily_loss_rule_present=True,
        drawdown_rule_present=True,
        consistency_rule_required=consistency,
    )


class FakePhase28:
    def __init__(
        self,
        *,
        prop_snapshot: PropFirmRuleSnapshot | None = None,
        prop_ready: bool = True,
        telemetry_program: str = "Trading Combine",
        telemetry_size: str = "50K",
    ) -> None:
        self.prop_snapshot = prop_snapshot or _snapshot()
        self.prop_ready = prop_ready
        self.telemetry_program = telemetry_program
        self.telemetry_size = telemetry_size

    def prepare(
        self,
        *,
        intent,
        profiles,
        rule_context,
        now_ms,
        supervision_policy,
        research_hints,
        policy,
    ) -> dict:
        accounts = []
        for profile in profiles:
            if profile.configured_classification is AccountClassification.PERSONAL:
                accounts.append(
                    {
                        "account_alias": profile.account_alias,
                        "classification": "PERSONAL",
                        "provider_label": "Personal Broker",
                        "provider_id": None,
                        "risk_telemetry_state": None,
                        "effective_prop_risk_limits": None,
                        "phase27_account_plan": None,
                        "preparation_ready": True,
                    }
                )
                continue
            snapshot = self.prop_snapshot.public_dict()
            accounts.append(
                {
                    "account_alias": profile.account_alias,
                    "classification": "PROP_FIRM",
                    "provider_label": "Topstep / Rithmic",
                    "provider_id": "TOPSTEP",
                    "risk_telemetry_state": {
                        "snapshot": {
                            "program_name": self.telemetry_program,
                            "account_size": self.telemetry_size,
                        }
                    },
                    "effective_prop_risk_limits": {
                        "daily_loss_limit": 3_000,
                        "daily_loss_used": 500,
                        "remaining_drawdown_buffer": 1_800,
                        "nominal_account_size": 100_000,
                    },
                    "phase27_account_plan": {
                        "research_state": {"snapshot": snapshot},
                        "preparation_ready": self.prop_ready,
                    },
                    "preparation_ready": self.prop_ready,
                }
            )
        return {"accounts": accounts}


def _engine(phase28: FakePhase28 | None = None) -> LondresPhase29PropComplianceEngine:
    return LondresPhase29PropComplianceEngine(
        phase28=phase28 or FakePhase28(),
        compliance=PropFirmComplianceEngine(),
        performance_metrics_max_age_ms=5_000,
        rule_cache_ttl_ms=60_000,
    )


def _metrics(
    alias: str,
    *,
    daily_loss_used: float = 500,
    drawdown: float = 1_800,
    contracts: dict[str, int] | None = None,
) -> PropFirmLiveMetrics:
    return PropFirmLiveMetrics(
        account_alias=alias,
        observed_at_ms=NOW_MS - 500,
        daily_loss_used=daily_loss_used,
        remaining_drawdown_buffer=drawdown,
        current_contracts_by_root=contracts or {},
    )


def test_two_personal_and_one_prop_pass_independently() -> None:
    engine = _engine()
    result = engine.prepare(
        intent=_intent(),
        profiles=(_personal("NT-A"), _personal("NT-B"), _prop("NT-C")),
        performance_metrics=(_metrics("NT-C"),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    prop = next(item for item in result["accounts"] if item["account_alias"] == "NT-C")
    assert prop["compliance_state"]["status"] == "READY"
    assert prop["rule_cache_state"]["provider_id"] == "TOPSTEP"
    assert result["nominal_prop_account_size_used_for_sizing"] is False


def test_missing_prop_performance_metrics_isolated_under_best_effort() -> None:
    engine = _engine()
    result = engine.prepare(
        intent=_intent(),
        profiles=(_personal("NT-A"), _prop("NT-C")),
        performance_metrics=(),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.BEST_EFFORT,
    )
    assert result["status"] == "PARTIAL_READY"
    prop = next(item for item in result["accounts"] if item["account_alias"] == "NT-C")
    assert prop["status"] == Phase29AccountStatus.BLOCKED_MISSING_PERFORMANCE_METRICS.value


def test_daily_loss_breach_blocks_prop() -> None:
    result = _engine().prepare(
        intent=_intent(),
        profiles=(_prop("NT-C"),),
        performance_metrics=(_metrics("NT-C", daily_loss_used=3_000),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PROP_LIMIT_BREACH"
    assert result["accounts"][0]["compliance_state"]["status"] == "DAILY_LOSS_BREACH"


def test_drawdown_breach_blocks_prop() -> None:
    result = _engine().prepare(
        intent=_intent(),
        profiles=(_prop("NT-C"),),
        performance_metrics=(_metrics("NT-C", drawdown=0),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PROP_LIMIT_BREACH"
    assert result["accounts"][0]["compliance_state"]["status"] == "DRAWDOWN_BREACH"


def test_current_contract_cap_reached_blocks_prop() -> None:
    result = _engine().prepare(
        intent=_intent(),
        profiles=(_prop("NT-C"),),
        performance_metrics=(_metrics("NT-C", contracts={"MNQ": 2}),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["compliance_state"]["status"] == "CONTRACT_CAP_REACHED"


def test_required_consistency_formula_without_verified_adapter_fails_closed() -> None:
    phase28 = FakePhase28(prop_snapshot=_snapshot(consistency=True), prop_ready=False)
    result = _engine(phase28).prepare(
        intent=_intent(),
        profiles=(_prop("NT-C"),),
        performance_metrics=(_metrics("NT-C"),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_UNSUPPORTED_REQUIRED_FORMULA"
    assert result["accounts"][0]["compliance_state"]["status"] == "UNSUPPORTED_REQUIRED_FORMULA"


def test_program_mismatch_between_verified_telemetry_and_rules_blocks() -> None:
    phase28 = FakePhase28(telemetry_program="Express Funded")
    result = _engine(phase28).prepare(
        intent=_intent(),
        profiles=(_prop("NT-C"),),
        performance_metrics=(_metrics("NT-C"),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PROVIDER_PROGRAM_MISMATCH"


def test_all_or_none_blocks_batch_when_prop_fails() -> None:
    result = _engine().prepare(
        intent=_intent(),
        profiles=(_personal("NT-A"), _prop("NT-C")),
        performance_metrics=(),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    assert result["status"] == "BLOCKED"
    assert result["batch_ready_for_future_execution"] is False


def test_rule_cache_expires_and_invalidates_stale_snapshot() -> None:
    cache = InMemoryPropFirmRuleCache()
    snapshot = _snapshot()
    key = PropFirmRuleCacheKey(
        provider_label="Topstep / Rithmic",
        program_hint="Trading Combine",
        account_size_hint="50K",
    )
    entry = PropFirmRuleCacheEntry(
        key=key,
        snapshot=snapshot,
        fetched_at_ms=NOW_MS - 10_000,
        expires_at_ms=NOW_MS + 1_000,
        source_digest=snapshot.source_digest,
    )
    cache.put(entry)
    assert cache.get(key=key, now_ms=NOW_MS) is not None
    assert cache.get(key=key, now_ms=NOW_MS + 2_000) is None


def test_rule_cache_rejects_provider_program_key_mismatch() -> None:
    cache = InMemoryPropFirmRuleCache()
    snapshot = _snapshot()
    wrong_key = PropFirmRuleCacheKey(
        provider_label="Topstep",
        program_hint="Express Funded",
        account_size_hint="50K",
    )
    entry = PropFirmRuleCacheEntry(
        key=wrong_key,
        snapshot=snapshot,
        fetched_at_ms=NOW_MS - 1_000,
        expires_at_ms=NOW_MS + 60_000,
        source_digest=snapshot.source_digest,
    )
    try:
        cache.put(entry)
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched cache key must fail closed")
