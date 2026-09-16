# Londres Phase 29 — Provider-specific prop compliance firewall

Phase 29 adds a final read-only compliance layer above Phase 28. Phase 27 researches current official prop-firm rules and Phase 28 supplies current provider-defined daily-loss/drawdown telemetry. Phase 29 adds current performance metrics needed for rules that cannot be decided from risk-buffer telemetry alone, while refusing to invent provider-specific formulas.

## Core invariant

A NinjaTrader installation may contain personal and prop accounts at the same time. Classification remains per account. Personal accounts bypass prop-specific compliance. Prop accounts must pass the researched rule contract and current verified account metrics independently.

Advertised nominal prop size is never used as tradable equity. The existing sizing invariant remains:

`remaining_daily_loss_buffer = daily_loss_limit - daily_loss_used`

and, when a drawdown rule applies:

`prop_risk_equity = min(remaining_daily_loss_buffer, remaining_drawdown_buffer)`

The configured 3% / 5% / 10% Londres risk tier is applied to that risk base.

## Performance metrics contract

`PropFirmLiveMetrics` can carry current account facts such as:

- daily loss already used;
- remaining drawdown buffer;
- realized and unrealized P/L observations;
- current open contract usage by futures root;
- trading-day count;
- cumulative profit;
- best-day profit;
- scaling level;
- payout-eligibility inputs.

Metrics have an explicit `observed_at_ms`. Phase 29 requires an explicit maximum age and fails closed when metrics are stale or future-dated.

## Exact provider formulas only

Consistency, scaling and similar provider-specific formulas are not inferred from marketing language or generic assumptions. `PropFirmFormulaRegistry` accepts explicit provider-specific formula adapters. When current official rules require a formula and no verified adapter exists, Phase 29 returns `BLOCKED_UNSUPPORTED_REQUIRED_FORMULA`.

This means a newly discovered consistency rule cannot be silently ignored and cannot be approximated.

## Direct deterministic checks

Rules that are directly expressible from verified structured data are enforced without an LLM decision:

- daily-loss exhaustion;
- max/trailing drawdown exhaustion;
- current contract-cap exhaustion;
- stale/missing required metrics;
- Phase 27 rule-program versus Phase 28 telemetry-program mismatch;
- account-size hint mismatch where both verified sources provide a value.

## Rule snapshot provenance cache

Phase 29 adds `PropFirmRuleCache` abstractions:

- `InMemoryPropFirmRuleCache` is the default;
- `JsonFilePropFirmRuleCache` is available only when the caller explicitly supplies a file path.

Cached entries contain only validated structured rule snapshots and provenance:

- provider/program/account-size key;
- fetched timestamp;
- expiration timestamp;
- source digest;
- verified rule snapshot.

No API keys, broker credentials, raw NinjaTrader account numbers or order data are stored. Expired, corrupted, or program/account-size-mismatched entries fail closed.

## Mixed-account orchestration

With `BEST_EFFORT`, a prop account that fails Phase 29 can be isolated while healthy personal/prop accounts remain preparation-ready. With `ALL_OR_NONE`, one blocked enabled account blocks the batch.

The same Londres trade intent is still replicated, never a raw contract count.

## Execution boundary

Phase 29 remains preparation-only:

- `read_only=true`
- `execution_enabled=false`
- `order_submission_enabled=false`
- `order_authorized=false`
- `broker_order_placed=false`

No NinjaTrader order placement, amendment, cancellation, flattening, ATM or close API is introduced.
