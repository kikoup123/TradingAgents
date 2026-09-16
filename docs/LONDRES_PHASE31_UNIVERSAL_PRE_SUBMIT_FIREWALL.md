# Londres Phase 31 — Universal Immediate Pre-Submit Firewall

Phase 31 is the final read-only safety boundary before a future execution adapter.
It consumes only Phase 30-authorized account envelopes and immediately re-reads
current broker state for NinjaTrader, FP Markets cTrader, and Vantage MT5.

It does **not** submit, modify, cancel, close, or flatten broker orders.

## Required upstream state

The Phase 30 batch must match the `TradeIntent`, remain execution-handoff-ready,
carry `order_authorized=true`, and still have `order_submission_enabled=false`.
The current implementation is intentionally limited to `MARKET_ON_SIGNAL`.

Every passing account must still be `AUTHORIZED_FOR_UNIVERSAL_HANDOFF`, match the
exact trade id, canonical symbol, direction, entry, stop, target and exit mode,
and carry a valid 64-character SHA-256 Phase 30 authorization fingerprint.

## Immediate broker revalidation

Phase 31 binds each account alias to an explicit read-only broker adapter and
requires the adapter broker type to match the Phase 30 venue:

- NinjaTrader -> `NINJATRADER`
- FP Markets -> `CTRADER`
- Vantage -> `MT5`

The read-only adapter must still report `execution_enabled=false`.

Immediately before handoff, the universal broker supervisor re-reads:

- connection/account readability;
- current bid/ask and quote timestamp;
- current instrument metadata;
- verified tick value and its timestamp;
- current account equity.

Quote age, tick-value age, reconnect behavior and future-timestamp tolerance are
explicit `BrokerSupervisionPolicy` inputs. No hidden freshness defaults are
introduced by Phase 31.

## Broker identity and symbol binding

FP Markets and Vantage identities are reverified from the current account
snapshot. The normalized current broker identity must still match the exact
identity authorized in Phase 30 and the declared venue.

The broker symbol/contract is never guessed. Current instrument metadata must
reference the exact Phase 30 broker symbol and the same canonical instrument.
Verified account-currency tick value is required.

## NinjaTrader rollover firewall

For NinjaTrader, Phase 31 extracts the selected futures root from the upstream
Phase 27/26 envelope and calls the read-only adapter's active-contract resolver
again. The current rollover state must still be verified and the active contract
must equal the exact Phase 30 contract.

If rollover changes between authorization and pre-submit, the account is blocked
and must be re-authorized upstream.

## Exact volume-grid firewall

Phase 31 never silently resizes a position.

The exact Phase 30 lots/contracts must still satisfy the broker's current:

- volume unit;
- minimum volume;
- maximum volume;
- volume step.

NinjaTrader contract volume must remain an exact positive integer. If a broker
changes its lot/contract grid after authorization, Phase 31 blocks the account.

## Market-quality firewall

`Phase31PreSubmitPolicy` requires explicit non-negative integer limits for:

- maximum spread in ticks;
- maximum adverse entry deviation in ticks.

For `MARKET_ON_SIGNAL`, the executable quote is current ask for bullish orders
and current bid for bearish orders. The executable quote must still remain
strictly inside the stop/target geometry.

## Current-equity risk revalidation

Risk is recalculated at the current executable quote using the exact authorized
Phase 30 volume:

```text
stop_distance = abs(current_executable_price - stop_price)
risk_per_volume = (stop_distance / tick_size) * tick_value
projected_cash_risk = risk_per_volume * exact_authorized_volume
projected_equity_risk_fraction = projected_cash_risk / current_equity
```

The account is blocked if projected risk exceeds:

- its Phase 30 selected risk tier (exactly 3%, 5%, or 10%);
- the Londres hard 10% account maximum;
- an explicit per-account cash cap supplied in the Phase 31 binding;
- the stricter existing NinjaTrader Phase 26 cash cap when one is present.

The firewall blocks rather than silently shrinking the authorized size.

## Duplicate authorization defense

Phase 31 accepts an external registry of already-used Phase 30 authorization
fingerprints and also detects duplicates within the same batch. A duplicate
fingerprint is blocked before broker handoff.

Phase 31 itself does not mutate or consume the registry. Successful accounts
report `fingerprint_consumed=false`. Atomic reservation/consumption belongs in a
later execution-command layer where it can be coupled to actual broker submit
semantics.

## Pre-submit snapshot fingerprint

Every passing account receives a SHA-256 snapshot fingerprint bound to:

- Phase 30 authorization fingerprint;
- adapter id and account alias;
- venue and broker type;
- exact broker symbol;
- exact lots/contracts and volume unit;
- current account equity;
- current bid/ask and quote timestamp;
- current tick size and tick value;
- tick-value timestamp;
- Phase 31 revalidation timestamp.

This identifies the exact live broker/market state that passed the firewall.

## Batch policy

`BEST_EFFORT` isolates an account that fails the firewall while allowing other
passing accounts to remain ready.

`ALL_OR_NONE` revokes every otherwise-ready Phase 31 account if any enabled
account fails. Revoked accounts lose their Phase 31 snapshot fingerprint.

## Execution boundary

A passing Phase 31 account may expose:

```text
pre_submit_ready = true
order_authorized = true
```

but Phase 31 always preserves:

```text
execution_enabled = false
order_submission_enabled = false
broker_order_placed = false
fingerprint_registry_mutated = false
fingerprint_consumed = false
authorized_volume_resized = false
```

No execution surface is introduced in Phase 31.
