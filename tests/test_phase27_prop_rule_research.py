from __future__ import annotations

from tradingagents.brokers import (
    AccountClassification,
    AccountClassificationSource,
    BrokerSupervisionPolicy,
    OrchestrationPolicy,
    PropFirmRiskLimits,
    TradeIntent,
)
from tradingagents.brokers.prop_rule_research import (
    PropFirmIdentityStatus,
    PropFirmProviderRegistry,
    PropFirmRuleResearchEngine,
    PropFirmRuleResearchRequest,
    PropFirmRuleResearchStatus,
    PropFirmRuleSnapshot,
    PropFirmSearchHit,
)
from tradingagents.ict.phase26 import NinjaTraderAccountRuleProfile, NinjaTraderTradeRuleContext
from tradingagents.ict.phase27 import (
    LondresPhase27PropFirmRuleResearchEngine,
    Phase27AccountStatus,
    PropFirmRuleRefreshPolicy,
)

NOW_MS = 1_800_000_000_000


class FakeSearch:
    def __init__(self, hits: tuple[PropFirmSearchHit, ...]) -> None:
        self.hits = hits
        self.calls: list[dict] = []

    def search(self, *, query: str, include_domains: tuple[str, ...] = (), max_results: int = 8):
        self.calls.append(
            {"query": query, "include_domains": include_domains, "max_results": max_results}
        )
        if include_domains:
            return tuple(
                hit
                for hit in self.hits
                if any(domain in hit.url for domain in include_domains)
            )
        return self.hits


class FakeExtractor:
    def __init__(self, snapshot_factory) -> None:
        self.snapshot_factory = snapshot_factory

    def extract(self, *, provider, request, hits, now_ms):
        return self.snapshot_factory(provider, request, hits, now_ms)


class FakePhase24:
    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = accounts

    def discover(self) -> dict:
        return {"accounts": self.accounts}


class FakePhase26:
    def __init__(self) -> None:
        self.last_profiles = ()

    def prepare(
        self,
        *,
        intent,
        profiles,
        rule_context,
        now_ms,
        supervision_policy,
        policy,
    ) -> dict:
        self.last_profiles = tuple(profiles)
        accounts = []
        for profile in profiles:
            ready = not profile.consistency_rule_required and not profile.scaling_rule_required
            accounts.append(
                {
                    "account_alias": profile.account_alias,
                    "status": "READY" if ready else "BLOCKED_UNSUPPORTED_REQUIRED_RULE",
                    "preparation_ready": ready,
                    "policy_state": profile.public_dict(),
                }
            )
        return {"accounts": accounts}


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="phase27-nasdaq-short",
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


def _topstep_hit() -> PropFirmSearchHit:
    return PropFirmSearchHit(
        title="Topstep rules",
        url="https://help.topstep.com/en/articles/example",
        content="Official Topstep account rules",
        score=0.99,
    )


def _snapshot(provider, request, hits, now_ms, **overrides) -> PropFirmRuleSnapshot:
    values = {
        "provider_id": provider.provider_id,
        "provider_name": provider.display_name,
        "official_domains": provider.official_domains,
        "retrieved_at_ms": now_ms,
        "source_urls": (hits[0].url,),
        "program_name": request.program_hint,
        "account_size": request.account_size_hint,
        "allowed_roots": ("MNQ",),
        "max_contracts_by_root": {"MNQ": 1},
        "news_trading_allowed": False,
        "overnight_holding_allowed": False,
        "weekend_holding_allowed": False,
        "daily_loss_rule_present": True,
        "drawdown_rule_present": True,
    }
    values.update(overrides)
    return PropFirmRuleSnapshot(**values)


def test_registry_identifies_prop_firm_from_exact_provider_segment() -> None:
    status, record = PropFirmProviderRegistry().resolve("Topstep / Rithmic")
    assert status is PropFirmIdentityStatus.VERIFIED
    assert record is not None
    assert record.provider_id == "TOPSTEP"


