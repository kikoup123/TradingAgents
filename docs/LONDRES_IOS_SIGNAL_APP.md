# Londres Trading AI — Native iOS Signal, Journal and AI Product

## Scope

This branch creates a separate native iOS product on top of the validated Londres research codebase. It does not modify or replace the Phase 33/34 broker-execution boundary.

V1 responsibilities:

1. ingest market data;
2. reproduce Londres deterministic analysis in Swift;
3. qualify or reject setups deterministically;
4. calculate/display exact entry, stop, target and reward/risk;
5. explain the deterministic state with AI without allowing AI to override it;
6. journal setups and trades;
7. calculate performance and discipline analytics;
8. support internationalized presentation and AI output;
9. enforce paid feature entitlements with StoreKit.

## Source-of-truth rule

The existing Python implementations remain the reference while each module is ported. A Swift module is not considered complete until fixture/parity tests prove that the same market input produces the same normalized output.

Initial Python source modules include:

- `market_data.py`
- `liquidity.py`
- `daily_profile.py`
- `h4_profile.py`
- `csd.py`
- `delivery.py`
- `mmxm.py`
- `fair_value.py`
- `entry_execution.py`
- `executable_stop.py`
- later phase authorization/risk modules where relevant to signal presentation

Broker submission modules are explicitly outside the V1 iOS boundary.

## Deterministic signal contract

The mobile app must preserve this invariant:

```text
SMT_DETECTED
+ CSD_CONFIRMED
+ IOF_ALIGNED
+ TIME_PRICE_VALID
+ ENTRY_ZONE_VALID
= VALID_LONDRES_SETUP
```

SMT alone is never a signal.

The deterministic engine owns:

- setup state;
- direction;
- entry;
- stop;
- target;
- reward/risk;
- invalidation;
- risk-tier label;
- evidence chain.

AI may only consume that structured state for explanation, translation, summarization, journal review, and pattern discovery across historical journal records.

## Port order

### Stage A — Mobile foundation

- SwiftUI app shell
- Codable canonical models
- deterministic final signal validator
- local journal persistence
- performance analytics
- localization preferences
- StoreKit entitlement store
- AI service boundary

### Stage B — Market primitives

- canonical OHLCV candle model
- fixed UTC-4 session clock used by the strategy engine
- timeframe aggregation
- weekly/daily/H4/H1/M15/M5/M3/M1 views
- symbol normalization

### Stage C — Londres context

- liquidity engine
- weekly profile
- daily profile
- H4 profile
- HTF order-flow control
- session/time-price state

### Stage D — Confirmation sequence

- SMT
- CSD
- post-CSD IOF / IOFC
- MMXM/narrative alignment
- deterministic first-return entry zone

### Stage E — Trade geometry

- entry
- executable stop
- target
- R:R
- invalidation
- signal expiry/staleness

### Stage F — Data and AI

- cTrader demo market-data transport
- optional server-side scanner for 24/7 operation
- secure AI gateway; no provider secret inside the iOS bundle
- push notifications

### Stage G — Commercial product

- App Store products
- Londres Pro monthly subscription
- receipt/entitlement validation
- onboarding
- account/profile sync
- TestFlight

## Journal model

Each validated signal can create a journal record automatically with market context already attached. The trader only needs to add execution and human context where applicable.

Tracked fields include:

- symbol/direction/session;
- weekly/daily/H4 narrative;
- SMT/CSD/IOF state;
- entry/stop/target/planned R:R;
- realized R and monetary P&L;
- MFE/MAE;
- playbook model;
- psychology tags;
- discipline mistakes;
- plan compliance;
- before/after screenshot references;
- notes.

Analytics are intentionally separated into strategy performance and trader execution quality.

## Localization

Internal canonical identifiers never change with language. Presentation uses BCP-47 locale identifiers. Protected ICT/Londres terms can remain in English even when surrounding prose is translated. AI language, interface language, journal language and notification language are separate preferences.

## Subscription

StoreKit product IDs are defined in code, while actual localized prices are configured in App Store Connect. The app must never treat a hard-coded text price as entitlement evidence.

## Security

- no broker password in the mobile bundle;
- no AI provider secret in the mobile bundle;
- no execution token in the mobile bundle for V1;
- journal writes are local/atomic until encrypted cloud sync is introduced;
- deterministic signal state is auditable and not controlled by the LLM.
