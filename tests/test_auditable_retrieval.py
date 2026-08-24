from __future__ import annotations

from dataclasses import replace

import pytest

from equity_research.auditable_retrieval import (
    AuditableEvidenceRetriever,
    audit_artifact_claims,
    build_historical_financial_paths,
    build_historical_valuation_observations,
    canonical_source_type,
    evaluate_demo_freeze,
    execute_audit_repair_queue,
    extract_contractual_exposures,
    normalize_catalog_source_records,
    partial_rerun_eligible,
    semantic_evidence_hash,
)
from equity_research.evidence_inputs import evidence_is_claim_eligible
from equity_research.hybrid_research import _execute_registered_requests
from equity_research.models import (
    AnalogAuditDecision,
    AnalogQualityScorecard,
    AnalogResearchVariant,
    CompanyIdentity,
    FilingRecord,
    FinancialMetric,
    HistoricalEpisodeDossier,
    HybridResearchProfile,
    HybridResearchRun,
    ExecutionBackend,
    NormalizedSourceRecord,
    ResearchRoundArtifact,
)


IDENTITY = CompanyIdentity(
    ticker="AAPL",
    cik="0000320193",
    name="Apple Inc.",
    exchange="NASDAQ",
)


class FakeSecClient:
    def __init__(self) -> None:
        self.cached = False
        self.filing = FilingRecord(
            form="10-K",
            accession="0000320193-20-000096",
            filing_date="2020-10-30",
            report_date="2020-09-26",
            primary_doc="aapl-20200926.htm",
            description="Annual report",
            url=(
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019320000096/aapl-20200926.htm"
            ),
        )

    def get_comparable_filings(self, cik, form, limit=4):
        return [self.filing] if form == "10-K" else []

    def is_text_cached(self, url):
        return self.cached

    def get_filing_text(self, filing):
        return """
        <html><body>
        <p>Purchase obligations were $8.2 billion at September 26, 2020,
        primarily for manufacturing and supplier commitments.</p>
        <p>Operating cash flow was $80.7 billion.</p>
        </body></html>
        """

    def get_filing_documents(self, filing):
        return []


class FakePriceClient:
    def historical_daily_rows(self, ticker, *, start="", end=""):
        return {
            "status": "available",
            "provider": "fixture adjusted prices",
            "official": True,
            "adjusted": True,
            "source_url": "https://prices.example/AAPL",
            "retrieval_status": "cached_document",
            "rows": [
                {"date": "2020-01-02", "close": 75.0, "volume": 100},
                {"date": "2020-12-31", "close": 132.69, "volume": 200},
            ],
        }


def _source(
    source_id: str,
    *,
    origin: str,
    excerpt: str,
    source_type: str = "historical_filing",
) -> NormalizedSourceRecord:
    return NormalizedSourceRecord(
        source_id=source_id,
        ticker="AAPL",
        entity_name="Apple Inc.",
        security_id="AAPL",
        source_family="historical_primary",
        source_type=source_type,
        document_id=source_id,
        source_url=f"https://{origin}/{source_id}",
        publication_date="2020-12-31",
        reporting_period="2020-12-31",
        section="Historical outcome",
        page_or_table="",
        exact_excerpt=excerpt,
        metric_or_claim="Historical outcome",
        value=None,
        unit="",
        currency="USD",
        source_tier=1,
        origin_group=origin,
        licensing_policy="public_excerpt",
        retrieval_status="cached_document",
        parser_status="parsed",
        validation_status="validated",
        citation_id=f"citation-{source_id}",
    )


def test_semantic_hash_ignores_timestamps_order_and_cache_metadata() -> None:
    first = [
        {
            "evidence_id": "b",
            "period": "2020-12-31",
            "value": 2,
            "observed_at": "2026-01-01T00:00:00Z",
            "cache_status": "miss",
        },
        {
            "evidence_id": "a",
            "period": "2019-12-31",
            "value": 1,
            "latency_ms": 200,
        },
    ]
    second = [
        {**first[1], "latency_ms": 999},
        {
            **first[0],
            "observed_at": "2027-01-01T00:00:00Z",
            "cache_status": "hit",
        },
    ]
    first_hash, first_fingerprints = semantic_evidence_hash(
        first,
        prompt_version="p1",
        source_registry_version="r1",
    )
    second_hash, second_fingerprints = semantic_evidence_hash(
        second,
        prompt_version="p1",
        source_registry_version="r1",
    )
    assert first_hash == second_hash
    assert first_fingerprints == second_fingerprints


