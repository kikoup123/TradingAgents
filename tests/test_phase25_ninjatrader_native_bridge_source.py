from __future__ import annotations

from pathlib import Path

BRIDGE = Path("ninjatrader/LondresReadOnlyBridge.cs")


def _source() -> str:
    return BRIDGE.read_text(encoding="utf-8")


def test_native_bridge_source_is_present_and_read_only() -> None:
    source = _source()

    assert "class LondresReadOnlyBridge : AddOnBase" in source
    assert "Account.All" in source
    assert "account.Get(" in source
    assert "MasterInstrument.RolloverCollection" in source
    assert "new MarketData(instrument)" in source
    assert "marketData.Update += OnMarketData" in source
    assert "File.Replace(" in source
    assert 'SchemaVersion = 1' in source
    assert 'BridgeStatus = "CONNECTED"' in source

    forbidden = (
        ".Submit(",
        "CreateOrder(",
        "CancelAllOrders(",
        ".Cancel(",
        ".Change(",
        ".Flatten(",
        "StartAtmStrategy(",
        "AtmStrategyCreate(",
        "EnterLong(",
        "EnterShort(",
        "ExitLong(",
        "ExitShort(",
    )
    for token in forbidden:
        assert token not in source


def test_native_bridge_never_infers_prop_classification() -> None:
    source = _source()

    assert "ProviderClassification = null" in source
    assert "ProviderClassificationVerified = false" in source
    assert "PROP_FIRM" not in source.replace(
        "// - Provider PERSONAL/PROP_FIRM classification is never inferred here.", ""
    )


def test_native_bridge_requires_explicit_contract_quantity_cap() -> None:
    source = _source()

    assert 'config.MaxQuantityByRoot.TryGetValue(root, out maxQuantity)' in source
    assert "maxQuantity > 0" in source
    assert "maxQuantityConfigured &&" in source
    assert "MaxQuantity = maxQuantityConfigured ? maxQuantity : 0" in source
    assert "UNVERIFIED_MISSING_OR_CONFLICTING_METADATA" in source


def test_native_bridge_uses_ninjatrader_rollover_metadata_not_calendar_contract_guess() -> None:
    source = _source()

    assert "MasterInstrument.RolloverCollection" in source
    assert "active.ContractMonth" in source
    assert 'Source = "NINJATRADER_MASTER_INSTRUMENT_ROLLOVER_COLLECTION"' in source
    assert "GetNextExpiry(" not in source


def test_native_bridge_pins_supported_futures_economics() -> None:
    source = _source()

    for root in ("NQ", "MNQ", "ES", "MES", "YM", "MYM"):
        assert f'"{root}"' in source

    assert 'new ExpectedFuturesSpec("NASDAQ", 0.25, 20.0, 5.0)' in source
    assert 'new ExpectedFuturesSpec("NASDAQ", 0.25, 2.0, 0.5)' in source
    assert 'new ExpectedFuturesSpec("SP500", 0.25, 50.0, 12.5)' in source
    assert 'new ExpectedFuturesSpec("SP500", 0.25, 5.0, 1.25)' in source
    assert 'new ExpectedFuturesSpec("DOW", 1.0, 5.0, 5.0)' in source
    assert 'new ExpectedFuturesSpec("DOW", 1.0, 0.5, 0.5)' in source


def test_native_bridge_masks_accounts_and_hashes_private_key() -> None:
    source = _source()

    assert "SHA256.Create()" in source
    assert 'return "••••" + suffix;' in source
    assert "AccountKey = StableAccountKey(connectionName, account.Name)" in source
    assert "MaskedAccount = MaskAccount(account.Name)" in source


def test_native_bridge_uses_older_bid_ask_timestamp_for_freshness() -> None:
    source = _source()

    assert "Math.Min(bidMs, askMs)" in source
    assert "ask.Price < bid.Price" in source
