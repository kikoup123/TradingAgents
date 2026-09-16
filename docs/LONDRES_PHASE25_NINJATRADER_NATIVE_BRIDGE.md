# Londres Phase 25 — Native NinjaTrader 8 read-only bridge producer

Phase 25 adds the **Windows/NinjaTrader-side producer** for the Phase 24 read-only JSON bridge. It is intentionally one-way: NinjaTrader publishes sanitized account and market state; Londres reads it. There is no order-submission endpoint.

## Safety boundary

The source file is:

`ninjatrader/LondresReadOnlyBridge.cs`

The AddOn reads:

- connected NinjaTrader accounts through `Account.All`;
- account values through `Account.Get(...)`;
- connection/provider display metadata;
- futures Master Instrument tick size / point value;
- NinjaTrader Master Instrument rollover metadata;
- level-one bid/ask market data.

It does **not** call NinjaTrader order APIs. The repository contains a CI safety test that rejects use of order creation/submission/change/cancel/flatten/ATM entry methods in this producer.

The execution boundary remains:

```text
read_only = true
execution_enabled = false
order_submission_enabled = false
order_authorized = false
broker_order_placed = false
```

## Why this is a native AddOn

NinjaTrader Desktop is a Windows/.NET application. The AddOn runs inside NinjaTrader and can use the supported Account, Connection, Instrument, MasterInstrument, RolloverCollection and MarketData APIs. The Python Londres process never receives NinjaTrader credentials and never needs direct access to broker login material.

NinjaTrader documents that `Account.All` exposes the account collection, `Account.Get()` exposes account variables, MasterInstrument exposes `TickSize`, `PointValue` and `RolloverCollection`, and AddOns may subscribe to MarketData. The producer is built around those documented read-only paths.

## Output location

By default the AddOn writes:

```text
<NinjaTrader UserDataDir>\Londres\ninjatrader_snapshot.json
```

The config file is:

```text
<NinjaTrader UserDataDir>\Londres\londres_bridge_config.json
```

The snapshot is written to a temporary file, flushed, and atomically replaced so Phase 24 never intentionally reads a half-written JSON document.

## Required local config

Phase 25 intentionally has **no guessed contract maximum**. Before a futures root can be marked `metadata_verified=true`, an explicit local maximum quantity must be configured.

Example:

```json
{
  "publish_interval_ms": 500,
  "max_quantity_by_root": {
    "NQ": 0,
    "MNQ": 0,
    "ES": 0,
    "MES": 0,
    "YM": 0,
    "MYM": 0
  }
}
```

The example is intentionally fail-closed: zero means the root is not verified for use. The local operator must replace only intended roots with explicit positive technical ceilings.

These Phase 25 values are a **bridge-level technical ceiling**, not the final per-account policy cap. Phase 26 adds a separate account-local `max_contracts_by_root` check for each NinjaTrader account. A Phase 26 account cap may be lower than the Phase 25 bridge ceiling, and Phase 26 blocks rather than silently resizing when the independently risk-sized contract quantity exceeds that account cap.

The Phase 25 values are not inferred from advertised prop account size and are not used as prop-firm daily-loss equity. Phase 23 still owns the personal-vs-prop risk base.

If a root is missing or its configured maximum is not positive, Phase 25 publishes that instrument as unverified and Phase 24 fails closed for that path.

## Account discovery

On every publish cycle the AddOn enumerates NinjaTrader accounts. It publishes:

- a SHA-256-derived private bridge key rather than the raw account name;
- a masked display identity containing only the final account characters;
- account denomination;
- CashValue as balance;
- NetLiquidation as equity;
- InitialMargin and BuyingPower where available;
- connection/provider display metadata;
- per-account connected state.

### Personal vs prop classification

The native bridge deliberately publishes:

```text
provider_classification = null
provider_classification_verified = false
```

by default.

That is intentional. A NinjaTrader connection/provider name is not sufficient evidence that an individual account is personal or prop. Phase 23/24 therefore continues to use user-confirmed/private-registry classification unless a future authoritative provider-specific integration can prove the classification.