def test_unknown_provider_searches_candidates_but_does_not_auto_trust_domain() -> None:
    search = FakeSearch(
        (
            PropFirmSearchHit(
                title="Unknown firm",
                url="https://unknown-example.test/rules",
                content="claims to be official",
            ),
        )
    )
    engine = PropFirmRuleResearchEngine(
        registry=PropFirmProviderRegistry(),
        search_client=search,
        extractor=FakeExtractor(_snapshot),
    )
    result = engine.research(
        request=PropFirmRuleResearchRequest(provider_label="Brand New Firm"),
        now_ms=NOW_MS,
    )
    assert result.status is PropFirmRuleResearchStatus.PROVIDER_IDENTITY_UNKNOWN
    assert result.ready is False
    assert result.candidate_sources


def test_research_accepts_only_current_official_provider_domains() -> None:
    search = FakeSearch((_topstep_hit(),))
    engine = PropFirmRuleResearchEngine(
        registry=PropFirmProviderRegistry(),
        search_client=search,
        extractor=FakeExtractor(_snapshot),
    )
    result = engine.research(
        request=PropFirmRuleResearchRequest(
            provider_label="Topstep",
            program_hint="Trading Combine",
            account_size_hint="50K",
        ),
        now_ms=NOW_MS,
    )
    assert result.status is PropFirmRuleResearchStatus.READY
    assert result.snapshot is not None
    assert result.snapshot.provider_id == "TOPSTEP"
    assert search.calls[-1]["include_domains"] == ("topstep.com", "help.topstep.com")


def test_nonofficial_snapshot_source_is_rejected_even_if_extractor_returns_it() -> None:
    search = FakeSearch((_topstep_hit(),))

    def bad_snapshot(provider, request, hits, now_ms):
        return PropFirmRuleSnapshot(
            provider_id=provider.provider_id,
            provider_name=provider.display_name,
            official_domains=provider.official_domains,
            retrieved_at_ms=now_ms,
            source_urls=("https://reddit.com/r/example",),
        )

    engine = PropFirmRuleResearchEngine(
        registry=PropFirmProviderRegistry(),
        search_client=search,
        extractor=FakeExtractor(bad_snapshot),
    )
    result = engine.research(
        request=PropFirmRuleResearchRequest(provider_label="Topstep"),
        now_ms=NOW_MS,
    )
    assert result.status is PropFirmRuleResearchStatus.UNTRUSTED_SOURCE


def _personal(alias: str, cap: int = 5) -> NinjaTraderAccountRuleProfile:
    return NinjaTraderAccountRuleProfile(
        account_alias=alias,
        root_map={"NASDAQ": "NQ"},
        risk_fraction=0.03,
        allowed_roots=("NQ",),
        max_contracts_by_root={"NQ": cap},
        configured_classification=AccountClassification.PERSONAL,
        classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
    )


def _prop(alias: str, cap: int = 5, *, with_drawdown: bool = True) -> NinjaTraderAccountRuleProfile:
    return NinjaTraderAccountRuleProfile(
        account_alias=alias,
        root_map={"NASDAQ": "MNQ"},
        risk_fraction=0.03,
        allowed_roots=("MNQ",),
        max_contracts_by_root={"MNQ": cap},
        configured_classification=AccountClassification.PROP_FIRM,
        classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
        prop_limits=PropFirmRiskLimits(
            daily_loss_limit=3_000,
            daily_loss_used=500,
            remaining_drawdown_buffer=(1_800 if with_drawdown else None),
            nominal_account_size=100_000,
        ),
        news_trading_allowed=True,
        overnight_holding_allowed=True,
        weekend_holding_allowed=True,
    )


def _phase27(search: FakeSearch, extractor: FakeExtractor, accounts: list[dict]):
    research = PropFirmRuleResearchEngine(
        registry=PropFirmProviderRegistry(),
        search_client=search,
        extractor=extractor,
    )
    phase26 = FakePhase26()
    engine = LondresPhase27PropFirmRuleResearchEngine(
        phase24=FakePhase24(accounts),
        phase26=phase26,
        research=research,
        refresh_policy=PropFirmRuleRefreshPolicy(rules_max_age_ms=86_400_000),
    )
    return engine, phase26


