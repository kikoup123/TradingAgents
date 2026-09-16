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
from .ctrader_execution import (
    CTraderExecutionAdapter,
    CTraderJsonExecutionTransport,
    CTraderTradingOAuth,
)
from .ctrader_valuation import conservative_loss_conversion_rate, resolve_linear_tick_value
from .execution import (
    BrokerExecutionAdapter,
    BrokerExecutionCapabilities,
    BrokerExecutionOutcome,
    BrokerExecutionReceipt,
)
from .mt5 import (
    MT5BridgeError,
    MT5DiscoveredAccount,
    MT5JsonBridgeTransport,
    MT5ReadOnlyBridge,
    MT5UniversalReadOnlyAdapter,
)
from .mt5_execution import (
    MT5ExecutionIPCError,
    MT5FileExecutionTransport,
    VantageMT5ExecutionAdapter,
    deterministic_mt5_magic,
)
from .ninjatrader import (
    NinjaTraderBridgeError,
    NinjaTraderDiscoveredAccount,
    NinjaTraderJsonBridgeTransport,
    NinjaTraderReadOnlyBridge,
    NinjaTraderUniversalReadOnlyAdapter,
)
from .ninjatrader_execution import (
    NinjaTraderExecutionAdapter,
    NinjaTraderExecutionIPCError,
    NinjaTraderFileExecutionTransport,
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
    "BrokerExecutionAdapter",
    "BrokerExecutionCapabilities",
    "BrokerExecutionOutcome",
    "BrokerExecutionReceipt",
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
    "CTraderExecutionAdapter",
    "CTraderJsonExecutionTransport",
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
    "CTraderTradingOAuth",
    "CTraderUniversalReadOnlyAdapter",
    "ExchangeFuturesSpec",
    "FuturesContractResolution",
    "FuturesContractResolutionStatus",
    "MT5BridgeError",
    "MT5DiscoveredAccount",
    "MT5ExecutionIPCError",
    "MT5FileExecutionTransport",
    "MT5JsonBridgeTransport",
    "MT5ReadOnlyBridge",
    "MT5UniversalReadOnlyAdapter",
    "NINJATRADER_EQUITY_INDEX_FUTURES",
    "NinjaTraderBridgeError",
    "NinjaTraderDiscoveredAccount",
    "NinjaTraderExecutionAdapter",
    "NinjaTraderExecutionIPCError",
    "NinjaTraderFileExecutionTransport",
    "NinjaTraderFuturesContractResolver",
    "NinjaTraderJsonBridgeTransport",
    "NinjaTraderReadOnlyBridge",
    "NinjaTraderUniversalReadOnlyAdapter",
    "OrchestrationPolicy",
    "ParsedNinjaTraderContract",
    "SymbolMappingError",
    "TradeIntent",
    "VantageMT5ExecutionAdapter",
    "canonicalize_symbol",
    "conservative_loss_conversion_rate",
    "deterministic_mt5_magic",
    "parse_ninjatrader_contract_symbol",
    "resolve_linear_tick_value",
]
