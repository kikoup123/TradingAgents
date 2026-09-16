# Londres Phase 11 — Stop-range risk sizing

Phase 11 introduces deterministic position sizing from the actual structural stop range. The Trader is not allowed to choose lots, contracts, portfolio percentage, or any other trading volume on the Londres path.

## Core rule

`SMT -> CSD -> IOF -> entry -> selected structural stop -> executable stop -> measure pips/ticks -> calculate safe volume`

A wider stop produces a smaller position. A tighter stop may produce a larger position only while projected cash risk remains inside the same account-risk policy.

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
- allowed risk fraction;
- optional hard cash-risk cap.

The engine calculates:

1. `price_distance = abs(entry - executable_stop)`
2. `ticks = ceil(price_distance / tick_size)`
3. `risk_per_volume_unit = ticks * tick_value_per_volume_unit`
4. `cash_risk_budget = min(account_equity * risk_fraction, optional_cash_cap)`
5. `raw_volume = cash_risk_budget / risk_per_volume_unit`
6. cap at broker maximum;
7. round DOWN to the broker volume step;
8. verify projected cash risk remains at or below the risk budget.

When a pip size exists, the engine also reports the entry-to-stop distance in pips.

## Safety behavior

- Manual/LLM volume is forbidden.
- The Londres Trader validator clears any LLM-generated `position_sizing` field.
- Missing exact entry -> WAIT.
- Missing executable stop -> WAIT.
- Stop on the wrong side of the entry -> invalid geometry.
- Safe calculated size below broker minimum -> no trade; the engine does not round up and exceed the risk budget.
- Price distance is rounded UP to whole ticks for risk calculation.
- Position volume is rounded DOWN to the broker step.
- `order_authorized` remains false; this phase does not place broker orders.

## Structural stop relationship

Phase 10 still gives the Trader discretion between the two approved structural stop anchors:

- `IOF_RANGE`
- `SMT_PROTECTED`

Phase 11 does not change that choice. It converts the final executable stop derived from the selected anchor into a deterministic risk range and safe volume.

The symbol-specific buffer beyond the structural anchor must be resolved before final sizing. Until that executable stop exists, the engine refuses to size from the anchor alone.

## Account-risk policy

Phase 11 intentionally does not hard-code an account-risk percentage. The percentage is an explicit system risk-policy input, not a Trader/user volume decision. This prevents the trading agent from silently changing account exposure while still allowing the project owner to define the global risk mandate.
