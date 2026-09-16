from __future__ import annotations

import json

from tradingagents.brokers import (
    AccountClassification,
    AccountClassificationSource,
    BrokerSupervisionPolicy,
    JsonPropRiskTelemetrySource,
    OrchestrationPolicy,
    PropFirmProviderRegistry,
    PropRiskTelemetryPolicy,
    PropRiskTelemetrySnapshot,
    PropRiskTelemetryStatus,
    PropRiskTelemetryValidator,
    TradeIntent,
)
from tradingagents.ict.phase26 import NinjaTraderAccountRuleProfile, NinjaTraderTradeRuleContext
from tradingagents.ict.phase28 import LondresPhase28DynamicPropRiskEngine, Phase28AccountStatus

NOW_MS = 1_800_000_000_000


class FakePhase24:
    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = accounts

    def discover(self) -> dict:
        return {"accounts": self.accounts}


class FakePhase27:
    def __init__(self, blocked_aliases: set[str] | None = None) -> None:
        self.blocked_aliases = blocked_aliases or set()
        self.last_profiles = ()
        self.last_hints = ()

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
        self.last_profiles = tuple(profiles)
        self.last_hints = tuple(research_hints)
        accounts = []
        for profile in profiles:
            ready = profile.account_alias not in self.blocked_aliases
            accounts.append(
                {
                    "account_alias": profile.account_alias,
                    "status": "READY" if ready else "BLOCKED_PHASE26",
                    "preparation_ready": ready,
                }
            )
        return {"accounts": accounts}


class FakeTelemetry:
    def __init__(self, snapshots: dict[tuple[str, str], PropRiskTelemetrySnapshot]) -> None:
        self.snapshots = snapshots
        self.calls: list[tuple[str, str]] = []

    def snapshot(self, *, account_alias: str, provider_id: str):
        key = (account_alias, provider_id)
        self.calls.append(key)
        return self.snapshots.get(key)


def _intent() -> TradeIntent:
    return TradeIntent(
        trade_id="phase28-nasdaq-short",
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
        risk_fraction=0.05,
        allowed_roots=("MNQ",),
        max_contracts_by_root={"MNQ": 10},
        configured_classification=AccountClassification.PROP_FIRM,
        classification_source=AccountClassificationSource.USER_CONFIRMED_CONFIG,
    )


def _telemetry(
    alias: str,
    *,
    provider_id: str = "TOPSTEP",
    as_of_ms: int = NOW_MS - 1_000,
    verified: bool = True,
    daily_loss_limit: float = 3_000.0,
    daily_loss_used: float = 500.0,
    remaining_drawdown: float | None = 1_800.0,
) -> PropRiskTelemetrySnapshot:
    return PropRiskTelemetrySnapshot(
        account_alias=alias,
        provider_id=provider_id,
        currency="USD",
        as_of_ms=as_of_ms,
        source="VERIFIED_PROVIDER_DASHBOARD_COMPANION",
        source_verified=verified,
        daily_loss_limit=daily_loss_limit,
        daily_loss_used=daily_loss_used,
        remaining_drawdown_buffer=remaining_drawdown,
        nominal_account_size=100_000.0,
        program_name="Funded Account",
        account_size="100K",
        realized_pnl_observed=-100.0,
        unrealized_pnl_observed=-50.0,
        daily_loss_window_id="provider-window-2027-01-15",
        drawdown_model="PROVIDER_SUPPLIED_REMAINING_BUFFER",
    )


