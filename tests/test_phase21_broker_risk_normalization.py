from __future__ import annotations

import pytest

from tradingagents.brokers import (
    BrokerAccountSnapshot,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerRiskNormalizationStatus,
    BrokerRiskNormalizer,
    BrokerType,
    CTraderConversionLeg,
    CTraderUniversalReadOnlyAdapter,
    conservative_loss_conversion_rate,
    resolve_linear_tick_value,
)
from tradingagents.ict import LondresPhase21BrokerRiskEngine


def test_ctrader_direct_quote_currency_tick_value_needs_no_fx_chain() -> None:
    snapshot = resolve_linear_tick_value(
        symbol="XAUUSD",
        account_currency="USD",
        tick_size=0.01,
        quote_asset_id=2,
        deposit_asset_id=2,
    )
    assert snapshot.conversion_rate == pytest.approx(1.0)
    assert snapshot.tick_value_account_currency == pytest.approx(0.01)
    assert snapshot.conversion_symbols == ()


def test_ctrader_loss_conversion_uses_adverse_side_without_understating_risk() -> None:
    # Convert USD loss magnitude into EUR through EURUSD. Since USD is the
    # quote asset, conservative conversion uses 1 / bid rather than 1 / ask.
    leg = CTraderConversionLeg(
        symbol_id=1,
        symbol="EURUSD",
        base_asset_id=10,
        quote_asset_id=20,
        bid=1.1000,
        ask=1.1002,
        timestamp_ms=1000,
    )
    rate, timestamp = conservative_loss_conversion_rate(
        first_asset_id=20,
        last_asset_id=10,
        legs=(leg,),
    )
    assert rate == pytest.approx(1.0 / 1.1000)
    assert timestamp == 1000

    snapshot = resolve_linear_tick_value(
        symbol="XAUUSD",
        account_currency="EUR",
        tick_size=0.01,
        quote_asset_id=20,
        deposit_asset_id=10,
        conversion_legs=(leg,),
    )
    assert snapshot.tick_value_account_currency == pytest.approx(0.01 / 1.1000)


def test_ctrader_conversion_chain_must_be_contiguous() -> None:
    bad_leg = CTraderConversionLeg(
        symbol_id=1,
        symbol="GBPUSD",
        base_asset_id=30,
        quote_asset_id=20,
        bid=1.25,
        ask=1.2502,
    )
    with pytest.raises(ValueError, match="not contiguous"):
        conservative_loss_conversion_rate(
            first_asset_id=10,
            last_asset_id=20,
            legs=(bad_leg,),
        )


def _account(currency: str = "USD", connected: bool = True) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_alias="primary",
        broker_type=BrokerType.CTRADER,
        broker_name="FP Markets",
        masked_account="••••1234",
        connected=connected,
        currency=currency,
        balance=10_000.0,
        equity=10_000.0,
        used_margin=0.0,
        free_margin=10_000.0,
    )


def _instrument(*, tick_currency: str | None = "USD") -> BrokerInstrumentSpec:
    return BrokerInstrumentSpec(
        canonical_symbol="NASDAQ",
        broker_symbol="US100",
        tick_size=0.01,
        tick_value_account_currency=0.01,
        tick_value_currency=tick_currency,
        tick_value_source="CTRADER_OPEN_API_CONVERSION_CHAIN",
        tick_value_timestamp_ms=123456,
        valuation_model="CTRADER_LINEAR_VOLUME_UNIT_CONSERVATIVE_LOSS_FX",
        volume_step=1.0,
        min_volume=1.0,
        max_volume=10000.0,
        volume_unit="units",
        pip_size=0.1,
    )


def test_broker_risk_normalizer_rejects_tick_value_currency_mismatch() -> None:
    result = BrokerRiskNormalizer().normalize(
        account=_account("EUR"),
        instrument=_instrument(tick_currency="USD"),
    )
    assert result.status == BrokerRiskNormalizationStatus.INSTRUMENT_ACCOUNT_CURRENCY_MISMATCH
    assert result.instrument_risk_spec is None


