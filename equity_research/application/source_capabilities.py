from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .. import config
from ..domain.agent_research import (
    CredentialReference,
    SourceCapability,
    SourceCapabilityStatus,
)
from ..source_planner import SOURCE_REGISTRY


class SourceCapabilityRegistry:
    """Secret-free capability discovery over application-owned connectors."""

    def __init__(self, capabilities: Iterable[SourceCapability] = ()) -> None:
        self._capabilities = {
            item.capability_id: item for item in capabilities
        }

    def register(self, capability: SourceCapability) -> None:
        self._capabilities[capability.capability_id] = capability

    def all(self) -> tuple[SourceCapability, ...]:
        return tuple(sorted(
            self._capabilities.values(),
            key=lambda item: (item.label.casefold(), item.capability_id),
        ))

    def available(self) -> tuple[SourceCapability, ...]:
        eligible = {
            SourceCapabilityStatus.READY.value,
            SourceCapabilityStatus.CACHED_ONLY.value,
        }
        return tuple(item for item in self.all() if item.status in eligible)

    def for_source_family(self, source_family: str) -> tuple[SourceCapability, ...]:
        normalized = str(source_family or "").casefold()
        return tuple(
            item for item in self.all()
            if normalized in {value.casefold() for value in item.source_families}
        )

    def prompt_catalog(self) -> list[dict[str, Any]]:
        return [item.as_model_capability() for item in self.all()]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.all():
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts


@dataclass(frozen=True)
class SourceCapabilityRuntime:
    context: dict[str, Any]
    retrieval_engine: Any | None = None
    local_codex_ready: bool = False


