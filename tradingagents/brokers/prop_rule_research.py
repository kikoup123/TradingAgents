"""Fresh official-source prop-firm rule discovery and structured research.

Phase 27 separates three concerns that must not be conflated:

1. identifying a prop-firm provider from account/provider metadata;
2. retrieving current rule evidence from official provider domains; and
3. converting that evidence into a deterministic rule snapshot that can tighten
   an account policy without silently relaxing user-configured protections.

Unknown providers and ambiguous rules fail closed. The web-search API key never
appears in public output or AgentState.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlparse

import requests


class PropFirmIdentityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class PropFirmRuleResearchStatus(str, Enum):
    READY = "READY"
    PROVIDER_IDENTITY_UNKNOWN = "PROVIDER_IDENTITY_UNKNOWN"
    PROVIDER_IDENTITY_AMBIGUOUS = "PROVIDER_IDENTITY_AMBIGUOUS"
    SEARCH_PROVIDER_UNAVAILABLE = "SEARCH_PROVIDER_UNAVAILABLE"
    SEARCH_FAILED = "SEARCH_FAILED"
    NO_OFFICIAL_SOURCES = "NO_OFFICIAL_SOURCES"
    RULE_EXTRACTION_FAILED = "RULE_EXTRACTION_FAILED"
    RULES_AMBIGUOUS = "RULES_AMBIGUOUS"
    UNTRUSTED_SOURCE = "UNTRUSTED_SOURCE"


def _normalize_label(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


@dataclass(frozen=True)
class PropFirmProviderRecord:
    provider_id: str
    display_name: str
    aliases: tuple[str, ...]
    official_domains: tuple[str, ...]

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip().upper()
        if not provider_id:
            raise ValueError("provider_id is required")
        display_name = self.display_name.strip()
        if not display_name:
            raise ValueError("display_name is required")
        aliases = tuple(dict.fromkeys(_normalize_label(item) for item in self.aliases if item.strip()))
        if not aliases:
            raise ValueError("at least one provider alias is required")
        domains = tuple(
            dict.fromkeys(item.lower().strip().lstrip(".") for item in self.official_domains if item.strip())
        )
        if not domains:
            raise ValueError("at least one official domain is required")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "official_domains", domains)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_PROP_FIRM_PROVIDER_RECORDS: tuple[PropFirmProviderRecord, ...] = (
    PropFirmProviderRecord(
        provider_id="TOPSTEP",
        display_name="Topstep",
        aliases=("Topstep", "TopstepTrader", "Topstep Funded"),
        official_domains=("topstep.com", "help.topstep.com"),
    ),
    PropFirmProviderRecord(
        provider_id="APEX_TRADER_FUNDING",
        display_name="Apex Trader Funding",
        aliases=("Apex Trader Funding", "ApexTraderFunding"),
        official_domains=("apextraderfunding.com",),
    ),
    PropFirmProviderRecord(
        provider_id="MY_FUNDED_FUTURES",
        display_name="My Funded Futures",
        aliases=("My Funded Futures", "MyFundedFutures", "MFFU"),
        official_domains=("myfundedfutures.com", "help.myfundedfutures.com"),
    ),
    PropFirmProviderRecord(
        provider_id="TAKE_PROFIT_TRADER",
        display_name="Take Profit Trader",
        aliases=("Take Profit Trader", "TakeProfitTrader"),
        official_domains=("takeprofittrader.com",),
    ),
)


class PropFirmProviderRegistry:
    """Exact/segment alias resolver for provider identity.

    It intentionally avoids fuzzy substring matching. A NinjaTrader connection
    labelled only "Rithmic" or "Tradovate" must not be reclassified as a prop
    firm because many unrelated accounts may use the same connection vendor.
    """

    def __init__(
        self,
        records: tuple[PropFirmProviderRecord, ...] = DEFAULT_PROP_FIRM_PROVIDER_RECORDS,
    ) -> None:
        self._records = records
        aliases: dict[str, list[PropFirmProviderRecord]] = {}
        for record in records:
            for alias in record.aliases:
                aliases.setdefault(alias, []).append(record)
        self._aliases = aliases

    def resolve(self, provider_label: str) -> tuple[PropFirmIdentityStatus, PropFirmProviderRecord | None]:
        raw = provider_label.strip()
        if not raw:
            return PropFirmIdentityStatus.UNKNOWN, None

        candidates: dict[str, PropFirmProviderRecord] = {}
        labels = {_normalize_label(raw)}
        for part in re.split(r"[/|()]", raw):
            normalized = _normalize_label(part)
            if normalized:
                labels.add(normalized)

        for label in labels:
            for record in self._aliases.get(label, ()):  # exact alias only
                candidates[record.provider_id] = record

        if len(candidates) == 1:
            return PropFirmIdentityStatus.VERIFIED, next(iter(candidates.values()))
        if len(candidates) > 1:
            return PropFirmIdentityStatus.AMBIGUOUS, None
        return PropFirmIdentityStatus.UNKNOWN, None


@dataclass(frozen=True)
class PropFirmSearchHit:
    title: str
    url: str
    content: str
    score: float | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class PropFirmRuleSearchClient(Protocol):
    def search(
        self,
        *,
        query: str,
        include_domains: tuple[str, ...] = (),
        max_results: int = 8,
    ) -> tuple[PropFirmSearchHit, ...]: ...


class TavilyPropFirmRuleSearchClient:
    """Minimal Tavily Search API client using the repository's requests dependency."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str = "https://api.tavily.com/search",
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_key = (api_key or os.getenv("TAVILY_API_KEY") or "").strip()
        self._endpoint = endpoint.strip()
        self._timeout_seconds = float(timeout_seconds)
        if self._timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def search(
        self,
        *,
        query: str,
        include_domains: tuple[str, ...] = (),
        max_results: int = 8,
    ) -> tuple[PropFirmSearchHit, ...]:
        if not self._api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")
        if not query.strip():
            raise ValueError("query is required")
        if max_results <= 0 or max_results > 20:
            raise ValueError("max_results must be between 1 and 20")

        payload: dict[str, Any] = {
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_raw_content": True,
        }
        if include_domains:
            payload["include_domains"] = list(include_domains)

        response = requests.post(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results")
        if not isinstance(results, list):
            raise RuntimeError("Tavily response did not contain a results list")

        hits: list[PropFirmSearchHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            raw = item.get("raw_content")
            content = raw if isinstance(raw, str) and raw.strip() else str(item.get("content") or "")
            score_raw = item.get("score")
            score = float(score_raw) if isinstance(score_raw, (int, float)) else None
            hits.append(
                PropFirmSearchHit(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    content=content,
                    score=score,
                )
            )
        return tuple(hits)


@dataclass(frozen=True)
class PropFirmRuleResearchRequest:
    provider_label: str
    program_hint: str | None = None
    account_size_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_label.strip():
            raise ValueError("provider_label is required")


@dataclass(frozen=True)
class PropFirmRuleSnapshot:
    provider_id: str
    provider_name: str
    official_domains: tuple[str, ...]
    retrieved_at_ms: int
    source_urls: tuple[str, ...]
    program_name: str | None = None
    account_size: str | None = None
    allowed_roots: tuple[str, ...] | None = None
    max_contracts_by_root: dict[str, int] | None = None
    allowed_sessions: tuple[str, ...] | None = None
    news_trading_allowed: bool | None = None
    overnight_holding_allowed: bool | None = None
    weekend_holding_allowed: bool | None = None
    consistency_rule_required: bool = False
    consistency_limit_percent: float | None = None
    scaling_rule_required: bool = False
    daily_loss_rule_present: bool | None = None
    drawdown_rule_present: bool | None = None
    ambiguous: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.retrieved_at_ms < 0:
            raise ValueError("retrieved_at_ms must be >= 0")
        if not self.provider_id.strip() or not self.provider_name.strip():
            raise ValueError("provider identity is required")
        domains = tuple(dict.fromkeys(item.lower().strip().lstrip(".") for item in self.official_domains))
        if not domains:
            raise ValueError("official_domains are required")
        object.__setattr__(self, "official_domains", domains)

        sources = tuple(dict.fromkeys(url.strip() for url in self.source_urls if url.strip()))
        if not sources:
            raise ValueError("at least one source URL is required")
        object.__setattr__(self, "source_urls", sources)

        if self.max_contracts_by_root is not None:
            caps: dict[str, int] = {}
            for root, value in self.max_contracts_by_root.items():
                normalized = str(root).strip().upper()
                if not normalized or isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("max-contract rules must be explicit positive integers")
                caps[normalized] = value
            object.__setattr__(self, "max_contracts_by_root", caps)

        if self.allowed_roots is not None:
            object.__setattr__(
                self,
                "allowed_roots",
                tuple(dict.fromkeys(str(root).strip().upper() for root in self.allowed_roots if str(root).strip())),
            )
        if self.allowed_sessions is not None:
            object.__setattr__(
                self,
                "allowed_sessions",
                tuple(
                    dict.fromkeys(
                        str(session).strip().upper()
                        for session in self.allowed_sessions
                        if str(session).strip()
                    )
                ),
            )
        if self.consistency_limit_percent is not None and not (
            0 < self.consistency_limit_percent <= 100
        ):
            raise ValueError("consistency_limit_percent must be > 0 and <= 100")

    @property
    def source_digest(self) -> str:
        material = json.dumps(
            {
                "provider_id": self.provider_id,
                "retrieved_at_ms": self.retrieved_at_ms,
                "source_urls": self.source_urls,
                "rules": self.public_dict(include_digest=False),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def public_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if include_digest:
            payload["source_digest"] = self.source_digest
        payload["research_source_policy"] = "OFFICIAL_PROVIDER_DOMAINS_ONLY"
        return payload


class PropFirmRuleExtractor(Protocol):
    def extract(
        self,
        *,
        provider: PropFirmProviderRecord,
        request: PropFirmRuleResearchRequest,
        hits: tuple[PropFirmSearchHit, ...],
        now_ms: int,
    ) -> PropFirmRuleSnapshot: ...


class JsonLLMPropFirmRuleExtractor:
    """Provider-agnostic structured extractor for a configured LangChain chat model.

    The model is not allowed to authorize trading. Its output is accepted only
    after deterministic source-domain validation in PropFirmRuleResearchEngine.
    Unknown values must be null; material disagreement must set ambiguous=true.
    """

    def __init__(self, llm: Any) -> None:
        if llm is None or not hasattr(llm, "invoke"):
            raise ValueError("llm must expose invoke()")
        self._llm = llm

    def extract(
        self,
        *,
        provider: PropFirmProviderRecord,
        request: PropFirmRuleResearchRequest,
        hits: tuple[PropFirmSearchHit, ...],
        now_ms: int,
    ) -> PropFirmRuleSnapshot:
        evidence = [
            {"title": hit.title, "url": hit.url, "content": hit.content[:12000]}
            for hit in hits
        ]
        prompt = (
            "Extract current prop-firm trading rules from ONLY the supplied official-source evidence. "
            "Return one JSON object and no prose. Never infer a value that is not explicit. "
            "If multiple account programs/plans conflict and the supplied program/account-size hints do not "
            "disambiguate them, set ambiguous=true. Contract caps must be integer counts by futures root. "
            "Do not use advertised nominal account size as risk equity. Schema keys: "
            "program_name, account_size, allowed_roots, max_contracts_by_root, allowed_sessions, "
            "news_trading_allowed, overnight_holding_allowed, weekend_holding_allowed, "
            "consistency_rule_required, consistency_limit_percent, scaling_rule_required, "
            "daily_loss_rule_present, drawdown_rule_present, ambiguous, notes, source_urls.\n\n"
            f"Provider: {provider.display_name}\n"
            f"Program hint: {request.program_hint!r}\n"
            f"Account-size hint: {request.account_size_hint!r}\n"
            f"Evidence JSON: {json.dumps(evidence, ensure_ascii=False)}"
        )
        response = self._llm.invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") if isinstance(item, dict) else item) for item in content
            )
        text = str(content).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("rule extractor must return a JSON object")

        return PropFirmRuleSnapshot(
            provider_id=provider.provider_id,
            provider_name=provider.display_name,
            official_domains=provider.official_domains,
            retrieved_at_ms=now_ms,
            source_urls=tuple(str(url) for url in data.get("source_urls") or ()),
            program_name=(str(data["program_name"]) if data.get("program_name") else None),
            account_size=(str(data["account_size"]) if data.get("account_size") else None),
            allowed_roots=(
                tuple(str(item) for item in data["allowed_roots"])
                if isinstance(data.get("allowed_roots"), list)
                else None
            ),
            max_contracts_by_root=(
                {str(key): int(value) for key, value in data["max_contracts_by_root"].items()}
                if isinstance(data.get("max_contracts_by_root"), dict)
                else None
            ),
            allowed_sessions=(
                tuple(str(item) for item in data["allowed_sessions"])
                if isinstance(data.get("allowed_sessions"), list)
                else None
            ),
            news_trading_allowed=data.get("news_trading_allowed"),
            overnight_holding_allowed=data.get("overnight_holding_allowed"),
            weekend_holding_allowed=data.get("weekend_holding_allowed"),
            consistency_rule_required=bool(data.get("consistency_rule_required", False)),
            consistency_limit_percent=(
                float(data["consistency_limit_percent"])
                if data.get("consistency_limit_percent") is not None
                else None
            ),
            scaling_rule_required=bool(data.get("scaling_rule_required", False)),
            daily_loss_rule_present=data.get("daily_loss_rule_present"),
            drawdown_rule_present=data.get("drawdown_rule_present"),
            ambiguous=bool(data.get("ambiguous", False)),
            notes=tuple(str(item) for item in data.get("notes") or ()),
        )


@dataclass(frozen=True)
class PropFirmRuleResearchResult:
    status: PropFirmRuleResearchStatus
    identity_status: PropFirmIdentityStatus
    provider_label: str
    provider_id: str | None
    provider_name: str | None
    official_domains: tuple[str, ...]
    candidate_sources: tuple[PropFirmSearchHit, ...]
    snapshot: PropFirmRuleSnapshot | None
    reason_codes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status is PropFirmRuleResearchStatus.READY and self.snapshot is not None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "identity_status": self.identity_status.value,
            "provider_label": self.provider_label,
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "official_domains": list(self.official_domains),
            "candidate_sources": [item.public_dict() for item in self.candidate_sources],
            "snapshot": self.snapshot.public_dict() if self.snapshot is not None else None,
            "reason_codes": list(self.reason_codes),
            "search_credentials_exposed": False,
            "account_environment": "HIDDEN_INTERNAL",
        }


class PropFirmRuleResearchEngine:
    """Resolve provider identity, search official domains, and validate a rule snapshot."""

    def __init__(
        self,
        *,
        registry: PropFirmProviderRegistry,
        search_client: PropFirmRuleSearchClient,
        extractor: PropFirmRuleExtractor,
    ) -> None:
        self._registry = registry
        self._search = search_client
        self._extractor = extractor

    def research(
        self,
        *,
        request: PropFirmRuleResearchRequest,
        now_ms: int,
    ) -> PropFirmRuleResearchResult:
        identity_status, provider = self._registry.resolve(request.provider_label)
        if identity_status is PropFirmIdentityStatus.AMBIGUOUS:
            return self._blocked(
                status=PropFirmRuleResearchStatus.PROVIDER_IDENTITY_AMBIGUOUS,
                identity_status=identity_status,
                request=request,
                reason="PROVIDER_METADATA_MATCHES_MULTIPLE_PROP_FIRM_IDENTITIES",
            )
        if provider is None:
            try:
                candidates = self._search.search(
                    query=(
                        f'"{request.provider_label}" official futures prop firm rules '
                        "daily loss drawdown max contracts news overnight"
                    ),
                    max_results=5,
                )
            except Exception:
                candidates = ()
            return self._blocked(
                status=PropFirmRuleResearchStatus.PROVIDER_IDENTITY_UNKNOWN,
                identity_status=PropFirmIdentityStatus.UNKNOWN,
                request=request,
                candidates=candidates,
                reason=(
                    "UNKNOWN_PROVIDER_REQUIRES_ONE_TIME_TRUSTED_DOMAIN_VERIFICATION_BEFORE_RULES_CAN_ADAPT"
                ),
            )

        query_parts = [
            provider.display_name,
            "official trading rules",
            "daily loss drawdown maximum contracts news trading overnight weekend consistency scaling",
        ]
        if request.program_hint:
            query_parts.append(request.program_hint)
        if request.account_size_hint:
            query_parts.append(request.account_size_hint)

        try:
            hits = self._search.search(
                query=" ".join(query_parts),
                include_domains=provider.official_domains,
                max_results=10,
            )
        except RuntimeError as exc:
            if "API_KEY" in str(exc).upper() or "CONFIGURED" in str(exc).upper():
                return self._blocked(
                    status=PropFirmRuleResearchStatus.SEARCH_PROVIDER_UNAVAILABLE,
                    identity_status=identity_status,
                    request=request,
                    provider=provider,
                    reason="PROP_RULE_WEB_SEARCH_PROVIDER_NOT_CONFIGURED",
                )
            return self._blocked(
                status=PropFirmRuleResearchStatus.SEARCH_FAILED,
                identity_status=identity_status,
                request=request,
                provider=provider,
                reason="PROP_RULE_WEB_SEARCH_FAILED",
            )
        except Exception:
            return self._blocked(
                status=PropFirmRuleResearchStatus.SEARCH_FAILED,
                identity_status=identity_status,
                request=request,
                provider=provider,
                reason="PROP_RULE_WEB_SEARCH_FAILED",
            )

        official_hits = tuple(
            hit for hit in hits if _host_allowed(_host(hit.url), provider.official_domains)
        )
        if not official_hits:
            return self._blocked(
                status=PropFirmRuleResearchStatus.NO_OFFICIAL_SOURCES,
                identity_status=identity_status,
                request=request,
                provider=provider,
                candidates=hits,
                reason="NO_CURRENT_OFFICIAL_PROVIDER_RULE_SOURCES_FOUND",
            )

        try:
            snapshot = self._extractor.extract(
                provider=provider,
                request=request,
                hits=official_hits,
                now_ms=now_ms,
            )
        except Exception:
            return self._blocked(
                status=PropFirmRuleResearchStatus.RULE_EXTRACTION_FAILED,
                identity_status=identity_status,
                request=request,
                provider=provider,
                candidates=official_hits,
                reason="OFFICIAL_RULE_EVIDENCE_COULD_NOT_BE_PARSED_INTO_VALIDATED_RULES",
            )

        if snapshot.provider_id != provider.provider_id:
            return self._blocked(
                status=PropFirmRuleResearchStatus.UNTRUSTED_SOURCE,
                identity_status=identity_status,
                request=request,
                provider=provider,
                candidates=official_hits,
                reason="EXTRACTED_PROVIDER_ID_DOES_NOT_MATCH_VERIFIED_PROVIDER_IDENTITY",
            )
        if any(
            not _host_allowed(_host(url), provider.official_domains) for url in snapshot.source_urls
        ):
            return self._blocked(
                status=PropFirmRuleResearchStatus.UNTRUSTED_SOURCE,
                identity_status=identity_status,
                request=request,
                provider=provider,
                candidates=official_hits,
                reason="RULE_SNAPSHOT_REFERENCES_NON_OFFICIAL_SOURCE_DOMAIN",
            )
        searched_urls = {hit.url for hit in official_hits}
        if any(url not in searched_urls for url in snapshot.source_urls):
            return self._blocked(
                status=PropFirmRuleResearchStatus.UNTRUSTED_SOURCE,
                identity_status=identity_status,
                request=request,
                provider=provider,
                candidates=official_hits,
                reason="RULE_SNAPSHOT_SOURCE_WAS_NOT_PRESENT_IN_CURRENT_SEARCH_EVIDENCE",
            )
        if snapshot.ambiguous:
            return self._blocked(
                status=PropFirmRuleResearchStatus.RULES_AMBIGUOUS,
                identity_status=identity_status,
                request=request,
                provider=provider,
                candidates=official_hits,
                snapshot=snapshot,
                reason="OFFICIAL_RULES_DIFFER_BY_PROGRAM_OR_ACCOUNT_AND_HINTS_ARE_INSUFFICIENT",
            )

        return PropFirmRuleResearchResult(
            status=PropFirmRuleResearchStatus.READY,
            identity_status=identity_status,
            provider_label=request.provider_label,
            provider_id=provider.provider_id,
            provider_name=provider.display_name,
            official_domains=provider.official_domains,
            candidate_sources=official_hits,
            snapshot=snapshot,
            reason_codes=(
                "PROP_FIRM_IDENTITY_MATCHED_VERIFIED_PROVIDER_REGISTRY",
                "RULE_SEARCH_RESTRICTED_TO_OFFICIAL_PROVIDER_DOMAINS",
                "RULE_SNAPSHOT_VALIDATED_AGAINST_CURRENT_SEARCH_EVIDENCE",
                "RULE_RESEARCH_DOES_NOT_AUTHORIZE_BROKER_EXECUTION",
            ),
        )

    @staticmethod
    def _blocked(
        *,
        status: PropFirmRuleResearchStatus,
        identity_status: PropFirmIdentityStatus,
        request: PropFirmRuleResearchRequest,
        reason: str,
        provider: PropFirmProviderRecord | None = None,
        candidates: tuple[PropFirmSearchHit, ...] = (),
        snapshot: PropFirmRuleSnapshot | None = None,
    ) -> PropFirmRuleResearchResult:
        return PropFirmRuleResearchResult(
            status=status,
            identity_status=identity_status,
            provider_label=request.provider_label,
            provider_id=provider.provider_id if provider is not None else None,
            provider_name=provider.display_name if provider is not None else None,
            official_domains=provider.official_domains if provider is not None else (),
            candidate_sources=candidates,
            snapshot=snapshot,
            reason_codes=(reason, "PROP_RULE_ADAPTATION_FAILS_CLOSED_UNTIL_RESEARCH_IS_VERIFIED"),
        )
