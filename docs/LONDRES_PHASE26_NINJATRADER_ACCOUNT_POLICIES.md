# Londres Phase 26 — Per-Live-Account NinjaTrader Policy

Phase 26 adds deterministic policy checks for each live NinjaTrader brokerage account above Phase 24.

Each account can define its own canonical-symbol-to-futures-root mapping, allowed roots, maximum contracts by root, 3%/5%/10% equity risk tier, optional cash-risk cap, allowed sessions, high-impact-news permission, overnight holding permission, and weekend holding permission.

Risk sizing still occurs from the account's current broker-reported equity. If risk sizing produces more contracts than the explicit account cap, Phase 26 returns `BLOCKED_MAX_CONTRACTS`; it never silently reduces the position.

When an optional rule requires current context and that context is missing, Phase 26 fails closed with `BLOCKED_RULE_CONTEXT`. A known rule violation returns `BLOCKED_RULE_VIOLATION`.

`BEST_EFFORT` isolates a blocked account; `ALL_OR_NONE` blocks the whole batch when any enabled account fails. Phase 26 remains read-only and does not submit orders.
