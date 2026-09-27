# Londres private demo/live brokerage routing

## Purpose

Londres may connect to demo and live brokerage accounts without exposing an account's environment in public or LLM-facing state.

The environment is a trusted local routing fact, not a strategy input and not public agent metadata.

Public account payloads use:

```text
account_scope = BROKERAGE_ACCOUNTS
account_environment = HIDDEN_INTERNAL
```

Account numbers remain masked and stable public aliases are hashes of private routing identities.

## cTrader / FP Markets

cTrader already supports private `DEMO` and `LIVE` endpoint selection through `CTraderEnvironment`. The environment and full account id live in secret configuration and are omitted from sanitized public status/account payloads.

## Vantage / MetaTrader 5

`scripts/mt5_readonly_bridge.py` accepts only MT5 demo or real brokerage accounts.

The local bridge reads `ACCOUNT_TRADE_MODE_DEMO`, `ACCOUNT_TRADE_MODE_REAL`, and `ACCOUNT_TRADE_MODE_CONTEST`. Demo and real are accepted. Contest and unknown modes fail closed.

The local JSON snapshot retains `trade_mode` only because the trusted MT5-side route must know which terminal/account it is connected to. `MT5UniversalReadOnlyAdapter` validates that private mode and deliberately omits it from:

- discovered-account payloads;
- public adapter status;
- account snapshots;
- bridge metadata;
- broker supervision output;
- Phase 29 account plans.

The public account alias is derived from the private account key. The private key includes MT5 server plus login, so distinct broker routes do not collapse into a shared public identity.

## NinjaTrader

The native NinjaTrader bridge enumerates `Account.All`; it does not filter accounts by demo/live classification. Therefore demo and live accounts made available by the connected NinjaTrader installation can both be discovered.

The bridge does not publish an account-type classification field. Routing identity is instead bound to:

```text
SHA256(connection_name + account_name)
```

Only the masked account and hashed public alias leave the trusted routing boundary. This keeps accounts on distinct private routes without publishing whether a route is demo or live.

## Replication and risk

Demo and live accounts use the same Londres replication invariant:

```text
one validated TradeIntent
    -> independent account equity
    -> independent broker symbol/contract geometry
    -> independent 3% / 5% / 10% risk sizing
```

Raw lots/contracts are never copied between accounts.

Environment does not change strategy validity. It only determines which private broker route eventually receives an authorized command.

## Execution safety requirement for Phase 32+

Future execution commands must bind to the exact opaque/private account route already represented by the account alias and adapter binding. A command prepared for one route must never be transferable to another account merely because canonical symbol, price, or quantity are identical.

The exactly-once execution ledger must therefore bind at minimum:

- Phase 30 authorization fingerprint;
- Phase 31 pre-submit snapshot fingerprint;
- account alias;
- adapter/broker type;
- exact broker symbol or futures contract;
- exact quantity/volume;
- direction, entry, stop, target, and exit mode.

Demo/live classification remains outside public command/state serialization.

## Current execution boundary

This hardening does not enable order placement.

```text
execution_enabled = false
order_submission_enabled = false
broker_order_placed = false
```

NinjaTrader, cTrader, and MT5 integrations remain read-only until the later execution layer is explicitly implemented and validated.