def test_broker_risk_normalizer_builds_phase11_spec_when_verified() -> None:
    result = BrokerRiskNormalizer().normalize(
        account=_account("USD"),
        instrument=_instrument(tick_currency="USD"),
    )
    assert result.status == BrokerRiskNormalizationStatus.READY
    assert result.instrument_risk_spec is not None
    assert result.instrument_risk_spec.tick_value_per_volume_unit == pytest.approx(0.01)
    assert result.instrument_risk_spec.volume_unit == "units"


class TickResolvedConnector:
    def __init__(self) -> None:
        self.connected = True

    def public_status(self) -> dict:
        return {
            "provider": "cTrader Open API",
            "broker": "FP Markets",
            "status": "CONNECTED",
            "account": "••••1234",
            "account_environment": "HIDDEN_INTERNAL",
            "read_only": True,
            "order_submission_enabled": False,
        }

    def connect(self) -> dict:
        self.connected = True
        return self.public_status()

    def account_snapshot(self) -> dict:
        return {
            "broker": "FP Markets",
            "masked_account": "••••1234",
            "currency": "USD",
            "balance": 10000.0,
            "equity": 10000.0,
            "used_margin": 0.0,
            "free_margin": 10000.0,
            "status": "CONNECTED",
            "account_environment": "HIDDEN_INTERNAL",
        }

    def symbol_snapshot(self, symbol: str) -> dict:
        return {
            "symbol_id": 42,
            "symbol": symbol,
            "digits": 2,
            "pip_position": 1,
            "min_volume_protocol": 100,
            "max_volume_protocol": 1_000_000,
            "step_volume_protocol": 100,
            "sl_distance": 10,
            "tp_distance": 10,
            "distance_set_in": "SYMBOL_DISTANCE_IN_POINTS",
        }

    def tick_value_snapshot(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "account_currency": "USD",
            "tick_size": 0.01,
            "tick_value_account_currency": 0.01,
            "quote_asset_id": 2,
            "deposit_asset_id": 2,
            "conversion_rate": 1.0,
            "conversion_symbols": (),
            "conversion_timestamp_ms": None,
            "valuation_model": "CTRADER_LINEAR_VOLUME_UNIT_CONSERVATIVE_LOSS_FX",
            "verified": True,
            "tick_value_source": "CTRADER_OPEN_API_CONVERSION_CHAIN",
        }

    def quote_snapshot(self, symbol: str) -> dict:
        return {
            "symbol_id": 42,
            "symbol": symbol,
            "bid": 21900.0,
            "ask": 21900.5,
            "timestamp_ms": 1_700_000_000_000,
        }


def test_ctrader_universal_adapter_feeds_verified_tick_value_to_phase21() -> None:
    adapter = CTraderUniversalReadOnlyAdapter(TickResolvedConnector(), adapter_id="fp-readonly")
    context = LondresPhase21BrokerRiskEngine().analyze(
        adapter=adapter,
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="US100",
    )
    assert context["risk_normalization"]["status"] == "READY"
    assert context["risk_normalization"]["tick_value_account_currency"] == pytest.approx(0.01)
    assert context["order_authorized"] is False
    assert context["broker_order_placed"] is False
    assert context["account"]["account_environment"] == "HIDDEN_INTERNAL"


def test_phase21_adapter_contract_remains_read_only() -> None:
    adapter = CTraderUniversalReadOnlyAdapter(TickResolvedConnector())
    capabilities: BrokerCapabilities = adapter.capabilities()
    quote: BrokerQuote = adapter.quote_snapshot(
        account_alias="primary",
        canonical_symbol="NASDAQ",
        broker_symbol="US100",
    )
    assert capabilities.execution_enabled is False
    assert quote.bid == pytest.approx(21900.0)
    assert not hasattr(adapter, "place_order")
