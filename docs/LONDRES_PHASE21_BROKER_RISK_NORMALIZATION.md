# Londres Phase 21 — Broker-native risk normalization

Phase 21 closes the gap between broker market metadata and the deterministic Phase 11 position-sizing engine.

## Objective

The Londres engine must never invent a tick value, contract multiplier, currency conversion rate, lot size, or broker volume step. A broker adapter must resolve the cash value of one broker tick for one broker volume unit in the connected account's deposit currency before deterministic risk sizing can proceed.

The normalized path is:

```text
broker account + broker instrument metadata
        ↓
verified account deposit currency
        ↓
verified tick size
        ↓
verified tick value in deposit currency
        ↓
broker min/max/step volume constraints
        ↓
InstrumentRiskSpec
        ↓
Phase 11 deterministic 3% / 5% / 10% sizing
```

No broker order is submitted in Phase 21.

## Universal normalization contract

`BrokerRiskNormalizer` accepts a `BrokerAccountSnapshot` and `BrokerInstrumentSpec` and fails closed unless:

- the broker account is connected;
- the account deposit currency is known;
- broker instrument metadata is marked verified;
- tick value is positive and already resolved in account currency;
- an explicit tick-value currency, when supplied, matches the account deposit currency;
- tick size and broker volume-grid metadata can form a valid `InstrumentRiskSpec`.

The output records tick-value provenance, valuation model and conversion timestamp when available.

## cTrader valuation

cTrader Open API exposes symbol price geometry and asset-conversion chains but does not expose the cTrader Automate `Symbol.TickValue` property directly through Open API.

For the read-only cTrader adapter, Phase 21 therefore resolves one-tick cash value for one normalized cTrader volume unit as follows:

```text
one volume unit × one display tick
        = tick_size amount in the symbol quote asset

quote-asset amount
        × cTrader conversion chain
        = account-deposit-currency tick value
```

The production connector requests the broker's `ProtoOASymbolsForConversionReq` chain and subscribes to live spot prices for every conversion leg.

### Conservative loss-side FX conversion

Risk sizing must not understate a possible cash loss because of spread. For that reason the conversion engine uses the adverse side of every conversion leg when converting a loss magnitude:

- base → quote: use ASK;
- quote → base: use `1 / BID`.

The chain must connect exactly from the symbol quote asset to the account deposit asset. Missing prices, missing asset IDs, a discontinuous chain, or a non-positive rate fail closed.

If the symbol quote asset already equals the account deposit asset, the conversion rate is exactly `1.0` and no FX chain is required.

## Security boundary

Phase 21 keeps the existing privacy design:

- Demo/Live stays internal to the adapter;
- full account IDs and OAuth credentials remain outside AgentState;
- public state contains only masked account identity and normalized broker metadata;
- there is no order-placement method in the universal read-only adapter;
- `order_submission_enabled=false`;
- `order_authorized=false`;
- `broker_order_placed=false`.

## Multi-broker behavior

The normalization layer is broker-agnostic. Future NinjaTrader, MT5/Vantage, Interactive Brokers or Tradovate adapters may supply tick value differently:

- futures adapters can provide the exchange/broker-native tick value per contract directly;
- MT5 adapters can use broker-provided tick-value/contract fields and account-currency conversion;
- cTrader uses its Open API symbol geometry plus conversion chain;
- a custom adapter may provide another verified method.

The Londres strategy and Phase 11 risk engine do not change between brokers.

## Files

- `tradingagents/brokers/risk_normalization.py`
- `tradingagents/brokers/ctrader_valuation.py`
- `tradingagents/brokers/ctrader.py`
- `tradingagents/brokers/ctrader_adapter.py`
- `tradingagents/ict/phase21.py`
- `tests/test_phase21_broker_risk_normalization.py`

## Still out of scope

- live authenticated FP Markets validation using user credentials;
- persistent quote-heartbeat and stale-conversion-rate supervision;
- NinjaTrader futures adapter and rollover logic;
- MT5/Vantage adapter;
- post-fill slippage re-risking;
- broker order submission, amendment, partial close, or position close;
- demo or live automated trading.
