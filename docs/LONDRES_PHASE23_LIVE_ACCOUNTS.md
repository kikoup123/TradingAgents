# Londres Phase 23 — Live Account Risk and Multi-Account Replication

Phase 23 is live-account-only. Every enabled brokerage account uses its current broker-reported equity as the sizing base.

For each account:

`cash risk = current account equity × selected risk tier`

The only accepted risk tiers are 3%, 5%, and 10%, with 10% remaining the hard ceiling. Londres never copies a raw lot or contract quantity from another account. The same validated `TradeIntent` is replicated and every account calculates its own volume from its equity, stop distance, verified tick value, and broker volume grid.

`BEST_EFFORT` allows healthy accounts to remain preparation-ready when another account fails. `ALL_OR_NONE` requires every enabled account to pass.

Phase 23 does not submit orders. `order_authorized=false` and `broker_order_placed=false` remain hard boundaries.
