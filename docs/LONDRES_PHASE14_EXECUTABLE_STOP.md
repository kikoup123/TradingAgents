# Londres Phase 14 — Executable stop buffer

Phase 14 converts the Trader's already hard-validated structural stop choice into an executable broker stop price.

## Inputs

Phase 14 requires all of the following:

- Phase 13 status `ENTRY_TRIGGERED` and its exact entry price;
- a valid Trader structural stop selection from `IOF_RANGE` or `SMT_PROTECTED`;
- the broker symbol's exact tick size;
- an explicit stop-buffer policy in whole ticks.

No default buffer is invented. If the buffer policy has not yet been configured, the engine returns `WAIT_FOR_BUFFER_POLICY` and risk sizing remains blocked.

## Directional rule

For a bearish trade:

`executable stop = structural anchor + explicit buffer`

For a bullish trade:

`executable stop = structural anchor - explicit buffer`

The result is then snapped **outward** to the broker tick grid so rounding can never move the stop closer to the entry than the requested structural buffer.

The structural anchor must already be beyond the entry in the invalidation direction. A bearish anchor at or below the entry, or a bullish anchor at or above the entry, fails closed as invalid geometry.

## Buffer policy

The buffer must be at least one whole broker tick. This enforces the existing structural rule that the executable stop belongs beyond the IOF range high/low or SMT protected extreme, not directly on the structural boundary.

The actual number of ticks is deliberately not hard-coded in Phase 14. It remains an explicit symbol/execution policy input so NASDAQ, gold, index CFDs, futures, and other instruments can use the correct broker-specific convention.

## Output

Phase 14 records:

- exact Phase 13 entry;
- selected structural stop source;
- structural anchor price;
- placement rule;
- broker tick size;
- configured buffer ticks and price distance;
- executable stop price;
- entry-to-stop price distance;
- entry-to-stop tick distance;
- whether deterministic risk sizing may proceed.

`order_authorized` remains false. Phase 14 does not place orders and does not choose position size. Once it is `READY`, Phase 11 risk sizing can use the exact entry-to-executable-stop distance together with the selected 3%, 5%, or 10% account-risk tier and broker tick value.
