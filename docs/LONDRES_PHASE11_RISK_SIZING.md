# Londres Phase 11 — Stop-range risk sizing

Phase 11 introduces deterministic position sizing from the actual structural stop range. The Trader is not allowed to choose lots, contracts, portfolio percentage, or any other trading volume on the Londres path.

## Core rule

`SMT -> CSD -> IOF -> entry -> selected structural stop -> executable stop -> measure pips/ticks -> choose approved account-risk tier -> calculate safe volume`

A wider stop produces a smaller position. A tighter stop may produce a larger position only while projected cash risk remains inside the selected account-risk tier.

## Approved Trader risk tiers

For an active Londres trade, the Trader may select exactly one of:

- `3%` of account equity;
- `5%` of account equity;
- `10%` of account equity.

`10%` is the absolute hard ceiling. No 1%, 2%, 4%, 6%, 11%, or arbitrary custom percentage is valid on this execution path.

The Trader chooses the risk tier, but never chooses the lot/contract size. Position size remains fully deterministic from the actual stop range.

## Sizing formula

The broker adapter supplies:

- tick size;
- cash value per tick per volume unit;
- volume step;
- minimum volume;
- maximum volume;
- optional pip size for pip reporting.

The risk policy supplies:

- account equity;
- one approved risk fraction: `0.03`, `0.05`, or `0.10`;
- optional hard cash-risk cap that can only reduce the percentage budget.

The engine calculates:

1. `price_distance = abs(entry - executable_stop)`
2. `ticks = ceil(price_distance / tick_size)`
3. `risk_per_volume_unit = ticks * tick_value_per_volume_unit`
4. `cash_risk_budget = min(account_equity * selected_risk_fraction, optional_cash_cap)`
5. `raw_volume = cash_risk_budget / risk_per_volume_unit`
6. cap at broker maximum;
7. round DOWN to the broker volume step;
8. verify projected cash risk remains at or below both the selected risk budget and the 10% hard ceiling.

When a pip size exists, the engine also reports the entry-to-stop distance in pips.

## Safety behavior

- Manual/LLM volume is forbidden.
- The Londres Trader validator clears any LLM-generated `position_sizing` field.
- Active trades without one of the approved 3% / 5% / 10% tiers fail closed to HOLD.
- Missing exact entry -> WAIT.
- Missing executable stop -> WAIT.
- Stop on the wrong side of the entry -> invalid geometry.
- Safe calculated size below broker minimum -> no trade; the engine does not round up and exceed the risk budget.
- Price distance is rounded UP to whole ticks for risk calculation.
- Position volume is rounded DOWN to the broker step.
- Projected account risk may never exceed 10%.
- `order_authorized` remains false; this phase does not place broker orders.

## Structural stop relationship

Phase 10 still gives the Trader discretion between the two approved structural stop anchors:

- `IOF_RANGE`
- `SMT_PROTECTED`

Phase 11 does not change that choice. It converts the final executable stop derived from the selected anchor into a deterministic risk range and safe volume.

The symbol-specific buffer beyond the structural anchor must be resolved before final sizing. Until that executable stop exists, the engine refuses to size from the anchor alone.

## Separation of discretion and authority

The Trader has two bounded discretionary choices:

1. structural stop source: `IOF_RANGE` or `SMT_PROTECTED` when both are valid;
2. account-risk tier: `3%`, `5%`, or `10%`.

The Trader does **not** control the resulting volume. The risk engine is the only sizing authority and computes the maximum broker-valid size from the exact entry-to-stop distance.
