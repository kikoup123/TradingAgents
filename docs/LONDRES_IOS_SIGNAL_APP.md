# Londres Trading AI — Native iOS Signal, Journal and AI Product

## Scope

This branch creates a separate native iOS product on top of the validated Londres research codebase. It does not modify or replace the Phase 33/34 broker-execution boundary.

The app's **analysis and signal generation remain broker-independent**. Market data, deterministic strategy logic, paper execution, journaling and AI explanations do not require broker trading authority.

The app may optionally connect to cTrader Open API using OAuth scope `accounts` for **read-only account information**. This connection cannot submit, modify or close broker orders. The implementation is documented in `docs/LONDRES_IOS_CTRADER_READ_ONLY.md`.

V1 responsibilities:

1. ingest broker-independent market-price data;
2. reproduce Londres deterministic analysis in Swift;
3. qualify or reject setups deterministically;
4. calculate/display exact entry, stop, target and reward/risk;
5. paper-execute validated setups inside app-owned simulated accounts;
6. explain deterministic state with AI without allowing AI to override it;
7. journal setups and simulated trades;
8. calculate performance and discipline analytics;
9. support internationalized presentation and AI output;
10. enforce paid feature entitlements with StoreKit;
11. optionally display read-only cTrader account balance/equity/margin data without order authority.

## Internal demo accounts

The app owns two persistent simulated accounts:

- `$1,000 App Demo`
- `$5,000 App Demo`

These are not brokerage accounts. They do not submit orders to, reconcile executions with, or alter any broker account.

When the Londres engine validates a setup, the app creates a paper position using the same deterministic entry, stop, target, risk tier and direction. Incoming market prices are used only to determine whether the simulated entry, stop, target or invalidation condition was reached. Realized R and simulated P&L are then applied to the app-owned balance.

The demo engine must fail closed when candle ordering is ambiguous. If stop and target are both touched in a bar and the sequence cannot be proven, the trade is marked ambiguous rather than credited as a win.

All demo performance must be labeled simulated/hypothetical. It is not a broker statement and must not be presented as guaranteed or expected future performance.

## Source-of-truth rule

The existing Python implementations remain the reference while each strategy module is ported. A Swift module is not considered complete until fixture/parity tests prove that the same market input produces the same normalized output.

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

Broker **order-submission** modules remain outside the iOS product boundary. The optional cTrader integration reuses only the hardened read-only connector and OAuth account-access path.

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

AI may only consume that structured state for explanation, translation, summarization, journal review, and pattern discovery across historical journal records. AI cannot invent, authorize, resize, or alter a setup that fails the deterministic rule chain.

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

### Stage E — Trade geometry and app-owned demo execution

- entry
- executable stop
- target
- R:R
- invalidation
- signal expiry/staleness
- $1,000 and $5,000 internal simulated ledgers
- deterministic paper-position lifecycle
- equity/P&L/drawdown/performance analytics

### Stage F — Market data, AI and optional broker read-only sync

- broker-independent market-data transport
- optional server-side scanner for 24/7 operation
- secure AI gateway; no provider secret inside the iOS bundle
- push notifications
- optional cTrader OAuth `accounts` connection
- masked cTrader account list
- read-only balance/equity/margin snapshots

The strategy market-data provider has no trading authority. The cTrader read-only connection is a separate optional account-information path and is not used to authorize deterministic signals or broker execution.

### Stage G — Commercial product

- App Store products
- Londres Pro monthly subscription
- 1-month introductory free trial configured in App Store Connect
- receipt/entitlement validation
- onboarding
- account/profile sync
- TestFlight

## Journal model

Each validated signal can create a journal record automatically with market context already attached. The app can also attach the corresponding internal demo trade lifecycle and simulated result.

Tracked fields include:

- symbol/direction/session;
- weekly/daily/H4 narrative;
- SMT/CSD/IOF state;
- entry/stop/target/planned R:R;
- realized R and simulated monetary P&L;
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

StoreKit product IDs are defined in code, while actual localized prices and introductory offers are configured in App Store Connect. The app must never treat a hard-coded text price or trial claim as entitlement evidence.

## Security and execution boundary

- cTrader OAuth requests `accounts` view-only scope, never `trading` in this milestone;
- no broker username or password is collected by the iOS app; cTrader authentication occurs on the cTrader authorization page;
- `CTRADER_CLIENT_SECRET` remains server-side and is never embedded in the Swift bundle;
- raw cTrader access/refresh tokens and full account IDs are not exposed to normal iOS UI;
- the iOS app stores only an encrypted opaque broker-session token in Keychain;
- no broker order-routing endpoint exists in the mobile gateway for this milestone;
- market-data credentials remain server-side/read-only;
- no AI provider secret is stored in the mobile bundle;
- journal and demo-ledger writes remain local/atomic until encrypted cloud sync is introduced;
- deterministic signal state is auditable and not controlled by the LLM;
- broker order execution remains disabled in the iOS V1 product.