def test_catalog_match_is_discovery_not_live_retrieval() -> None:
    evidence = {
        "evidence_id": "e1",
        "citation_id": "c1",
        "kind": "validated_claim",
        "source_type": "sec_filing",
        "source_tier": 1,
        "period": "2025-09-27",
        "summary": "Operating cash flow increased.",
        "exact_excerpt": "Operating cash flow increased.",
        "source_url": "https://www.sec.gov/example",
        "retrieval_status": "cached_document",
        "parser_status": "parsed",
        "validation_status": "validated",
    }
    context = {
        "searchable_evidence_catalog": [evidence],
        "evidence_catalog": [evidence],
        "claim_eligible_evidence_ids": ["e1"],
    }
    outcomes = _execute_registered_requests(
        context,
        {
            "source_requests": [{
                "request_id": "request-1",
                "source_type": "sec_filing",
                "title": "Operating cash flow",
            }]
        },
    )
    assert outcomes[0]["status"] == "catalog_match"
    assert "fetched_live" not in outcomes[0]["retrieval_lifecycle"]


def test_metadata_only_source_cannot_support_a_claim() -> None:
    catalog = [{
        "evidence_id": "meta-1",
        "kind": "historical_source",
        "source_type": "reputable_publisher",
        "source_tier": 3,
        "period": "2020-01-01",
        "summary": "Headline metadata only.",
        "source_url": "https://publisher.example/item",
        "metadata_only": True,
    }]
    record = normalize_catalog_source_records(IDENTITY, catalog)[0]
    assert record.retrieval_status == "metadata_only"
    assert record.validation_status == "metadata_only"
    assert evidence_is_claim_eligible({
        **catalog[0],
        "retrieval_status": record.retrieval_status,
        "parser_status": record.parser_status,
        "validation_status": record.validation_status,
    }) is False


def test_sec_retrieval_returns_exact_periodized_validated_excerpt() -> None:
    retriever = AuditableEvidenceRetriever(
        IDENTITY,
        sec_client=FakeSecClient(),
        filings=[],
        catalog=[],
    )
    records, lifecycle = retriever.retrieve(
        request_id="sec-1",
        source_type="historical_filing",
        query="10-K purchase obligations supplier commitments",
        period="2020",
    )
    assert lifecycle.status == "validated"
    assert lifecycle.source_type == "historical_filing"
    assert records[0].reporting_period == "2020-09-26"
    assert "Purchase obligations" in records[0].exact_excerpt
    assert records[0].retrieval_status == "fetched_live"
    assert records[0].validation_status == "validated"


def test_ttm_fcf_uses_validated_trailing_values_not_interim_annualization() -> None:
    metrics = [
        FinancialMetric(
            "Operating Cash Flow",
            80.0,
            "USD",
            "2025-09-27",
            trailing_twelve_month_value=125.0,
            trailing_period_end="2025-09-27",
            trailing_method="four_aligned_quarters",
        ),
        FinancialMetric(
            "Capital Expenditure",
            10.0,
            "USD",
            "2025-09-27",
            trailing_twelve_month_value=14.0,
            trailing_period_end="2025-09-27",
            trailing_method="four_aligned_quarters",
        ),
    ]
    path = next(
        item for item in build_historical_financial_paths("AAPL", metrics)
        if item.metric == "Free Cash Flow TTM"
    )
    assert path.status == "available"
    assert path.observations[0]["value"] == pytest.approx(111.0)
    assert "TTM" in path.formula
    assert "annualized" not in path.formula.casefold()


