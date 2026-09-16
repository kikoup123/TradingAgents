"""Broker connectivity adapters.

Broker modules keep secrets and account-environment details outside LLM-facing
state. Londres is configured for live brokerage accounts only; risk sizing uses
each account's current broker-reported equity.
"""

from .contracts import (
    BrokerAccountSnapshot,
    BrokerAdapter,
    BrokerCapabilities,
    BrokerInstrumentSpec,
    BrokerQuote,
    BrokerType,
    CanonicalSymbol,
    OrchestrationPolicy,
    TradeIntent,
    canonicalize_symbol,
)
from .ctrader import (
    CTraderAccountSnapshot,
    CTraderConnectionError,
    CTraderConversionLeg,
    CTraderEnvironment,
    CTraderJsonReadOnlyTransport,
    CTraderOAuthClient,
    CTraderQuoteSnapshot,
    CTraderReadOnlyConnector,
    CTraderReadOnlyError,
    CTraderReadOnlyTransport,
    CTraderSecretConfig,
    CTraderSymbolSnapshot,
    CTraderTickValueSnapshot,
    CTraderTokenSet,
)
from .ctrader_adapter import CTraderUniversalReadOnlyAdapter
from .ctrader_valuation import conservative_loss_conversion_rate, resolve_linear_tick_value
from .mt5 import (
    MT5BridgeError,
    MT5DiscoveredAccount,
    MT5JsonBridgeTransport,
    MT5ReadOnlyBridge,
    MT5UniversalReadOnlyAdapter,
)
from .ninjatrader import (
    NinjaTraderBridgeError,
    NinjaTraderDiscoveredAccount,
    NinjaTraderJsonBridgeTransport,
    NinjaTraderReadOnlyBridge,
    NinjaTraderUniversalReadOnlyAdapter,
)
from .ninjatrader_futures import (
    NINJATRADER_EQUITY_INDEX_FUTURES,
    ExchangeFuturesSpec,
    FuturesContractResolution,
    FuturesContractResolutionStatus,
    NinjaTraderFuturesContractResolver,
    ParsedNinjaTraderContract,
    parse_ninjatrader_contract_symbol,
)
from .risk_normalization import (
    BrokerRiskNormalizationResult,
    BrokerRiskNormalizationStatus,
    BrokerRiskNormalizer,
)
from .supervision import (
    BrokerConnectionSupervisor,
    BrokerSupervisionPolicy,
    BrokerSupervisionResult,
    BrokerSupervisionStatus,
)
from .symbols import BrokerSymbolMap, SymbolMappingError

__all__ = [
    "BrokerAccountSnapshot",
    "BrokerAdapter",
    "BrokerCapabilities",
    "BrokerConnectionSupervisor",
    "BrokerInstrumentSpec",
    "BrokerQuote",
    "BrokerRiskNormalizationResult",
    "BrokerRiskNormalizationStatus",
    "BrokerRiskNormalizer",
    "BrokerSupervisionPolicy",
    "BrokerSupervisionResult",
    "BrokerSupervisionStatus",
    "BrokerSymbolMap",
    "BrokerType",
    "CanonicalSymbol",
    "CTraderAccountSnapshot",
    "CTraderConnectionError",
    "CTraderConversionLeg",
    "CTraderEnvironment",
    "CTraderJsonReadOnlyTransport",
    "CTraderOAuthClient",
    "CTraderQuoteSnapshot",
    "CTraderReadOnlyConnector",
    "CTraderReadOnlyError",
    "CTraderReadOnlyTransport",
    "CTraderSecretConfig",
    "CTraderSymbolSnapshot",
    "CTraderTickValueSnapshot",
    "CTraderTokenSet",
    "CTraderUniversalReadOnlyAdapter",
    "ExchangeFuturesSpec",
    "FuturesContractResolution",
    "FuturesContractResolutionStatus",
    "MT5BridgeError",
    "MT5DiscoveredAccount",
    "MT5JsonBridgeTransport",
    "MT5ReadOnlyBridge",
    "MT5UniversalReadOnlyAdapter",
    "NINJATRADER_EQUITY_INDEX_FUTURES",
    "NinjaTraderBridgeError",
    "NinjaTraderDiscoveredAccount",
    "NinjaTraderFuturesContractResolver",
    "NinjaTraderJsonBridgeTransport",
    "NinjaTraderReadOnlyBridge",
    "NinjaTraderUniversalReadOnlyAdapter",
    "OrchestrationPolicy",
    "ParsedNinjaTraderContract",
    "SymbolMappingError",
    "TradeIntent",
    "canonicalize_symbol",
    "conservative_loss_conversion_rate",
    "parse_ninjatrader_contract_symbol",
    "resolve_linear_tick_value",
]
