# Londres Phase 24 — NinjaTrader read-only bridge, futures metadata and rollover

Phase 24 connects the broker-agnostic Londres stack to a **read-only NinjaTrader 8 bridge snapshot**. It does not add broker execution.

## Safety boundary

The Python side contains no NinjaTrader order API. The local Windows/NinjaTrader-side bridge publishes account, instrument, quote and rollover metadata; Londres only reads it.

Always preserved:

- `read_only = true`
- `execution_enabled = false`
- `order_submission_enabled = false`
- `order_authorized = false`
- `broker_order_placed = false`
- Demo/live classification stays internal as `HIDDEN_INTERNAL`
- raw account IDs and credentials never enter AgentState

## Why a local bridge

NinjaTrader 8 is a Windows/.NET desktop platform. Phase 24 therefore defines a local JSON bridge boundary suitable for a NinjaTrader AddOn or other trusted local producer. The producer should atomically replace the snapshot file after it has collected a complete read-only state.

The Python transport is `NinjaTraderJsonBridgeTransport`; the universal adapter is `NinjaTraderUniversalReadOnlyAdapter`.

## Bridge schema v1

Minimal shape:

```json
{
  "schema_version": 1,
  "bridge_status": "CONNECTED",
  "generated_at_ms": 1800000000000,
  "accounts": [],
  "instruments": {},
  "quotes": {},
  "rollovers": {}
}
```

### Accounts

Each account row requires a stable private `account_key`. The key remains inside the adapter and is hashed into a public alias such as `NT-1A2B3C4D`.

```json
{
  "account_key": "private-local-key",
  "masked_account": "••••1234",
  "provider": "Provider display name",
  "provider_classification": "PERSONAL",
  "provider_classification_verified": true,
  "connected": true,
  "currency": "USD",
  "balance": 25000.0,
  "equity": 25000.0,
  "used_margin": 0.0,
  "free_margin": 25000.0
}
```

`provider_classification` is accepted only when `provider_classification_verified` is exactly `true`. Otherwise Phase 23/24 treats provider classification as unknown and requires a user-confirmed/private-registry profile.

The adapter never infers `PROP_FIRM` from NinjaTrader itself, the balance, a nominal account size, account-name patterns or provider-name substrings.

## Mixed personal and prop accounts

One NinjaTrader installation may expose any mix:

```text
NT account A -> PERSONAL
NT account B -> PERSONAL
NT account C -> PROP_FIRM
```

The same Londres `TradeIntent` is replicated, but each account is prepared independently.

Personal account:

```text
risk_base = actual account equity
```

Prop account:

```text
remaining_daily_loss_buffer = daily_loss_limit - daily_loss_used
risk_base = min(remaining_daily_loss_buffer, remaining max/trailing drawdown buffer)
```

The advertised `$50K/$100K/$150K` prop account size is metadata only and is never used for sizing.

## Explicit standard vs Micro root selection

Phase 24 requires an explicit root selection **per account and canonical symbol**. It never silently converts a standard contract to a Micro contract or vice versa.

Examples:

```text
Account A: NASDAQ -> NQ
Account B: NASDAQ -> NQ
Account C: NASDAQ -> MNQ
```

This allows two personal accounts to use NQ while a prop account uses MNQ for the same canonical NASDAQ trade when that is the configured account mapping.

## Verified futures specifications

Supported roots in Phase 24:

| Root | Canonical | Exchange | Tick size | Point value | Tick value |
| --- | --- | --- | ---: | ---: | ---: |
| NQ | NASDAQ | CME | 0.25 | $20.00 | $5.00 |
| MNQ | NASDAQ | CME | 0.25 | $2.00 | $0.50 |
| ES | SP500 | CME | 0.25 | $50.00 | $12.50 |
| MES | SP500 | CME | 0.25 | $5.00 | $1.25 |
| YM | DOW | CBOT | 1.00 | $5.00 | $5.00 |
| MYM | DOW | CBOT | 1.00 | $0.50 | $0.50 |