def build_source_capability_registry(
    context: dict[str, Any],
    *,
    retrieval_engine: Any | None = None,
    local_codex_ready: bool = False,
) -> SourceCapabilityRegistry:
    """Resolve capabilities from live clients, loaded local secrets, and cache.

    Config has already been populated by the local secret manager. This
    function observes only whether a credential exists; it never returns the
    value or passes an opaque credential reference to a model prompt.
    """

    cached_types = {
        str(item.get("source_type") or "")
        for item in context.get("searchable_evidence_catalog", [])
        if isinstance(item, dict)
    }
    registered_records = list(
        getattr(retrieval_engine, "registered_source_catalog", ()) or ()
    )
    registered_types = {
        str(getattr(item, "source_type", "") or "")
        for item in registered_records
    }
    decision_context = context.get("decision_context") or {}
    consensus_context = (
        decision_context.get("consensus", {})
        if isinstance(decision_context, dict) else {}
    )
    resolved_provider_names = {
        _provider_slug(str(item.get("provider") or ""))
        for item in (
            consensus_context.get("provider_statuses", [])
            if isinstance(consensus_context, dict) else []
        )
        if isinstance(item, dict)
        and str(item.get("status") or "").casefold()
        not in {"unavailable", "failed", "missing_key", "disabled"}
    }
    if isinstance(consensus_context, dict) and consensus_context.get("provider"):
        resolved_provider_names.add(
            _provider_slug(str(consensus_context.get("provider") or ""))
        )

    capabilities: list[SourceCapability] = []
    capabilities.append(_capability(
        "sec_edgar",
        "SEC EDGAR filings and XBRL",
        ("sec_filing", "historical_filing", "agm_egm_proxy"),
        ("search", "retrieve", "open", "find", "extract_table", "structured_facts"),
        ready=bool(
            retrieval_engine is not None
            and getattr(retrieval_engine, "sec_client", None) is not None
            and str(context.get("security_key") or "").startswith("cik:")
        ),
        cached=bool({"sec_filing", "historical_filing"} & cached_types),
        provider="sec.gov",
        licensing="public_filing_excerpt",
        storage="permitted parsed documents and citation-bound excerpts",
        credential_key="SEC_USER_AGENT",
    ))

    provider_specs = (
        (
            "fmp",
            "Financial Modeling Prep",
            "FMP_API_KEY",
            ("consensus_manual", "earnings_transcript", "quantitative_historical_price"),
            ("search", "structured_data", "historical", "consensus_history"),
            "configured plan and endpoint entitlements apply",
        ),
        (
            "alpha_vantage",
            "Alpha Vantage",
            "ALPHAVANTAGE_API_KEY",
            ("consensus_manual", "earnings_transcript", "quantitative_historical_price", "macro_market"),
            ("structured_data", "historical", "consensus", "transcript"),
            "configured plan and endpoint entitlements apply",
        ),
        (
            "finnhub",
            "Finnhub",
            "FINNHUB_API_KEY",
            ("consensus_manual", "quantitative_historical_price", "public_news_metadata"),
            ("structured_data", "historical", "consensus", "search"),
            "configured plan and endpoint entitlements apply",
        ),
        (
            "tiingo",
            "Tiingo",
            "TIINGO_API_KEY",
            ("quantitative_historical_price",),
            ("historical", "structured_data"),
            "configured plan and endpoint entitlements apply",
        ),
        (
            "eodhd",
            "EODHD",
            "EODHD_API_KEY",
            ("quantitative_historical_price",),
            ("historical", "structured_data"),
            "configured plan and endpoint entitlements apply",
        ),
        (
            "polygon",
            "Polygon",
            "POLYGON_API_KEY",
            ("quantitative_historical_price", "public_news_metadata"),
            ("historical", "structured_data", "search"),
            "configured plan and endpoint entitlements apply",
        ),
        (
            "fred",
            "Federal Reserve Economic Data",
            "FRED_API_KEY",
            ("macro_market", "institutional_history"),
            ("structured_data", "historical"),
            "public macro series; provider limits apply",
        ),
        (
            "bea",
            "Bureau of Economic Analysis",
            "BEA_API_KEY",
            ("macro_market", "industry_official_dataset"),
            ("structured_data", "historical"),
            "public official statistics",
        ),
        (
            "census",
            "U.S. Census Bureau",
            "CENSUS_API_KEY",
            ("macro_market", "trade_import_export", "industry_official_dataset"),
            ("structured_data", "historical"),
            "public official statistics",
        ),
        (
            "bls",
            "Bureau of Labor Statistics",
            "BLS_API_KEY",
            ("macro_market", "labor_employment", "industry_official_dataset"),
            ("structured_data", "historical"),
            "public official statistics",
        ),
        (
            "newsapi",
            "NewsAPI",
            "NEWSAPI_API_KEY",
            ("public_news_metadata", "reputable_publisher"),
            ("search", "metadata"),
            "metadata and permitted content only",
        ),
        (
            "event_registry",
            "Event Registry",
            "EVENT_REGISTRY_API_KEY",
            ("public_news_metadata", "reputable_publisher"),
            ("search", "metadata"),
            "metadata and permitted content only",
        ),
        (
            "wisburg",
            "Wisburg research",
            "WISBURG_API_KEY",
            ("external_research",),
            ("search", "metadata", "permitted_excerpt"),
            "user entitlement; requires independent corroboration",
        ),
    )
    for capability_id, label, key, families, operations, entitlement in provider_specs:
        adapter_enabled = capability_id not in {"polygon", "event_registry"}
        if not adapter_enabled:
            capabilities.append(SourceCapability(
                capability_id=capability_id,
                label=label,
                source_families=families,
                operations=operations,
                status=SourceCapabilityStatus.UNAVAILABLE.value,
                provider=capability_id,
                freshness="unavailable",
                licensing_policy="configured_adapter_slot",
                storage_policy="no provider payload is retained",
                cost_note="Provider adapter is not enabled in this release.",
                entitlement_note=(
                    entitlement
                    + "; a locally encrypted credential may be configured, but the connector slot is not executable yet"
                ),
            ))
            continue
        capabilities.append(_capability(
            capability_id,
            label,
            families,
            operations,
            ready=bool(
                getattr(config, key, "")
                or _provider_slug(capability_id) in resolved_provider_names
            ),
            cached=bool(set(families) & cached_types),
            provider=capability_id,
            licensing=(
                "licensed_or_user_entitled"
                if capability_id in {"fmp", "alpha_vantage", "finnhub", "tiingo", "eodhd", "polygon", "newsapi", "event_registry", "wisburg"}
                else "public_official_data"
            ),
            storage="normalized observations and policy-permitted excerpts",
            credential_key=key,
            entitlement=entitlement,
        ))

    keyless_specs = (
        (
            "registered_issuer_sources",
            "Registered issuer and counterparty documents",
            (
                "issuer_ir", "issuer_ir_report", "issuer_announcement",
                "issuer_management_remarks", "presentation",
                "supplier_customer_announcement", "global_peer_official_document",
            ),
            "public and registered source terms apply",
        ),
        (
            "official_regulatory_sources",
            "Regulator, court, safety, patent, and procurement sources",
            (
                "regulator_court", "product_safety", "patent_ip",
                "government_contract", "trade_import_export", "labor_employment",
            ),
            "public official records",
        ),
        (
            "academic_institutional_sources",
            "Academic and institutional research",
            ("institutional_history", "industry_official_dataset"),
            "open full text or authoritative metadata; publisher terms apply",
        ),
    )
    for capability_id, label, families, licensing in keyless_specs:
        has_registered = bool(set(families) & registered_types)
        capabilities.append(_capability(
            capability_id,
            label,
            families,
            ("search", "retrieve", "open", "find", "extract_table"),
            # A generic retriever is not proof that a catalog or URL exists
            # for this family. Advertising it as ready wastes agent turns on
            # source requests the application cannot fulfill.
            ready=has_registered,
            cached=bool(set(families) & cached_types),
            provider="registered_connectors",
            licensing=licensing,
            storage="policy-permitted documents, metadata, and citation-bound excerpts",
        ))

    capabilities.append(SourceCapability(
        capability_id="local_codex",
        label="Authenticated local Codex",
        source_families=("independent_research_qa",),
        operations=("frozen_audit", "adversarial_scout", "closure"),
        status=(
            SourceCapabilityStatus.READY.value
            if local_codex_ready else SourceCapabilityStatus.UNAVAILABLE.value
        ),
        provider="codex_local",
        licensing_policy="model_execution_only",
        storage_policy="no authentication material persisted",
        entitlement_note="Uses existing local Codex authentication; no API key is exposed.",
    ))

    allowed_types = {
        str(value) for value in context.get("allowed_source_types", [])
    }
    known_families = {
        family for capability in capabilities for family in capability.source_families
    }
    for entry in SOURCE_REGISTRY:
        if entry.source_type in known_families:
            continue
        if allowed_types and entry.source_type not in allowed_types:
            continue
        capabilities.append(_capability(
            f"registered_{entry.source_type}",
            entry.label,
            (entry.source_type,),
            ("search", "retrieve", "open"),
            ready=entry.source_type in registered_types,
            cached=entry.source_type in cached_types,
            provider="registered_connectors",
            licensing=entry.licensing_policy,
            storage="source-policy controlled",
            entitlement=entry.notes,
        ))
    return SourceCapabilityRegistry(capabilities)


