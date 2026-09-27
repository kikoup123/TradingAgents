# Londres Phase 24 — NinjaTrader Read-Only Live Accounts

Phase 24 connects the Londres broker-preparation stack to NinjaTrader 8 through a local read-only JSON bridge.

The adapter discovers multiple live brokerage accounts, masks account identifiers, reads current balance/equity/margin/currency, resolves explicit futures roots to verified active quarterly contracts, validates NQ/MNQ, ES/MES, and YM/MYM metadata, and applies Phase 22 quote/tick-value freshness supervision.

Each account has an explicit canonical-symbol-to-futures-root mapping. Standard and Micro contracts are never substituted silently. Active contracts are accepted only when the NinjaTrader rollover metadata verifies them.

After supervision passes, Phase 24 sends the account to Phase 23. Position size is calculated from that account's current broker-reported equity and the selected 3%/5%/10% risk tier.

The adapter exposes no order-placement API. `read_only=true`, `order_submission_enabled=false`, `order_authorized=false`, and `broker_order_placed=false` remain mandatory.