Reference product specifications:

- CME E-mini Nasdaq-100: https://www.cmegroup.com/trading/equity-index/files/emini-nasdaq-100-futures-options.pdf
- CME E-mini equity index FAQ: https://www.cmegroup.com/trading/equity-index/eminifaq.html
- CME Micro E-mini equity index FAQ: https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html
- CME/CBOT E-mini Dow specification: https://www.cmegroup.com/trading/equity-index/files/EQ-159_DowJonesFinal.pdf

Runtime instrument metadata from NinjaTrader must match the verified registry and must include `metadata_verified=true` plus an explicit integer `max_quantity`. A mismatch fails closed.

## Quarterly contract format and rollover

Supported contract strings use NinjaTrader-style quarterly month/year notation:

```text
NQ 12-26
MNQ 12-26
ES 12-26
MES 12-26
YM 12-26
MYM 12-26
```

Only March, June, September and December contract months are accepted.

The bridge must publish rollover metadata per root:

```json
{
  "NQ": {
    "active_contract": "NQ 12-26",
    "verified": true,
    "source": "NINJATRADER_INSTRUMENT_MANAGER",
    "as_of_ms": 1800000000000
  }
}
```

Londres does **not** calculate the front month from the calendar. If the bridge cannot verify the active contract, Phase 24 returns `CONTRACT_ROLLOVER_UNVERIFIED` and the account is blocked. This prevents an accidental trade in an expired/incorrect contract and prevents silent root switching.

## Instrument payload

Example:

```json
{
  "NQ 12-26": {
    "symbol": "NQ 12-26",
    "root": "NQ",
    "canonical_symbol": "NASDAQ",
    "tick_size": 0.25,
    "point_value": 20.0,
    "tick_value": 5.0,
    "currency": "USD",
    "max_quantity": 20,
    "metadata_verified": true,
    "metadata_source": "NINJATRADER_MASTER_INSTRUMENT"
  }
}
```

Phase 24 currently accepts the static exchange tick-value path only for USD-denominated account currency. A non-USD account fails closed until an explicit futures P/L conversion layer is added.

## Quotes and Phase 22 supervision

Quotes are keyed by exact verified contract:

```json
{
  "NQ 12-26": {
    "bid": 25000.00,
    "ask": 25000.25,
    "timestamp_ms": 1800000000000
  }
}
```

Before Phase 23 risk sizing, every account/contract path passes Phase 22 broker supervision:

1. bridge connected;
2. account readable and connected;
3. exact contract resolved;
4. quote complete and fresh;
5. verified static tick value available;
6. configured supervision thresholds pass.

A stale quote blocks the account before prop/personal sizing occurs.

## Orchestration

`BEST_EFFORT` isolates failures: a blocked prop account does not stop two healthy personal accounts from being preparation-ready.

`ALL_OR_NONE` requires every enabled NinjaTrader account to pass discovery, rollover, supervision and Phase 23 preparation.

Neither policy submits orders in Phase 24.

## Windows/NinjaTrader bridge producer contract

A future NinjaTrader 8 AddOn should:

1. enumerate connected accounts and read account values only;
2. map each private account identifier to a stable local `account_key`;
3. publish only a masked account identifier externally;
4. expose provider classification only when it comes from authoritative provider/account metadata;
5. read Master Instrument properties and active-contract/rollover information;
6. publish timestamped bid/ask snapshots;
7. write a temporary JSON file, flush it, then atomically replace the configured bridge file;
8. implement no order-placement endpoint for the Phase 24 bridge.

The bridge file should be readable only by the local user running Londres/NinjaTrader.

## Still out of scope

- NinjaTrader order submission/amend/cancel/close;
- automated standard-to-Micro substitution;
- calendar-guessed front-contract selection;
- non-USD futures tick-value conversion;
- prop-firm-specific news/consistency/max-contract rule packs beyond the Phase 23 risk-base contract;
- post-fill slippage/risk revalidation;
- live or demo automated trading.