def test_cash_conversion_bridge_separates_current_strength_from_recurrence() -> None:
    metrics = [
        FinancialMetric(
            "Operating Cash Flow", 117.0, "USD", "2026-06-27",
            trailing_twelve_month_value=146.724e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Net Income", 98.0, "USD", "2026-06-27",
            trailing_twelve_month_value=128.930e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Capital Expenditure", 6.8, "USD", "2026-06-27",
            trailing_twelve_month_value=10.041e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Dividends Paid", 11.8, "USD", "2026-06-27",
            trailing_twelve_month_value=15.640e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Repurchases", 62.1, "USD", "2026-06-27",
            trailing_twelve_month_value=82.226e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
    ]

    path = next(
        item for item in build_historical_financial_paths("AAPL", metrics)
        if item.metric == "Cash Conversion Bridge TTM"
    )
    observation = path.observations[0]

    assert path.status == "partial"
    assert observation["free_cash_flow"] == pytest.approx(136.683e9)
    assert observation["operating_cash_flow_to_net_income"] == pytest.approx(
        146.724 / 128.930
    )
    assert observation["cash_after_dividends_and_repurchases"] == pytest.approx(
        38.817e9
    )
    assert observation["recurrence_assessment"] == "insufficient_history"
    assert any("prior same-duration" in item for item in path.data_gaps)
    assert any("Cash taxes" in item for item in path.data_gaps)


def test_cash_conversion_bridge_rejects_mixed_trailing_periods() -> None:
    metrics = [
        FinancialMetric(
            "Operating Cash Flow", 1.0, "USD", "2026-06-27",
            trailing_twelve_month_value=146.0,
            trailing_period_end="2026-06-27",
        ),
        FinancialMetric(
            "Net Income", 1.0, "USD", "2026-03-28",
            trailing_twelve_month_value=128.0,
            trailing_period_end="2026-03-28",
        ),
        FinancialMetric(
            "Capital Expenditure", 1.0, "USD", "2026-06-27",
            trailing_twelve_month_value=10.0,
            trailing_period_end="2026-06-27",
        ),
    ]

    path = next(
        item for item in build_historical_financial_paths("AAPL", metrics)
        if item.metric == "Cash Conversion Bridge TTM"
    )

    assert path.status == "unavailable"
    assert path.observations == []
    assert "same trailing-period" in path.data_gaps[0]


def test_claim_audit_ignores_period_tokens_and_reconciles_derived_margin() -> None:
    records = [
        replace(
            _source(
                "revenue",
                origin="sec.gov",
                excerpt="Revenue: 111.2B USD",
            ),
            reporting_period="2026-03-28",
            metric_or_claim="Revenue",
        ),
        replace(
            _source(
                "gross-profit",
                origin="sec.gov",
                excerpt="Gross Profit: 54.8B USD",
            ),
            reporting_period="2026-03-28",
            metric_or_claim="Gross Profit",
        ),
    ]
    artifact = ResearchRoundArtifact(
        artifact_id="reconciliation",
        artifact_type="reconciliation_analyst",
        status="validated",
        payload={
            "claims": [{
                "claim": (
                    "Gross profit was $54.8B; gross margin approximately "
                    "49.3% for Q2 FY2026 ended March 28, 2026."
                ),
                "evidence_ids": ["revenue", "gross-profit"],
            }],
        },
    )

    audits, queue = audit_artifact_claims("AAPL", artifact, records)

    assert audits[0].period_alignment == "aligned"
    assert audits[0].numerical_reconciliation == "passed"
    assert audits[0].status == "passed"
    assert queue.items == []


def test_financial_paths_preserve_every_current_prior_pair() -> None:
    metrics = [
        FinancialMetric(
            "Revenue",
            100.0,
            "USD",
            "2025-09-27",
            fiscal_period="FY",
            form="10-K",
            previous_value=90.0,
            previous_period_end="2024-09-28",
        ),
        FinancialMetric(
            "Revenue",
            110.0,
            "USD",
            "2026-09-26",
            fiscal_period="FY",
            form="10-K",
            previous_value=100.0,
            previous_period_end="2025-09-27",
        ),
    ]
    path = next(
        item for item in build_historical_financial_paths("AAPL", metrics)
        if item.metric == "Revenue"
    )
    values = {
        (item["period"], item["value"]) for item in path.observations
    }
    assert values == {
        ("2024-09-28", 90.0),
        ("2025-09-27", 100.0),
        ("2026-09-26", 110.0),
    }
    assert all(item.get("source_id") for item in path.observations)


def test_issuer_announcement_request_can_use_validated_issuer_ir_text() -> None:
    source = _source(
        "issuer-1",
        origin="apple.com",
        excerpt=(
            "Apple announced a supplier capacity agreement and purchase "
            "commitment."
        ),
        source_type="issuer_ir",
    )
    retriever = AuditableEvidenceRetriever(
        IDENTITY,
        catalog=[{
            "evidence_id": source.source_id,
            "kind": "management_evidence",
            "source_type": source.source_type,
            "source_tier": 1,
            "period": source.reporting_period,
            "summary": source.exact_excerpt,
            "exact_excerpt": source.exact_excerpt,
            "source_url": source.source_url,
            "origin_group": source.origin_group,
            "retrieval_status": source.retrieval_status,
            "parser_status": source.parser_status,
            "validation_status": source.validation_status,
        }],
    )
    records, lifecycle = retriever.retrieve(
        request_id="issuer-request",
        source_type="issuer_announcement",
        query="supplier capacity agreement purchase commitment",
    )
    assert lifecycle.status == "catalog_match"
    assert records[0].source_id == "issuer-1"


def test_registered_institutional_source_fetches_underlying_content() -> None:
    source = NormalizedSourceRecord(
        source_id="fed-telecom",
        ticker="AAPL",
        entity_name="Apple Inc.",
        security_id="AAPL",
        source_family="historical_context",
        source_type="institutional_history",
        document_id="fed-telecom",
        source_url="https://www.federalreserve.gov/example",
        publication_date="2003-04-24",
        reporting_period="1998-2002",
        section="Investment bust",
        page_or_table="",
        exact_excerpt=(
            "telecommunications fiber overcapacity investment bust"
        ),
        metric_or_claim=(
            "telecommunications fiber overcapacity investment bust"
        ),
        value=None,
        unit="",
        currency="",
        source_tier=2,
        origin_group="federalreserve.gov",
        licensing_policy="public_institutional_excerpt",
        retrieval_status="metadata_only",
        parser_status="not_parsed",
        validation_status="metadata_only",
    )
    retriever = AuditableEvidenceRetriever(
        IDENTITY,
        fetch_text=lambda _url: (
            "<p>Telecommunications firms overinvested in long-haul fiber, "
            "leaving excess capacity after demand disappointed.</p>"
        ),
        registered_source_catalog=[source],
    )
    records, lifecycle = retriever.retrieve(
        request_id="institutional-request",
        source_type="institutional_history",
        query="telecommunications fiber overcapacity demand",
        period="1998-2002",
    )
    assert lifecycle.status == "validated"
    assert records[0].origin_group == "federalreserve.gov"
    assert "overinvested" in records[0].exact_excerpt


def test_price_and_valuation_path_uses_only_values_available_as_of_date() -> None:
    retriever = AuditableEvidenceRetriever(
        IDENTITY,
        price_client=FakePriceClient(),
    )
    records, lifecycle = retriever.retrieve(
        request_id="price-1",
        source_type="quantitative_historical_price",
        query="historical price path 2020",
        period="2020",
    )
    paths = [
        build_historical_financial_paths(
            "AAPL",
            [
                FinancialMetric(
                    "Diluted Shares",
                    16.0,
                    "shares",
                    "2020-09-26",
                    filed="2020-10-30",
                ),
                FinancialMetric(
                    "Cash",
                    20.0,
                    "USD",
                    "2020-09-26",
                    filed="2020-10-30",
                ),
                FinancialMetric(
                    "Long-term Debt",
                    10.0,
                    "USD",
                    "2020-09-26",
                    filed="2020-10-30",
                ),
            ],
        )
    ][0]
    valuations = build_historical_valuation_observations(
        "AAPL", paths, records
    )
    assert lifecycle.status == "validated"
    assert valuations
    assert all(item.no_lookahead_safe for item in valuations)
    assert valuations[-1].market_cap == pytest.approx(132.69 * 16.0)


def test_contractual_exposure_is_extracted_without_inferring_absence() -> None:
    record = _source(
        "contract-1",
        origin="sec.gov",
        excerpt=(
            "Purchase obligations were $8.2 billion, including minimum "
            "supplier commitments."
        ),
    )
    observations = extract_contractual_exposures("AAPL", [record])
    assert observations
    assert observations[0].status == "disclosed"
    assert observations[0].amount == pytest.approx(8.2e9)
    assert observations[0].source_ids == ["contract-1"]


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("regulator_release", "regulator_court"),
        ("issuer_newsroom", "issuer_announcement"),
        ("historical_price", "quantitative_historical_price"),
    ],
)
def test_registered_source_aliases_map_to_canonical_families(
    alias: str,
    canonical: str,
) -> None:
    assert canonical_source_type(alias) == canonical


