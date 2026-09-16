# Londres Phase 23 — Mixed Personal and Prop-Firm Account Risk Bases

Phase 23 allows one validated Londres `TradeIntent` to be prepared across a mixture of personal brokerage accounts and prop-firm accounts on the same platform, including the intended NinjaTrader use case of two real accounts plus one prop account.

It does **not** place, amend, partially close, or close broker orders.

## Core invariant

Account classification is **per account**, not per platform or per NinjaTrader connection.

A single NinjaTrader installation may therefore contain:

- Account A — `PERSONAL`
- Account B — `PERSONAL`
- Account C — `PROP_FIRM`

All three may receive the same Londres trade intent, while each account is independently risk-sized.

## Classification

Phase 23 supports these classifications:

- `PERSONAL`
- `PROP_FIRM`
- `UNKNOWN`

Supported classification sources are:

- `USER_CONFIRMED_CONFIG`
- `VERIFIED_PROVIDER_METADATA`
- `PRIVATE_PROVIDER_REGISTRY`
- `UNKNOWN`

The engine never decides that an account is a prop account merely because:

- it is connected through NinjaTrader;
- its balance resembles a common prop account size;
- its account name contains a guessed naming pattern.

If the configured classification and a future provider-detected classification disagree, the account fails closed with a classification conflict. If no verified classification exists, the account remains `UNKNOWN` and position sizing is blocked.

## Personal account risk base

For a personal account:

```text
risk_base = actual_account_equity
trade_risk_budget = risk_base × selected Londres tier
```

The selected Londres tier remains exactly one of 3%, 5%, or 10%.

## Prop-firm account risk base

The advertised/nominal prop account size is descriptive metadata only and is never used by the sizing engine.

For a prop account:

```text
remaining_daily_loss_buffer = daily_loss_limit - daily_loss_used

risk_base = min(
    remaining_daily_loss_buffer,
    remaining_max_or_trailing_drawdown_buffer  # when applicable
)

trade_risk_budget = risk_base × selected Londres tier
```

If no separate max/trailing-drawdown buffer applies or can be verified, the remaining daily-loss buffer is the risk base.

Examples:

```text
Nominal prop size:             $100,000   # metadata only
Daily loss limit:                $3,000
Daily loss already used:           $500
Remaining daily-loss buffer:     $2,500
Remaining trailing DD buffer:    $1,500

Phase 23 risk base:              $1,500
3% Londres risk budget:             $45
```

The engine does not calculate 3% of $100,000 for this prop account.

## Reusing Phase 20 safely

Phase 23 resolves the account-specific risk base first. It then creates an internal read-only risk-base view of the broker adapter and delegates the remaining preparation to Phase 20:

- explicit broker symbol mapping;
- broker capabilities;
- verified tick value in account currency;
- stop-distance risk calculation;
- broker min/max/step validation;
- target geometry;
- exact 60/40 hold split validation;
- fill-aware break-even metadata.

The original account snapshot remains unchanged. Phase 23 separately preserves actual account equity and the risk base used for sizing.

## Mixed-account example

For the same NQ trade:

```text
Personal A
actual equity = $10,000
risk base = $10,000

Personal B
actual equity = $20,000
risk base = $20,000

Prop C
actual account equity = $100,000
nominal account size = $100,000
remaining daily-loss buffer = $2,500
remaining drawdown buffer = $1,500
risk base = $1,500
```

Each account receives a separately calculated contract quantity. Raw contract counts are never copied from one account to another.

## Orchestration policies

`BEST_EFFORT` isolates account failures. If the prop account has an exhausted loss buffer but both personal accounts are valid, the personal accounts may remain preparation-ready.

`ALL_OR_NONE` requires every enabled account to pass classification, risk-base resolution, and Phase 20 preparation.

## Fail-closed conditions

Examples include:

- unknown account classification;
- configured/provider classification conflict;
- missing prop daily-loss-limit data;
- exhausted daily-loss buffer;
- exhausted max/trailing-drawdown buffer;
- Phase 20 symbol/capability/tick-value/risk/target/60-40 failure.

No blocked condition silently falls back to nominal prop account size or actual prop account equity.

## Security and execution boundary

Public state continues to hide Demo/Live classification and full account credentials/identifiers.

Phase 23 always emits:

```text
execution_enabled = false
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

Phase 23 prepares account-specific risk and replication semantics only. A concrete NinjaTrader transport/account adapter and any future order-submission layer remain separate phases.
