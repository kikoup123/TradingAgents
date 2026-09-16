# Londres Phase 26 — Per-account NinjaTrader futures and prop-firm rule profiles

Phase 26 adds a deterministic **account-local policy layer** above Phase 24/25. It is designed for mixed NinjaTrader installations where the same Londres trade intent may be replicated to, for example, two personal brokerage accounts and one prop-firm account.

The policy is evaluated **per account**. NinjaTrader itself is never classified as personal or prop.

## Core invariant

Londres replicates the trade intent, not a raw futures quantity.

```text
ONE LONDRES TRADE INTENT
        ↓
Phase 26 account policies
        ↓
Account A — PERSONAL — NQ — cap 4
Account B — PERSONAL — NQ — cap 8
Account C — PROP_FIRM — MNQ — cap 3
        ↓
Each account is independently risk-sized by Phase 23/20
```

If an independently calculated quantity exceeds an account-specific cap, Phase 26 **blocks that account**. It does not silently reduce the quantity to the maximum.

## Personal vs prop risk base

Phase 26 retains the Phase 23 rule exactly.

### Personal account

```text
risk_base = actual account equity
```

### Prop-firm account

```text
remaining_daily_loss_buffer = daily_loss_limit - daily_loss_used
risk_base = min(
    remaining_daily_loss_buffer,
    remaining max/trailing drawdown buffer when present
)
```

The advertised or nominal `$50K / $100K / $150K` prop account size is descriptive metadata only and is never used as tradable sizing equity.

## Per-account policy object

`NinjaTraderAccountRuleProfile` binds the following to one account alias:

- explicit canonical-to-futures-root map;
- 3% / 5% / 10% Londres risk tier;
- PERSONAL / PROP_FIRM classification evidence;
- prop daily-loss and drawdown buffers;
- explicitly allowed futures roots;
- explicit maximum contracts per root;
- optional maximum cash-risk cap;
- optional allowed trading sessions;
- optional news-trading rule;
- optional overnight-holding rule;
- optional weekend-holding rule;
- explicit placeholders for consistency and scaling rules.

Unknown policy inputs fail closed when they are required.

## Explicit futures roots and caps

Example account policies:

```text
Personal A
NASDAQ -> NQ
allowed roots: NQ
max NQ contracts: 4

Personal B
NASDAQ -> NQ
allowed roots: NQ
max NQ contracts: 8

Prop C
NASDAQ -> MNQ
allowed roots: MNQ
max MNQ contracts: 3
```

Standard and Micro contracts are never silently substituted. If an account is mapped to NQ, Phase 26 does not replace it with MNQ simply because NQ does not fit the risk budget or contract cap.

## Contract-cap behavior

Example:

```text
Phase 23/20 risk-sized result: 2 MNQ
Phase 26 account cap:          1 MNQ
```

Result:

```text
BLOCKED_MAX_CONTRACTS
```

The account is blocked. Londres does **not** change `2 MNQ` to `1 MNQ` silently.

That preserves the original risk calculation and makes any future resize policy an explicit separate decision.

## Session, news, overnight and weekend rules

`NinjaTraderTradeRuleContext` carries verified facts used by optional account rules:

- current session;
- whether the intended execution is inside a high-impact-news window;
- whether the position is intended to remain open overnight;
- whether the position is intended to remain open over the weekend.

If an account policy requires one of those facts and the fact is unavailable, the result is:

```text
BLOCKED_RULE_CONTEXT
```

If the fact is known and violates the account policy, the result is:

```text
BLOCKED_RULE_VIOLATION
```

Examples:

- account allows only `NEW_YORK`, current session is `ASIA` -> blocked;
- prop account forbids high-impact-news trading and the setup is inside the verified news window -> blocked;
- overnight holding is prohibited and the trade is intended to remain open overnight -> blocked;
- weekend holding is prohibited and the position would remain open over the weekend -> blocked.

Phase 26 never assumes a missing rule fact means permission.

## Consistency and scaling rules

Prop-firm consistency and scaling rules can be materially different across programs and may depend on account history, payout phase, prior profits, account age, contract history or provider-specific calculations.

Phase 26 therefore does not invent generic formulas for them.

If a profile sets either:

```text
consistency_rule_required = true
```

or:

```text
scaling_rule_required = true
```

before a verified implementation for that provider/account exists, Phase 26 returns:

```text
BLOCKED_UNSUPPORTED_REQUIRED_RULE
```

This makes unsupported provider rules visible and fail-closed rather than silently ignored.

## Integration order

Phase 26 runs above the existing NinjaTrader stack:

```text
NinjaTrader native read-only producer — Phase 25
        ↓
Phase 24 account discovery / futures rollover
        ↓
Phase 22 freshness supervision
        ↓
Phase 23 PERSONAL / PROP_FIRM risk base
        ↓
Phase 20 independent risk sizing
        ↓
Phase 26 per-account rule and contract-cap validation
```

The Phase 24/23 account plan is retained inside the Phase 26 result for auditability.

## BEST_EFFORT vs ALL_OR_NONE

### BEST_EFFORT

A blocked prop account does not invalidate healthy personal accounts.

Example:

```text
Personal A -> READY
Personal B -> READY
Prop C     -> BLOCKED_MAX_CONTRACTS

Batch -> PARTIAL_READY
```

### ALL_OR_NONE

Every enabled account must pass Phase 26.

With the same three account results:

```text
Batch -> BLOCKED
```

No broker order is submitted under either orchestration mode.

## Status model

Per-account Phase 26 statuses:

- `READY`
- `SKIPPED_DISABLED`
- `BLOCKED_POLICY_CONFIGURATION`
- `BLOCKED_ROOT_NOT_ALLOWED`
- `BLOCKED_RULE_CONTEXT`
- `BLOCKED_RULE_VIOLATION`
- `BLOCKED_UNSUPPORTED_REQUIRED_RULE`
- `BLOCKED_PHASE24`
- `BLOCKED_MAX_CONTRACTS`

## Safety boundary

Phase 26 preserves:

```text
read_only = true
execution_enabled = false
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

There is still no NinjaTrader order placement, amendment, cancel, partial-close or position-close path.

## Files

- `tradingagents/ict/phase26.py`
- `tests/test_phase26_ninjatrader_account_policy.py`
- `docs/LONDRES_PHASE26_NINJATRADER_ACCOUNT_POLICIES.md`

## Current limitations

Phase 26 does not yet implement provider-specific consistency/scaling formulas, payout rules, provider-specific news calendars, or provider-specific trailing-drawdown calculation feeds. Those must be added from verified provider/account rules rather than guessed.

The native Phase 25 bridge remains read-only. Actual NinjaTrader execution is still out of scope.