def test_claim_level_audit_generates_repair_then_passes_two_origins() -> None:
    artifact = ResearchRoundArtifact(
        artifact_id="a1",
        artifact_type="reconciliation_analyst",
        status="validated",
        payload={
            "historical_analog_dossiers": [{
                "analog_id": "episode-1",
                "operating_outcome": "In 2020 revenue rose 50%.",
                "source_ids": ["s1"],
            }]
        },
    )
    first = _source(
        "s1",
        origin="sec.gov",
        excerpt="In 2020 revenue rose 50%.",
    )
    audits, queue = audit_artifact_claims("AAPL", artifact, [first])
    assert audits[0].status == "failed"
    assert queue.status == "open"
    assert queue.items[0].retrieval_request["source_type"] == "historical_filing"

    artifact.payload["historical_analog_dossiers"][0]["source_ids"] = [
        "s1",
        "s2",
    ]
    second = _source(
        "s2",
        origin="institution.example",
        excerpt="The 2020 record shows revenue rose 50%.",
        source_type="institutional_history",
    )
    audits, queue = audit_artifact_claims(
        "AAPL", artifact, [first, second]
    )
    assert audits[0].status == "passed"
    assert audits[0].independent_origin_count == 2
    assert queue.status == "closed"


def test_dossier_claim_audit_uses_field_level_evidence() -> None:
    filing = _source(
        "filing-1",
        origin="sec.gov",
        excerpt="Operating performance improved during 2020.",
    )
    price = replace(
        _source(
            "price-1",
            origin="prices.example",
            excerpt=(
                "Adjusted close 75.0 on 2020-01-02 and 132.69 on "
                "2020-12-31, a 76.92% increase."
            ),
            source_type="quantitative_historical_price",
        ),
        value=132.69,
        unit="price",
    )
    artifact = ResearchRoundArtifact(
        artifact_id="field-bound",
        artifact_type="reconciliation_analyst",
        status="validated",
        payload={
            "historical_analog_dossiers": [{
                "analog_id": "episode-field-bound",
                "operating_outcome": (
                    "Operating performance improved during 2020."
                ),
                "price_outcome": (
                    "Adjusted close moved from 75.0 on 2020-01-02 to "
                    "132.69 on 2020-12-31, a 76.92% increase."
                ),
                "source_ids": ["filing-1", "price-1"],
                "claim_evidence": [
                    {
                        "field": "operating_outcome",
                        "claim_text": (
                            "Operating performance improved during 2020."
                        ),
                        "evidence_ids": ["filing-1"],
                    },
                    {
                        "field": "price_outcome",
                        "claim_text": (
                            "Adjusted close moved from 75.0 on 2020-01-02 "
                            "to 132.69 on 2020-12-31, a 76.92% increase."
                        ),
                        "evidence_ids": ["price-1"],
                    },
                ],
            }]
        },
    )

    audits, _queue = audit_artifact_claims(
        "AAPL", artifact, [filing, price]
    )

    by_text = {item.claim_text: item for item in audits}
    price_audit = next(
        item for text, item in by_text.items()
        if "Adjusted close moved" in text
    )
    assert price_audit.supporting_evidence_ids == ["price-1"]
    assert "filing-1" not in price_audit.supporting_evidence_ids