def test_two_personal_and_one_prop_auto_tightens_only_prop_policy() -> None:
    search = FakeSearch((_topstep_hit(),))
    extractor = FakeExtractor(_snapshot)
    accounts = [
        {
            "account_alias": "NT-A",
            "provider": "Personal Broker A",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
        {
            "account_alias": "NT-B",
            "provider": "Personal Broker B",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
        {
            "account_alias": "NT-C",
            "provider": "Topstep / Rithmic",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
    ]
    engine, phase26 = _phase27(search, extractor, accounts)
    result = engine.prepare(
        intent=_intent(),
        profiles=(_personal("NT-A", 5), _personal("NT-B", 10), _prop("NT-C", 5)),
        rule_context=NinjaTraderTradeRuleContext(
            high_impact_news_window=False,
            will_hold_overnight=False,
            will_hold_weekend=False,
        ),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    adapted = {profile.account_alias: profile for profile in phase26.last_profiles}
    assert adapted["NT-A"].max_contracts_by_root["NQ"] == 5
    assert adapted["NT-B"].max_contracts_by_root["NQ"] == 10
    assert adapted["NT-C"].max_contracts_by_root["MNQ"] == 1
    assert adapted["NT-C"].news_trading_allowed is False
    assert adapted["NT-C"].overnight_holding_allowed is False
    assert adapted["NT-C"].weekend_holding_allowed is False
    prop_plan = next(item for item in result["accounts"] if item["account_alias"] == "NT-C")
    assert prop_plan["provider_id"] == "TOPSTEP"
    assert "MAX_CONTRACTS_MNQ_TIGHTENED_FROM_OFFICIAL_RULES" in prop_plan[
        "policy_adjustments"
    ]


def test_unknown_account_classification_remains_blocked_even_if_provider_is_known() -> None:
    search = FakeSearch((_topstep_hit(),))
    extractor = FakeExtractor(_snapshot)
    accounts = [
        {
            "account_alias": "NT-C",
            "provider": "Topstep",
            "detected_classification": None,
            "classification_metadata_verified": False,
        }
    ]
    engine, _ = _phase27(search, extractor, accounts)
    profile = NinjaTraderAccountRuleProfile(
        account_alias="NT-C",
        root_map={"NASDAQ": "MNQ"},
        risk_fraction=0.03,
        allowed_roots=("MNQ",),
        max_contracts_by_root={"MNQ": 5},
    )
    result = engine.prepare(
        intent=_intent(),
        profiles=(profile,),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    account = result["accounts"][0]
    assert account["status"] == Phase27AccountStatus.BLOCKED_ACCOUNT_CLASSIFICATION.value
    assert account["research_state"]["status"] == "READY"
    assert account["preparation_ready"] is False


def test_official_drawdown_rule_requires_current_dynamic_drawdown_buffer() -> None:
    search = FakeSearch((_topstep_hit(),))
    extractor = FakeExtractor(_snapshot)
    accounts = [
        {
            "account_alias": "NT-C",
            "provider": "Topstep",
            "detected_classification": None,
            "classification_metadata_verified": False,
        }
    ]
    engine, _ = _phase27(search, extractor, accounts)
    result = engine.prepare(
        intent=_intent(),
        profiles=(_prop("NT-C", with_drawdown=False),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_REQUIRED_PROP_RISK_DATA"


def test_best_effort_isolates_unknown_prop_provider_but_all_or_none_blocks_batch() -> None:
    search = FakeSearch((_topstep_hit(),))
    extractor = FakeExtractor(_snapshot)
    accounts = [
        {
            "account_alias": "NT-A",
            "provider": "Personal Broker",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
        {
            "account_alias": "NT-X",
            "provider": "Unknown Prop Brand",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
    ]
    engine, _ = _phase27(search, extractor, accounts)
    profiles = (_personal("NT-A"), _prop("NT-X"))
    best = engine.prepare(
        intent=_intent(),
        profiles=profiles,
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.BEST_EFFORT,
    )
    strict = engine.prepare(
        intent=_intent(),
        profiles=profiles,
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    assert best["status"] == "PARTIAL_READY"
    assert best["batch_ready_for_future_execution"] is True
    assert strict["status"] == "BLOCKED"
    assert strict["batch_ready_for_future_execution"] is False
