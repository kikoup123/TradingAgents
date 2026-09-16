# Londres Phase 28 — Dynamic Prop-Firm Risk Telemetry

Phase 28 adds the live account-risk state that Phase 27 deliberately did not invent.

Phase 27 can identify a verified prop firm and research current official rule pages. Those pages define the rule contract, but they do not provide the current per-account values needed for deterministic sizing, such as today's loss already used or the remaining trailing/max-drawdown buffer.

Phase 28 supplies that account-local state through a separate verified telemetry source before Phase 27/26/24/23 may prepare the account.

## Core invariant

Personal brokerage accounts continue to use actual account equity.

Prop-firm accounts never use advertised nominal account size as tradable equity. Their sizing base remains:

```text
remaining_daily_loss_buffer = daily_loss_limit - daily_loss_used

prop_risk_equity = min(
    remaining_daily_loss_buffer,
    remaining_drawdown_buffer,  # when the provider/program uses one
)
```

Then Londres applies the selected 3% / 5% / 10% risk tier to `prop_risk_equity`.

If the resulting budget cannot support one valid contract at the structural stop distance, the account does not trade. Londres does not round up and does not silently substitute a Micro contract for a standard contract.

## Why telemetry is separate from generic NinjaTrader P/L

Prop firms differ in:

- daily-loss reset time;
- whether unrealized P/L is included;
- whether commissions/fees are included;
- trailing versus static drawdown;
- intraday versus end-of-day drawdown;
- funded/evaluation/program-specific formulas.

Therefore Phase 28 does **not** derive `daily_loss_used` or `remaining_drawdown_buffer` from generic NinjaTrader realized/unrealized P/L. A provider-specific companion/dashboard/API integration must supply explicit verified values.

Optional realized/unrealized P/L observations may be attached for audit, but they do not control the prop risk formula.

## Telemetry contract

`tradingagents/brokers/prop_risk_telemetry.py` defines:

- `PropRiskTelemetrySnapshot`
- `PropRiskTelemetryPolicy`
- `PropRiskTelemetryValidator`
- `PropRiskTelemetrySource`
- `JsonPropRiskTelemetrySource`

A snapshot binds the state to:

- one Londres/NinjaTrader `account_alias`;
- one verified `provider_id`;
- one account currency;
- one source timestamp;
- one verified source;
- explicit daily-loss and optional drawdown values.

The source must be fresh according to an explicit `max_age_ms`. No default freshness interval is invented.

## Local JSON handoff

A provider-specific companion can atomically maintain a private file like:

```json
{
  "schema_version": 1,
  "accounts": [
    {
      "account_alias": "NT-AB12CD34",
      "provider_id": "TOPSTEP",
      "currency": "USD",
      "as_of_ms": 1800000000000,
      "source": "VERIFIED_PROVIDER_DASHBOARD_COMPANION",
      "source_verified": true,
      "daily_loss_limit": 3000.0,
      "daily_loss_used": 500.0,
      "remaining_drawdown_buffer": 1800.0,
      "nominal_account_size": 100000.0,
      "program_name": "Funded Account",
      "account_size": "100K"
    }
  ]
}
```

The nominal account size is retained only as descriptive metadata.

## Validation gates

Phase 28 blocks a prop account when telemetry is:

- missing;
- from an unverified source;
- for the wrong account alias;
- for the wrong prop provider;
- in the wrong account currency;
- stale;
- timestamped implausibly in the future;
- invalid/non-finite.

Unknown or conflicting PERSONAL/PROP_FIRM classification also remains blocked.

## Automatic program hinting

If verified telemetry includes `program_name` or `account_size`, Phase 28 can pass those values to Phase 27 as research hints. This helps Phase 27 disambiguate official rules that differ between evaluation/funded programs or account sizes.

An explicit user-supplied research hint still takes precedence.

## Mixed-account behavior

For one NinjaTrader installation:

```text
Personal A -> no prop telemetry -> actual account equity
Personal B -> no prop telemetry -> actual account equity
Prop C     -> fresh verified telemetry -> prop risk equity -> Phase 27 official rules
```

With `BEST_EFFORT`, stale/missing prop telemetry can isolate only the affected prop account while healthy personal accounts remain preparation-ready.

With `ALL_OR_NONE`, any blocked enabled account blocks the batch.

## Security boundary

Phase 28 remains preparation-only:

```text
read_only = true
execution_enabled = false
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

The JSON telemetry handoff contains no provider credentials. Any provider API/session credential stays inside the future companion integration and never enters AgentState.

## Still not implemented here

Phase 28 does not itself scrape private provider dashboards or authenticate to prop-firm portals. It defines and validates the safe handoff contract those provider-specific connectors must satisfy.

It also does not implement provider-specific consistency, scaling or payout-history calculations yet. Those can be added once the necessary verified performance-history telemetry is available.