def test_numeric_audit_reconciles_display_units_and_ignores_form_numbers() -> None:
    source = replace(
        _source(
            "revenue-q2",
            origin="sec.gov",
            excerpt=(
                "Revenue value: 111184000000.0 USD. The 10-Q notes that "
                "net sales and gross margins remain volatile."
            ),
        ),
        reporting_period="2026-03-28",
        publication_date="2026-05-01",
        value=111184000000.0,
        unit="USD",
        period_start="2025-12-28",
        duration_days=90,
    )
    artifact = ResearchRoundArtifact(
        artifact_id="scaled-value",
        artifact_type="reconciliation_analyst",
        status="validated",
        payload={
            "claims": [
                {
                    "claim": (
                        "Revenue was $111.2 billion for the three months "
                        "ended March 28, 2026."
                    ),
                    "evidence_ids": ["revenue-q2"],
                },
                {
                    "claim": (
                        "The 10-Q for the period ended March 28, 2026 notes "
                        "that net sales and gross margins remain volatile."
                    ),
                    "evidence_ids": ["revenue-q2"],
                },
            ]
        },
    )

    audits, _queue = audit_artifact_claims(
        "AAPL", artifact, [source]
    )

    assert all(item.status == "passed" for item in audits)


def test_duration_audit_rejects_six_month_claim_citing_quarter() -> None:
    source = replace(
        _source(
            "revenue-quarter",
            origin="sec.gov",
            excerpt="Revenue value: 111184000000.0 USD.",
        ),
        reporting_period="2026-03-28",
        period_start="2025-12-28",
        duration_days=90,
        value=111184000000.0,
        unit="USD",
    )
    artifact = ResearchRoundArtifact(
        artifact_id="wrong-duration",
        artifact_type="reconciliation_analyst",
        status="validated",
        payload={
            "claims": [{
                "claim": (
                    "Revenue was $111.2 billion for the six months ended "
                    "March 28, 2026."
                ),
                "evidence_ids": ["revenue-quarter"],
            }]
        },
    )

    audits, _queue = audit_artifact_claims(
        "AAPL", artifact, [source]
    )

    assert audits[0].status == "failed"
    assert any("fiscal durations" in item for item in audits[0].blockers)


