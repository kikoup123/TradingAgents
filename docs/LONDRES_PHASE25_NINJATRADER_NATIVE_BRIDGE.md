# Londres Phase 25 — Native NinjaTrader 8 Read-Only Bridge

`ninjatrader/LondresReadOnlyBridge.cs` is the Windows/NinjaTrader-side producer for Phase 24.

It publishes sanitized live brokerage account data, Master Instrument futures metadata, configured rollover metadata, and timestamped bid/ask snapshots to a local JSON file. Account names are masked and the private account key is hashed before leaving NinjaTrader.

The bridge does not classify account types and contains no order creation, submit, change, cancel, flatten, ATM, or close API.

For NQ/MNQ, ES/MES, and YM/MYM, metadata is considered verified only when NinjaTrader values match the pinned exchange economics and an explicit positive local `max_quantity_by_root` exists. A missing cap leaves that root unverified rather than inventing a quantity.

The bridge uses NinjaTrader's `MasterInstrument.RolloverCollection`; it does not guess the active contract from the calendar.
