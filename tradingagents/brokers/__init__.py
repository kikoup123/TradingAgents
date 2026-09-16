"""Broker connectivity adapters.

Broker modules keep secrets and account-environment classification outside
LLM-facing state. Strategy logic consumes the universal contracts instead of
binding directly to one broker implementation.
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
    CTraderEnvironment,
    CTraderJsonReadOnlyTransport,
    CTraderOAuthClient,
    CTraderQuoteSnapshot,
    CTraderReadOnlyConnector,
    CTraderReadOnlyError,
    CTraderReadOnlyTransport,
    CTraderSecretConfig,
    CTraderSymbolSnapshot,
    CTraderTokenSet,
)
from .ctrader_adapter import CTraderUniversalReadOnlyAdapter
from .symbols import BrokerSymbolMap, SymbolMappingError

__all__ = [
    "BrokerAccountSnapshot",
    "BrokerAdapter",
    "BrokerCapabilities",
    "BrokerInstrumentSpec",
    "BrokerQuote",
    "BrokerSymbolMap",
    "BrokerType",
    "CanonicalSymbol",
    "CTraderAccountSnapshot",
    "CTraderConnectionError",
    "CTraderEnvironment",
    "CTraderJsonReadOnlyTransport",
    "CTraderOAuthClient",
    "CTraderQuoteSnapshot",
    "CTraderReadOnlyConnector",
    "CTraderReadOnlyError",
    "CTraderReadOnlyTransport",
    "CTraderSecretConfig",
    "CTraderSymbolSnapshot",
    "CTraderTokenSet",
    "CTraderUniversalReadOnlyAdapter",
    "OrchestrationPolicy",
    "SymbolMappingError",
    "TradeIntent",
    "canonicalize_symbol",
]
