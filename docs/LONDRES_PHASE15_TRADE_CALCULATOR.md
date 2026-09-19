# Londres Phase 15 — Complete trade calculator

Phase 15 joins the deterministic components created in the prior phases into one pre-order trade calculation. It still does not authorize or place a broker order.

## Required inputs

Phase 15 requires:

- Phase 13 `ENTRY_TRIGGERED` exact entry;
- Phase 14 executable stop in `READY` state;
- hard-validated Trader selection containing:
  - structural stop source;
  - approved risk tier: 3%, 5%, or 10%;
  - deterministic Phase 12 exit mode;
- broker-resolved instrument risk specification:
  - tick size;
  - cash value per tick per volume unit;
  - volume step;
  - minimum and maximum volume;
  - optional pip size;
- positive account equity.

## Position sizing

The existing Phase 11 risk engine is reused. Position size is derived from the exact entry-to-executable-stop range and the selected risk tier. The Trader cannot choose lots or contracts manually.

The 10% account-risk ceiling remains absolute. Volume is rounded down to the broker step and may never be rounded up to consume more risk than the selected budget.

## R:R

The calculator uses the selected deterministic CSD target and the exact executable stop to calculate reward/risk.

For shorts:

`reward = entry - target`

For longs:

`reward = target - entry`

A target on the wrong side of entry fails closed.

## HOLD mode

When `HOLD_HTF_LIQUIDITY` is selected, Phase 15 validates:

- the -2.5 partial trigger is in the trade direction;
- the HTF liquidity runner target is beyond -2.5;
- management fractions remain exactly 60% partial / 40% runner;
- the final broker-sized position can be split exactly 60/40 on the broker volume grid;
- both resulting child quantities satisfy the broker minimum volume.

If the exact 60/40 split cannot be represented by the broker volume step, the state becomes `HOLD_SPLIT_NOT_BROKER_EXECUTABLE`. The engine does not silently approximate the user's management rule.

## Output

Phase 15 records:

- exact entry;
- executable stop;
- stop distance in price/ticks/pips;
- selected account-risk fraction;
- final broker-grid volume;
- projected cash risk and equity-risk fraction;
- selected exit mode;
- selected CSD target;
- reward distance and R:R;
- automatic partial trigger/fraction/quantity when HOLD is used;
- runner fraction/quantity, HTF target and runner R:R;
- the complete Phase 11 risk-sizing payload.

`order_authorized` remains false. The next phase is the hard pre-broker order validator. Broker connectivity, Demo/Live account handling, account privacy, actual order submission, replay/backtesting and live trading remain separate layers.