This preserves the mixed-account use case:

```text
NinjaTrader
├── personal account A
├── personal account B
└── prop account C
```

without classifying the whole NinjaTrader installation as one account type.

## Futures metadata

The native producer supports only the roots already pinned by Phase 24:

| Root | Canonical | Tick size | Point value | Tick value |
| --- | --- | ---: | ---: | ---: |
| NQ | NASDAQ | 0.25 | $20.00 | $5.00 |
| MNQ | NASDAQ | 0.25 | $2.00 | $0.50 |
| ES | SP500 | 0.25 | $50.00 | $12.50 |
| MES | SP500 | 0.25 | $5.00 | $1.25 |
| YM | DOW | 1.00 | $5.00 | $5.00 |
| MYM | DOW | 1.00 | $0.50 | $0.50 |

For an exact contract to be published as verified, NinjaTrader's Master Instrument values must match the pinned Phase 24 economics and the local quantity cap must be explicit.

## Rollover resolution

The native AddOn does not ask the Python strategy to guess the active quarter. It reads NinjaTrader's configured `MasterInstrument.RolloverCollection`, selects the latest rollover entry whose effective rollover date has been reached inside NinjaTrader, and resolves the exact contract symbol from that configured `ContractMonth`.

Example output:

```json
{
  "NQ": {
    "active_contract": "NQ 12-26",
    "verified": true,
    "source": "NINJATRADER_MASTER_INSTRUMENT_ROLLOVER_COLLECTION",
    "as_of_ms": 1800000000000
  }
}
```

If the Master Instrument or rollover collection cannot produce an exact contract, the bridge publishes `verified=false`; Phase 24 then returns `CONTRACT_ROLLOVER_UNVERIFIED` and blocks that account/contract path.

No standard/Micro substitution occurs.

## Quote handling

The AddOn opens a read-only MarketData subscription for each exact active supported contract. It publishes only a complete positive bid/ask where ask is not below bid.

The quote timestamp is the **older** of the bid timestamp and ask timestamp. This is deliberate: Phase 22 should consider the complete BBO stale when either side is stale rather than accepting a fresh ask with an old bid, or vice versa.

## Installation in NinjaTrader 8

1. Open NinjaTrader Desktop on the Windows machine where the accounts are connected.
2. Open **New > NinjaScript Editor**.
3. Create/open an AddOn source file and use the contents of `ninjatrader/LondresReadOnlyBridge.cs`.
4. Compile in NinjaScript Editor.
5. Restart NinjaTrader after a clean compile if needed so the AddOn lifecycle starts cleanly.
6. Create `<UserDataDir>\Londres\londres_bridge_config.json` with explicit positive `max_quantity_by_root` values only for intended roots.
7. Confirm `<UserDataDir>\Londres\ninjatrader_snapshot.json` is being refreshed.
8. Point the Phase 24 `NinjaTraderJsonBridgeTransport` at that snapshot file.

Do not place broker passwords, API secrets or raw account credentials in the config file.

## Important compile/runtime validation

GitHub CI can validate the Python consumer and statically validate the C# source safety contract, but the repository CI runner does **not** contain NinjaTrader Desktop assemblies. Therefore the final native compile must be performed inside NinjaTrader 8 on Windows.

If NinjaTrader reports a C# compiler error, fix it against the installed NinjaTrader version before using the bridge. Until the local AddOn compiles and the snapshot is being refreshed, Phase 24 must be treated as disconnected/unavailable.

## Current limitations

- native C# compilation is not executed by Linux GitHub CI because NinjaTrader assemblies are proprietary/local;
- the bridge is read-only and does not execute orders;
- prop-firm account classification remains explicit/user-confirmed unless authoritative account metadata exists;
- per-prop-firm consistency/scaling/payout formulas still require verified provider-specific implementations;
- the current static futures risk path requires USD-denominated futures accounts;
- Phase 25 max quantity is a local technical ceiling, while Phase 26 owns the stricter per-account contract policy;
- post-fill slippage and live order-state lifecycle remain out of scope.
