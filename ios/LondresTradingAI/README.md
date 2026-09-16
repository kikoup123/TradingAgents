# Londres Trading AI — iOS

Native iOS companion for the Londres deterministic trading framework.

## Product boundary

This app is intentionally separated from Phase 33/34 broker execution. Version 1 is a signal, technical-analysis, journal, analytics, localization, and subscription product. It does not place orders.

## Core principle

The app must never let a language model invent a trade. The deterministic Londres rule engine owns setup qualification and exact trade geometry. AI may explain, summarize, translate, classify journal notes, and produce reviews from structured engine output.

Canonical setup invariant:

```text
SMT_DETECTED + CSD_CONFIRMED + IOF_ALIGNED + TIME_PRICE_VALID + ENTRY_ZONE_VALID
= VALID_LONDRES_SETUP
```

SMT by itself is never actionable.

## Initial modules

- SwiftUI application shell
- Market and setup models
- Deterministic signal validator
- Exact entry / stop / target / R:R representation
- Professional journal data model
- Performance and discipline metrics foundation
- Localization foundation with protected trading terminology
- StoreKit entitlement abstraction for future subscriptions
- AI explanation protocol that consumes structured deterministic output

## Build

The project uses XcodeGen so the repository does not need to maintain a hand-edited `.xcodeproj`.

```bash
brew install xcodegen
cd ios/LondresTradingAI
xcodegen generate
open LondresTradingAI.xcodeproj
```

Use Xcode 15+ and iOS 17+.

## Security

- No broker passwords or execution secrets belong in the iOS bundle.
- No trading token should be committed to the repository.
- Subscription receipts and account/session tokens must use Keychain or server-side validation when those modules are implemented.
- The signal engine remains deterministic and auditable.

## Roadmap

### V1
Standalone analysis, signals, exact entry/SL/TP, journal, analytics, multilingual UI.

### V2
Live market-data adapters and background server notifications.

### V3
AI market narratives and journal coaching from structured deterministic state.

### V4
StoreKit subscriptions and account entitlement service.