def _accounts() -> list[dict]:
    return [
        {
            "account_alias": "NT-A",
            "provider": "Personal Broker A",
            "currency": "USD",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
        {
            "account_alias": "NT-B",
            "provider": "Personal Broker B",
            "currency": "USD",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
        {
            "account_alias": "NT-C",
            "provider": "Topstep / Rithmic",
            "currency": "USD",
            "detected_classification": None,
            "classification_metadata_verified": False,
        },
    ]


def _engine(source: FakeTelemetry, accounts: list[dict] | None = None, blocked=None):
    phase27 = FakePhase27(blocked_aliases=blocked)
    engine = LondresPhase28DynamicPropRiskEngine(
        phase24=FakePhase24(accounts or _accounts()),
        phase27=phase27,
        provider_registry=PropFirmProviderRegistry(),
        telemetry_source=source,
        telemetry_policy=PropRiskTelemetryPolicy(max_age_ms=10_000),
    )
    return engine, phase27


def test_validator_accepts_fresh_verified_state_and_never_uses_nominal_size() -> None:
    snapshot = _telemetry("NT-C")
    result = PropRiskTelemetryValidator().validate(
        snapshot=snapshot,
        account_alias="NT-C",
        provider_id="TOPSTEP",
        account_currency="USD",
        now_ms=NOW_MS,
        policy=PropRiskTelemetryPolicy(max_age_ms=10_000),
    )
    assert result.status is PropRiskTelemetryStatus.READY
    assert result.ready is True
    assert result.snapshot is not None
    assert result.snapshot.remaining_daily_loss_buffer == 2_500.0
    assert result.snapshot.tradable_risk_equity == 1_800.0
    public = result.snapshot.public_dict()
    assert public["nominal_account_size_used_for_sizing"] is False
    assert public["generic_pnl_used_to_infer_prop_limits"] is False


def test_validator_rejects_stale_and_unverified_telemetry() -> None:
    validator = PropRiskTelemetryValidator()
    stale = validator.validate(
        snapshot=_telemetry("NT-C", as_of_ms=NOW_MS - 20_000),
        account_alias="NT-C",
        provider_id="TOPSTEP",
        account_currency="USD",
        now_ms=NOW_MS,
        policy=PropRiskTelemetryPolicy(max_age_ms=10_000),
    )
    unverified = validator.validate(
        snapshot=_telemetry("NT-C", verified=False),
        account_alias="NT-C",
        provider_id="TOPSTEP",
        account_currency="USD",
        now_ms=NOW_MS,
        policy=PropRiskTelemetryPolicy(max_age_ms=10_000),
    )
    assert stale.status is PropRiskTelemetryStatus.STALE
    assert unverified.status is PropRiskTelemetryStatus.UNVERIFIED_SOURCE


def test_json_source_requires_exact_account_and_provider(tmp_path) -> None:
    path = tmp_path / "prop-risk.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "accounts": [
                    {
                        "account_alias": "NT-C",
                        "provider_id": "TOPSTEP",
                        "currency": "USD",
                        "as_of_ms": NOW_MS,
                        "source": "PROVIDER_API",
                        "source_verified": True,
                        "daily_loss_limit": 3000,
                        "daily_loss_used": 400,
                        "remaining_drawdown_buffer": 1700,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source = JsonPropRiskTelemetrySource(path)
    assert source.snapshot(account_alias="NT-C", provider_id="TOPSTEP") is not None
    assert source.snapshot(account_alias="NT-C", provider_id="APEX_TRADER_FUNDING") is None


def test_two_personal_and_one_prop_injects_only_prop_dynamic_risk() -> None:
    source = FakeTelemetry({("NT-C", "TOPSTEP"): _telemetry("NT-C")})
    engine, phase27 = _engine(source)
    result = engine.prepare(
        intent=_intent(),
        profiles=(_personal("NT-A"), _personal("NT-B"), _prop("NT-C")),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )

    assert result["status"] == "READY"
    assert result["ready_accounts"] == 3
    assert source.calls == [("NT-C", "TOPSTEP")]
    profiles = {item.account_alias: item for item in phase27.last_profiles}
    assert profiles["NT-A"].prop_limits is None
    assert profiles["NT-B"].prop_limits is None
    assert profiles["NT-C"].prop_limits is not None
    assert profiles["NT-C"].prop_limits.tradable_risk_equity == 1_800.0
    assert profiles["NT-C"].prop_limits.nominal_account_size == 100_000.0
    assert phase27.last_hints[0].account_alias == "NT-C"
    assert phase27.last_hints[0].program_hint == "Funded Account"
    assert phase27.last_hints[0].account_size_hint == "100K"

    prop = next(item for item in result["accounts"] if item["account_alias"] == "NT-C")
    assert prop["risk_telemetry_state"]["status"] == "READY"
    assert prop["effective_prop_risk_limits"]["tradable_risk_equity"] == 1_800.0
    assert prop["effective_prop_risk_limits"]["nominal_account_size_used_for_sizing"] is False


def test_missing_or_stale_prop_telemetry_blocks_prop_but_personal_can_continue() -> None:
    source = FakeTelemetry(
        {("NT-C", "TOPSTEP"): _telemetry("NT-C", as_of_ms=NOW_MS - 20_000)}
    )
    engine, _ = _engine(source)
    result = engine.prepare(
        intent=_intent(),
        profiles=(_personal("NT-A"), _prop("NT-C")),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.BEST_EFFORT,
    )
    assert result["status"] == "PARTIAL_READY"
    assert result["ready_accounts"] == 1
    prop = next(item for item in result["accounts"] if item["account_alias"] == "NT-C")
    assert prop["status"] == Phase28AccountStatus.BLOCKED_RISK_TELEMETRY.value
    assert prop["risk_telemetry_state"]["status"] == "STALE"


def test_all_or_none_blocks_batch_when_prop_telemetry_is_missing() -> None:
    source = FakeTelemetry({})
    engine, _ = _engine(source)
    result = engine.prepare(
        intent=_intent(),
        profiles=(_personal("NT-A"), _prop("NT-C")),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
        policy=OrchestrationPolicy.ALL_OR_NONE,
    )
    assert result["status"] == "BLOCKED"
    assert result["batch_ready_for_future_execution"] is False


def test_unknown_provider_identity_blocks_dynamic_prop_risk() -> None:
    accounts = [
        {
            "account_alias": "NT-X",
            "provider": "Unknown Prop Brand / Rithmic",
            "currency": "USD",
            "detected_classification": None,
            "classification_metadata_verified": False,
        }
    ]
    source = FakeTelemetry({})
    engine, _ = _engine(source, accounts=accounts)
    result = engine.prepare(
        intent=_intent(),
        profiles=(_prop("NT-X"),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PROVIDER_IDENTITY"


def test_phase27_block_after_telemetry_remains_fail_closed() -> None:
    source = FakeTelemetry({("NT-C", "TOPSTEP"): _telemetry("NT-C")})
    engine, _ = _engine(source, blocked={"NT-C"})
    result = engine.prepare(
        intent=_intent(),
        profiles=(_prop("NT-C"),),
        rule_context=NinjaTraderTradeRuleContext(),
        now_ms=NOW_MS,
        supervision_policy=_supervision(),
    )
    assert result["accounts"][0]["status"] == "BLOCKED_PHASE27"
    assert result["accounts"][0]["preparation_ready"] is False