def _capability(
    capability_id: str,
    label: str,
    families: tuple[str, ...],
    operations: tuple[str, ...],
    *,
    ready: bool,
    cached: bool,
    provider: str,
    licensing: str,
    storage: str,
    credential_key: str = "",
    entitlement: str = "",
) -> SourceCapability:
    status = (
        SourceCapabilityStatus.READY.value
        if ready else
        SourceCapabilityStatus.CACHED_ONLY.value
        if cached else
        SourceCapabilityStatus.ENTITLEMENT_REQUIRED.value
        if credential_key else
        SourceCapabilityStatus.UNAVAILABLE.value
    )
    reference = (
        CredentialReference(
            reference_id=f"local-vault:{credential_key}",
            source_label=label,
        )
        if credential_key and ready else None
    )
    return SourceCapability(
        capability_id=capability_id,
        label=label,
        source_families=families,
        operations=operations,
        status=status,
        provider=provider,
        freshness="live_or_policy_eligible_cache" if ready else "eligible_cache_only" if cached else "unavailable",
        licensing_policy=licensing,
        storage_policy=storage,
        cost_note="Provider plan and configured research budget apply." if credential_key else "No provider fee expected.",
        entitlement_note=entitlement,
        credential_reference=reference,
    )


def _provider_slug(value: str) -> str:
    normalized = "_".join(
        "".join(
            character if character.isalnum() else " "
            for character in str(value or "").casefold()
        ).split()
    )
    return {
        "financial_modeling_prep": "fmp",
        "alpha_vantage": "alpha_vantage",
        "event_registry_newsapi_ai": "event_registry",
    }.get(normalized, normalized)
