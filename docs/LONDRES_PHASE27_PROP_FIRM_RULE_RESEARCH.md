# Londres Phase 27 — automatic prop-firm identification, official rule research and adaptation

Phase 27 lets Londres identify the **prop-firm brand** for a verified prop account, search the firm's current official rule pages, convert the evidence into a structured rule snapshot, and tighten that account's Phase 26 policy before it remains preparation-ready.

It does **not** infer that an account is prop merely because of a provider name. Account classification remains per-account and comes from the existing Phase 23/24 verified classification path. This matters because a single NinjaTrader installation may contain personal accounts and prop accounts at the same time, and connection vendors such as Rithmic/Tradovate are not themselves proof of prop ownership.

## Runtime sequence

```text
NinjaTrader account
    ↓
per-account PERSONAL / PROP_FIRM classification
    ↓
PROP_FIRM only
    ↓
provider metadata -> provider registry identity
    ↓
current web search restricted to verified official provider domains
    ↓
structured rule extraction with source URLs
    ↓
deterministic source/domain validation
    ↓
auto-tighten Phase 26 account policy
    ↓
Phase 26 -> Phase 24 -> Phase 22 -> Phase 23 preparation
```

Personal accounts bypass prop-firm rule research and continue through the normal account-local policy path.

## Supported provider identity registry

Phase 27 ships with official-domain identity records for:

- Topstep — `topstep.com`, `help.topstep.com`
- Apex Trader Funding — `apextraderfunding.com`
- My Funded Futures — `myfundedfutures.com`, `help.myfundedfutures.com`
- Take Profit Trader — `takeprofittrader.com`

The registry is extensible. Provider matching is exact/segment-based, not fuzzy substring matching. A generic connection label such as `Rithmic` does not become a prop firm.

For an unknown provider, the search client may gather candidate web results, but Phase 27 returns `PROVIDER_IDENTITY_UNKNOWN` and blocks rule adaptation until the trusted official domain is added/verified. This prevents a search-engine result, affiliate page, forum, or impersonation domain from becoming an execution rule source.

## Web search

The included `TavilyPropFirmRuleSearchClient` uses Tavily's Search API through the repository's existing `requests` dependency.

Configure privately:

```text
TAVILY_API_KEY=...
```

For a known provider, every rule search is restricted with `include_domains` to the registry's official domains. Search credentials are never emitted in public output or AgentState.

The research layer is pluggable: any implementation of `PropFirmRuleSearchClient` can replace Tavily without changing Phase 27.

## Rule extraction

`JsonLLMPropFirmRuleExtractor` accepts the project's configured LangChain-compatible chat model and requires one strict JSON object. The prompt instructs the model to:

- use only the supplied official-source evidence;
- return `null` rather than inventing unavailable values;
- mark `ambiguous=true` when plans/programs conflict and the supplied hints do not disambiguate them;
- identify source URLs used for every snapshot;
- never use nominal advertised prop account size as sizing equity.

The LLM is **not** an authorization layer. The deterministic research engine rejects the snapshot if:

- provider identity does not match the verified registry record;
- any source URL is outside the official domains;
- a cited source was not present in the current search evidence;
- the rule set is materially ambiguous.

## Program/account hints

Prop rules often differ by evaluation/funded/live program, account size, or legacy/new product generation. Phase 27 therefore accepts optional per-account hints:

```python
PropFirmResearchHint(
    account_alias="NT-...",
    program_hint="Trading Combine",
    account_size_hint="50K",
)
```

If official sources show materially different rules and the hints are insufficient, the account fails closed with `RULES_AMBIGUOUS` rather than choosing a plan.

## Automatic adaptation semantics

Automatic adaptation is deliberately one-way: **tighten, never silently relax**.

Examples:

- local cap `MNQ=5`, current official cap `MNQ=2` -> Phase 27 changes the effective cap to `2`;
- local cap `MNQ=2`, official cap `MNQ=5` -> Londres keeps `2`;
- local `news_trading_allowed=True`, official rule says no -> effective value becomes `False`;
- local `news_trading_allowed=False`, official rule says yes -> remains `False`;
- local allowed roots intersect current official allowed roots; no overlap -> block;
- discovered consistency/scaling requirements are carried forward as required rules. If Phase 26 does not yet have the provider-specific metric/context needed to enforce them, Phase 26 blocks rather than ignoring them.

This prevents a changed website or extraction error from automatically making an account less restrictive.

## Dynamic daily-loss / drawdown data

Official web research identifies whether daily-loss and drawdown controls are part of the program, but it does **not** substitute static website numbers for current account state.

For prop sizing, the existing invariant remains:

```text
remaining_daily_loss_buffer = daily_loss_limit - daily_loss_used
risk_base = min(remaining_daily_loss_buffer, remaining_drawdown_buffer when applicable)
```

If current official rules require a drawdown control but the account's **current remaining drawdown buffer** is unavailable, Phase 27 blocks with `BLOCKED_REQUIRED_PROP_RISK_DATA`.

Advertised `$50K/$100K/$150K` account size remains metadata only and is never used as tradable equity.

## Freshness / cache

`PropFirmRuleRefreshPolicy` requires an explicit positive `rules_max_age_ms`.

Fresh verified research can be cached in memory for that interval. `refresh_on_each_prepare=True` forces a new official-domain search each preparation cycle. No implicit freshness interval is invented.

## Multi-account behavior

For a single NinjaTrader installation:

```text
Personal A -> no prop research -> own account policy -> independent sizing
Personal B -> no prop research -> own account policy -> independent sizing
Prop C     -> identify firm -> current official rules -> tightened policy -> prop risk buffer sizing
```

`BEST_EFFORT` isolates a prop account whose provider/rules cannot be verified while healthy accounts remain preparation-ready.

`ALL_OR_NONE` blocks the batch when any enabled account fails Phase 27.

## Security / execution boundary

Always preserved:

```text
read_only = true
execution_enabled = false
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

Phase 27 performs identification, research, policy adaptation and validation only. It does not submit, amend, cancel, flatten or close NinjaTrader orders.

## Current limitations

- a newly encountered provider/domain requires one-time trusted-domain verification before automatic adaptation;
- rules that require live performance-history metrics (for example some consistency/scaling formulas) remain fail-closed until the exact provider-specific metric is implemented;
- current research is in-memory cached, not yet persisted across process restarts;
- web research requires a configured search client/API credential;
- no automated demo/live broker execution is added.