def test_audit_repair_queue_executes_registered_retrieval() -> None:
    artifact = ResearchRoundArtifact(
        artifact_id="a2",
        artifact_type="reconciliation_analyst",
        status="validated",
        payload={
            "historical_analog_dossiers": [{
                "analog_id": "episode-2",
                "financing_outcome": (
                    "Purchase obligations were $8.2 billion in 2020."
                ),
                "source_ids": [],
            }]
        },
    )
    _, queue = audit_artifact_claims("AAPL", artifact, [])
    retriever = AuditableEvidenceRetriever(
        IDENTITY,
        sec_client=FakeSecClient(),
    )
    records, lifecycles = execute_audit_repair_queue(queue, retriever)
    assert records
    assert [item.status for item in lifecycles] == [
        "unavailable",
        "validated",
    ]
    assert queue.status == "evidence_added"
    assert queue.items[0].retrieval_outcome_ids


def test_partial_rerun_allows_only_additive_evidence() -> None:
    eligible, additions = partial_rerun_eligible(
        {"e1": "hash-1"},
        {"e1": "hash-1", "e2": "hash-2"},
    )
    assert eligible is True
    assert additions == ["e2"]
    assert partial_rerun_eligible(
        {"e1": "hash-1"},
        {"e1": "hash-1"},
    ) == (True, [])
    assert partial_rerun_eligible(
        {"e1": "hash-1"},
        {"e1": "changed"},
    ) == (False, [])


def test_failed_freeze_decision_preserves_existing_fixture_contract() -> None:
    run = HybridResearchRun(
        run_id="run-1",
        ticker="AAPL",
        status="investigated_draft",
        profile=HybridResearchProfile("p", "P"),
        execution_backend=ExecutionBackend(
            "fixture", "Fixture", "fixture", False
        ),
        deterministic_validation_status="partial",
    )
    decision = evaluate_demo_freeze(
        "AAPL",
        run=run,
        audited_dossiers=[],
        output_path="data/demo_hybrid/AAPL.json",
    )
    assert decision.allowed is False
    assert decision.status == "blocked"
    assert decision.output_path.endswith("AAPL.json")
    assert decision.blockers
