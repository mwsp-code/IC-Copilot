from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable

from ..domain.agent_research import (
    EvidenceCoveragePlan,
    ResearchAgenda,
    ResearchAgendaItem,
    ResearchQuestionDependency,
    ResearchQuestionTemplate,
)
from ..domain.research_intents import ResearchIntentResolver


RESEARCH_QUESTION_LIBRARY_VERSION = "comprehensive-research-agenda-v1"


def _dependency(question_key: str, *, material: bool = True) -> ResearchQuestionDependency:
    return ResearchQuestionDependency(question_key=question_key, material=material)


DEFAULT_RESEARCH_QUESTIONS: tuple[ResearchQuestionTemplate, ...] = (
    ResearchQuestionTemplate(
        question_key="business_operating_model",
        domain="Business and operating model",
        title="How does the company create, sustain, and lose economic value?",
        objective=(
            "Map revenue mechanisms, segments, geography, customers, pricing, volume, mix, "
            "costs, competitive position, and industry structure."
        ),
        decision_relevance="Defines the causal mechanism that every thesis and scenario must use.",
        confirming_evidence=("Segment and geographic economics", "Pricing, volume, mix, and cost mechanisms"),
        contradicting_evidence=("Weakening competitive position", "Economics dominated by temporary or accounting effects"),
        suggested_source_families=("sec_filing", "issuer_ir_report", "earnings_transcript", "industry_official_dataset"),
        analytical_tools=("search_sources", "open_source", "extract_table", "compare_periods"),
        expected_answer_shape=("business model", "principal mechanisms", "durability", "limiting observation"),
        keywords=("business model", "segment", "revenue", "pricing", "volume", "mix", "competition"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="financial_statement_integrity",
        domain="Financial-statement integrity",
        title="Do the headline financial facts reconcile to exact comparable disclosures?",
        objective=(
            "Reconcile material metrics to filing tables, XBRL contexts, start dates, durations, "
            "units, currencies, comparable periods, accounting changes, and footnotes."
        ),
        decision_relevance="Prevents period, unit, taxonomy, and accounting errors from entering the thesis.",
        confirming_evidence=("Exact filing or XBRL context", "Period-aligned comparable value"),
        contradicting_evidence=("Mixed durations or units", "Non-comparable tags, reclassifications, or duplicated contexts"),
        suggested_source_families=("sec_filing", "issuer_ir_report"),
        analytical_tools=("query_sec_facts", "open_source", "extract_table", "compare_periods"),
        expected_answer_shape=("reconciliation table", "exceptions", "mapping confidence"),
        keywords=("reconcile", "xbrl", "filing", "duration", "comparable", "accounting", "metric"),
        default_materiality="Critical",
    ),
    ResearchQuestionTemplate(
        question_key="operating_drivers",
        domain="Operating drivers",
        title="Which operating drivers explain the current change, and are they durable?",
        objective=(
            "Test demand breadth, margins, product and service mix, capacity, supply, pricing, "
            "operating leverage, and temporary versus recurring effects."
        ),
        decision_relevance="Connects reported outcomes to an investable causal explanation.",
        confirming_evidence=("Company-specific driver bridge", "Evidence of persistence across products, periods, or geographies"),
        contradicting_evidence=("Timing, refunds, channel build, or price-only growth", "Driver reversal or narrow concentration"),
        suggested_source_families=("sec_filing", "issuer_ir_report", "earnings_transcript", "industry_official_dataset"),
        analytical_tools=("search_sources", "open_source", "extract_table", "compare_periods"),
        dependencies=(_dependency("business_operating_model"), _dependency("financial_statement_integrity")),
        expected_answer_shape=("driver bridge", "recurring and temporary components", "monitoring indicators"),
        keywords=("driver", "margin", "demand", "pricing", "volume", "mix", "durable", "organic"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="cash_generation_capital_allocation",
        domain="Cash generation and capital allocation",
        title="Is cash conversion recurring after investment, taxes, financing, and capital returns?",
        objective=(
            "Reconcile net income to operating cash flow, working capital, cash taxes, capital "
            "expenditure, free cash flow, debt and financing, dividends, repurchases, dilution, "
            "and per-share value creation across annual and quarterly periods."
        ),
        decision_relevance="Tests earnings quality, reinvestment needs, liquidity, and shareholder value creation.",
        confirming_evidence=("Period-aligned cash-flow bridge", "Recurring conversion after working capital and capex", "Net share-count benefit after compensation"),
        contradicting_evidence=("Working-capital release or financing masks weak cash generation", "Capital returns consume cash without per-share benefit"),
        suggested_source_families=("sec_filing", "issuer_ir_report", "quantitative_historical_price", "consensus_manual"),
        analytical_tools=("query_sec_facts", "extract_table", "compute_financial_bridge", "compare_periods"),
        dependencies=(_dependency("financial_statement_integrity"),),
        expected_answer_shape=("annual and quarterly bridge", "recurrence assessment", "capital-allocation conclusion", "confidence"),
        keywords=("cash conversion", "cash generation", "free cash flow", "working capital", "capex", "tax", "financing", "repurchase", "capital return"),
        default_materiality="Critical",
    ),
    ResearchQuestionTemplate(
        question_key="balance_sheet_credit",
        domain="Balance sheet and credit",
        title="Can liquidity and credit capacity support the thesis through the downside case?",
        objective=(
            "Assess liquidity, debt maturity, interest coverage, leases, guarantees, commitments, "
            "covenants, refinancing risk, and downside resilience."
        ),
        decision_relevance="Determines financing flexibility and the path from operating risk to equity impairment.",
        confirming_evidence=("Maturity and liquidity coverage", "Downside funding resilience"),
        contradicting_evidence=("Near-term refinancing or covenant stress", "Material guarantees, recourse, or hidden obligations"),
        suggested_source_families=("sec_filing", "issuer_ir_report", "regulator_court"),
        analytical_tools=("query_sec_facts", "open_source", "extract_table", "compute_financial_bridge"),
        dependencies=(_dependency("financial_statement_integrity"), _dependency("cash_generation_capital_allocation")),
        expected_answer_shape=("liquidity bridge", "maturity profile", "downside transmission", "credit lens"),
        keywords=("credit", "debt", "liquidity", "maturity", "coverage", "lease", "guarantee", "refinancing"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="reverse_valuation_expectations",
        domain="Reverse FCF/DCF and market-implied expectations",
        title="What operating outcome must the current price be discounting?",
        objective=(
            "Use current enterprise value and user-editable assumptions to infer the growth, "
            "margin, reinvestment, duration, discount-rate, and terminal expectations embedded in price."
        ),
        decision_relevance="Frames valuation as an expectations problem rather than a single target price.",
        confirming_evidence=("Current price and enterprise-value inputs", "Reproducible reverse FCF or DCF calculation"),
        contradicting_evidence=("Implausible or inconsistent normalized assumptions", "Stale price or unsupported financial base"),
        suggested_source_families=("sec_filing", "quantitative_historical_price", "consensus_manual"),
        analytical_tools=("query_market_data", "query_sec_facts", "compute_reverse_valuation"),
        dependencies=(_dependency("financial_statement_integrity"), _dependency("cash_generation_capital_allocation")),
        expected_answer_shape=("implied operating assumptions", "comparison with internal scenarios", "sensitivity", "confidence"),
        keywords=("reverse fcf", "reverse dcf", "market implied", "implied expectations", "enterprise value", "current price"),
        default_materiality="Critical",
    ),
    ResearchQuestionTemplate(
        question_key="market_priced_in",
        domain="What the market has priced in",
        title="Is the proposed information absent from, partly embedded in, or already reflected by the market?",
        objective=(
            "Combine reverse valuation, point-in-time consensus and revisions, documented debate, "
            "and raw, market-relative, sector-relative, and beta-adjusted event reactions."
        ),
        decision_relevance="Distinguishes a good company observation from a mispriced investment thesis.",
        confirming_evidence=("Dated expectations omit the proposed driver", "Price and consensus underreact relative to the mechanism"),
        contradicting_evidence=("Consensus or valuation already embeds the driver", "Price and estimates fully react to the event"),
        suggested_source_families=("consensus_manual", "quantitative_historical_price", "external_research", "macro_market"),
        analytical_tools=("query_consensus_history", "query_market_data", "compute_event_attribution", "compute_reverse_valuation"),
        dependencies=(_dependency("reverse_valuation_expectations"), _dependency("operating_drivers")),
        expected_answer_shape=("priced-in classification", "expectations evidence", "reaction evidence", "remaining uncertainty"),
        keywords=("priced in", "underappreciated", "market capture", "consensus", "estimate revision", "reaction", "expectations"),
        default_materiality="Critical",
    ),
    ResearchQuestionTemplate(
        question_key="valuation_expected_return",
        domain="Valuation and expected return",
        title="What payoff follows from evidence-based bull, base, and bear operating paths?",
        objective=(
            "Build operating scenarios, probability-weighted value, expected return, downside, "
            "sensitivities, and explicit separation of evidence-backed inputs from assumptions."
        ),
        decision_relevance="Translates the thesis into payoff, downside, and decision thresholds.",
        confirming_evidence=("Scenario inputs tied to researched mechanisms", "Reproducible valuation and sensitivity"),
        contradicting_evidence=("Value depends on unsupported inputs", "Downside overwhelms plausible expected return"),
        suggested_source_families=("sec_filing", "consensus_manual", "quantitative_historical_price"),
        analytical_tools=("compute_reverse_valuation", "compare_periods"),
        dependencies=(_dependency("reverse_valuation_expectations"), _dependency("operating_drivers"), _dependency("balance_sheet_credit")),
        expected_answer_shape=("bull/base/bear", "weighted value", "expected return", "sensitivity"),
        keywords=("valuation", "fair value", "expected return", "bull", "bear", "scenario", "downside"),
        default_materiality="Critical",
    ),
    ResearchQuestionTemplate(
        question_key="catalysts_why_now",
        domain="Catalysts and why now",
        title="Why should the market recognize the mechanism now rather than later?",
        objective=(
            "Validate events, transmission mechanisms, timing windows, recognition paths, and monitoring triggers."
        ),
        decision_relevance="Separates a long-term observation from an actionable timing thesis.",
        confirming_evidence=("Dated material catalyst", "Explicit transmission to operating expectations or valuation"),
        contradicting_evidence=("No timing mechanism", "Event already reflected or operationally immaterial"),
        suggested_source_families=("issuer_announcement", "sec_filing", "regulator_court", "consensus_manual", "quantitative_historical_price"),
        analytical_tools=("search_sources", "open_source", "query_consensus_history", "compute_event_attribution"),
        dependencies=(_dependency("market_priced_in"),),
        expected_answer_shape=("validated event", "mechanism", "timing", "monitor"),
        keywords=("catalyst", "why now", "timing", "event", "recognition"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="counter_thesis_risks",
        domain="Counter-thesis and principal risks",
        title="What is the strongest evidence-based way the thesis could be wrong?",
        objective=(
            "Investigate regulatory, legal, competitive, financial, execution, accounting, and valuation risks and define falsification conditions."
        ),
        decision_relevance="Prevents confirmation bias and makes conviction falsifiable.",
        confirming_evidence=("Material contrary evidence tested", "Explicit falsification thresholds"),
        contradicting_evidence=("Counter-case relies only on generic boilerplate", "Material contradiction ignored"),
        suggested_source_families=("sec_filing", "regulator_court", "reputable_publisher", "external_research", "global_peer_official_document"),
        analytical_tools=("search_sources", "open_source", "query_market_data", "compare_periods"),
        dependencies=(_dependency("business_operating_model"),),
        expected_answer_shape=("strongest counter-thesis", "evidence", "transmission", "falsification conditions"),
        keywords=("counter thesis", "risk", "disprove", "falsify", "downside", "contradiction"),
        default_materiality="Critical",
    ),
    ResearchQuestionTemplate(
        question_key="management_credibility",
        domain="Management credibility",
        title="Does management's record support relying on its current narrative?",
        objective=(
            "Compare guidance accuracy, disclosure changes, incentive alignment, capital allocation, and narrative consistency with financial outcomes."
        ),
        decision_relevance="Calibrates how much weight to place on management claims and targets.",
        confirming_evidence=("Prior guidance reconciles with outcomes", "Capital allocation matches stated priorities"),
        contradicting_evidence=("Repeated misses or narrative drift", "Incentives or allocation conflict with the thesis"),
        suggested_source_families=("sec_filing", "earnings_transcript", "issuer_management_remarks", "agm_egm_proxy"),
        analytical_tools=("search_sources", "open_source", "compare_periods"),
        expected_answer_shape=("track record", "current credibility", "exceptions", "monitor"),
        keywords=("management", "credibility", "guidance", "incentive", "track record"),
        default_materiality="Medium",
    ),
    ResearchQuestionTemplate(
        question_key="historical_analogs_playbooks",
        domain="Historical analogs and playbooks",
        title="Which historical episodes test this mechanism, and where does transfer break down?",
        objective=(
            "Find same-issuer, peer, sector, and regime analogs; reconstruct mechanism, chronology, "
            "operating, market and financing outcomes; test counterexamples and transfer limits."
        ),
        decision_relevance="Uses history to challenge mechanism durability, timing, payoff, and failure modes.",
        confirming_evidence=("Issuer-correct primary chronology", "Independent origin for quantified outcomes", "Material similarities"),
        contradicting_evidence=("Wrong issuer or syndicated provenance", "Material business-model or regime differences", "Failed counterexamples"),
        suggested_source_families=("historical_filing", "quantitative_historical_price", "institutional_history", "global_peer_official_document"),
        analytical_tools=("search_sources", "open_source", "query_market_data", "compare_periods"),
        dependencies=(_dependency("business_operating_model"), _dependency("operating_drivers")),
        expected_answer_shape=("ranked candidates", "audited dossier", "transfer limits", "lessons and monitors"),
        keywords=("historical analog", "playbook", "history", "episode", "precedent", "counterexample"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="peers_readthrough",
        domain="Peers and read-through",
        title="What do relevant peers, suppliers, and customers confirm or contradict?",
        objective=(
            "Compare operating evidence, competition, valuation, supplier and customer signals, and cross-company contradictions."
        ),
        decision_relevance="Tests whether the mechanism is issuer-specific, industry-wide, or contradicted externally.",
        confirming_evidence=("Comparable peer mechanism", "Supplier or customer corroboration"),
        contradicting_evidence=("Peer evidence points the other way", "Comparison is not economically comparable"),
        suggested_source_families=("global_peer_official_document", "supplier_customer_announcement", "industry_official_dataset"),
        analytical_tools=("search_sources", "open_source", "compare_periods"),
        dependencies=(_dependency("business_operating_model"),),
        expected_answer_shape=("peer read-through", "differences", "implication"),
        keywords=("peer", "supplier", "customer", "read through", "competitor"),
        default_materiality="Medium",
    ),
    ResearchQuestionTemplate(
        question_key="contracts_hidden_exposures",
        domain="Contracts, obligations, and hidden exposures",
        title="What contractual or off-balance-sheet exposures could change the economics?",
        objective=(
            "Extract purchase obligations, supplier commitments, inventory, prepayments, leases, "
            "guarantees, concentration, recourse, residual-value exposure, and cancellation terms."
        ),
        decision_relevance="Finds economic obligations that headline financial statements can obscure.",
        confirming_evidence=("Exact commitment and lease tables", "Ownership, recourse, and cancellation terms"),
        contradicting_evidence=("Material non-cancellable obligations", "Guarantees, concentration, or residual-value support"),
        suggested_source_families=("sec_filing", "supplier_customer_announcement", "trade_import_export", "regulator_court"),
        analytical_tools=("search_sources", "open_source", "extract_table", "query_sec_facts"),
        dependencies=(_dependency("financial_statement_integrity"),),
        expected_answer_shape=("exposure table", "risk allocation", "change over time", "thesis implication"),
        keywords=("contract", "obligation", "commitment", "lease", "guarantee", "recourse", "residual value", "concentration"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="regulatory_legal",
        domain="Regulatory and legal status",
        title="What is the current procedural and economic status of material legal matters?",
        objective=(
            "Establish decisions, procedures, remedies, implementation dates, appeals, penalties, and economic transmission channels."
        ),
        decision_relevance="Translates legal headlines into current operating and valuation consequences.",
        confirming_evidence=("Current official decision or docket status", "Documented remedy and implementation path"),
        contradicting_evidence=("Escalating enforcement or mandatory business-model change", "Unbounded remedy or appeal risk"),
        suggested_source_families=("regulator_court", "sec_filing", "issuer_announcement", "reputable_publisher"),
        analytical_tools=("search_sources", "open_source", "find_in_source"),
        expected_answer_shape=("matter-by-matter status", "remedy", "appeal", "economic transmission"),
        keywords=("regulatory", "legal", "court", "antitrust", "dma", "appeal", "remedy", "penalty"),
        default_materiality="High",
    ),
    ResearchQuestionTemplate(
        question_key="adaptive_macro",
        domain="Adaptive Macro",
        title="Which macro exposures materially change the company mechanism?",
        objective=(
            "Select at most three decision-relevant rate, currency, inflation, trade, regulation, commodity, credit, or geographic exposures."
        ),
        decision_relevance="Connects macro context to company-specific cash flow and valuation rather than adding generic commentary.",
        confirming_evidence=("Quantified company transmission channel", "Relevant official macro series"),
        contradicting_evidence=("Exposure is immaterial or hedged", "Macro narrative lacks a company mechanism"),
        suggested_source_families=("macro_market", "trade_import_export", "industry_official_dataset", "sec_filing"),
        analytical_tools=("query_macro_series", "search_sources", "compare_periods"),
        expected_answer_shape=("up to three exposures", "transmission", "sensitivity", "monitor"),
        keywords=("macro", "rates", "currency", "inflation", "trade", "commodity", "credit", "geographic"),
        default_materiality="Medium",
    ),
    ResearchQuestionTemplate(
        question_key="external_research_wisburg",
        domain="External research and Wisburg",
        title="Which external themes are worth testing, and which remain uncorroborated?",
        objective=(
            "Use analyst debate, expert clues, Wisburg, and external research to generate hypotheses that are corroborated through independent evidence."
        ),
        decision_relevance="Expands idea discovery without allowing external opinion to promote itself into a thesis.",
        confirming_evidence=("Independent issuer, filing, market, or consensus corroboration",),
        contradicting_evidence=("Theme is stale, crowded, already priced, or unsupported by primary evidence",),
        suggested_source_families=("external_research", "reputable_publisher", "sec_filing", "issuer_ir_report", "consensus_manual"),
        analytical_tools=("search_sources", "open_source", "query_consensus_history", "compute_reverse_valuation"),
        expected_answer_shape=("themes tested", "corroboration", "rejections", "investigation candidates"),
        keywords=("wisburg", "external research", "analyst", "expert", "theme", "non consensus"),
        default_materiality="Medium",
    ),
    ResearchQuestionTemplate(
        question_key="monitoring_investigation",
        domain="Monitoring and investigation plan",
        title="What evidence would change the decision, and what should be monitored next?",
        objective=(
            "Define KPIs, events, falsification evidence, unresolved questions, and the highest-information-gain next actions."
        ),
        decision_relevance="Turns the research artifact into an updateable decision process.",
        confirming_evidence=("Measurable monitor and evidence source", "Explicit decision threshold"),
        contradicting_evidence=("Generic action with no completion criterion",),
        suggested_source_families=("sec_filing", "issuer_announcement", "consensus_manual", "quantitative_historical_price"),
        analytical_tools=("list_source_capabilities", "record_research_note"),
        dependencies=(_dependency("counter_thesis_risks"), _dependency("catalysts_why_now")),
        expected_answer_shape=("KPIs", "falsification triggers", "ranked next actions", "completion criteria"),
        keywords=("monitor", "investigate next", "next action", "falsification", "kpi"),
        default_materiality="High",
    ),
)


class ResearchQuestionLibrary:
    def __init__(
        self,
        templates: Iterable[ResearchQuestionTemplate] = DEFAULT_RESEARCH_QUESTIONS,
        *,
        version: str = RESEARCH_QUESTION_LIBRARY_VERSION,
    ) -> None:
        self.version = version
        self._templates = {item.question_key: item for item in templates}

    def all(self) -> tuple[ResearchQuestionTemplate, ...]:
        return tuple(self._templates.values())

    def get(self, question_key: str) -> ResearchQuestionTemplate | None:
        return self._templates.get(question_key)

    def matching(self, objective: str) -> tuple[ResearchQuestionTemplate, ...]:
        normalized = str(objective or "").casefold()
        ranked: list[tuple[int, ResearchQuestionTemplate]] = []
        for item in self._templates.values():
            score = sum(1 for token in item.keywords if token in normalized)
            if item.question_key.replace("_", " ") in normalized:
                score += 3
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].question_key))
        return tuple(item for _, item in ranked)


class ResearchAgendaBuilder:
    def __init__(self, library: ResearchQuestionLibrary | None = None) -> None:
        self.library = library or ResearchQuestionLibrary()

    def build(
        self,
        *,
        ticker: str,
        company_name: str,
        research_profile: str,
        security_archetype: str = "operating_company",
        cik: str = "",
        exchange: str = "",
        selected_objective: str = "",
        existing_questions: Iterable[Any] = (),
        model_additions: Iterable[dict[str, Any]] = (),
    ) -> ResearchAgenda:
        focused = bool(selected_objective.strip())
        resolver = ResearchIntentResolver()
        templates = (
            list(self.library.matching(selected_objective))
            if focused else list(self.library.all())
        )
        if focused and not templates:
            templates = [self.library.get("monitoring_investigation")]  # type: ignore[list-item]
        templates = [
            item for item in templates
            if item is not None
            and _applies_to_archetype(item, security_archetype)
        ]
        templates = self._include_dependencies(
            templates, security_archetype=security_archetype
        )
        items: list[ResearchAgendaItem] = []
        id_by_key: dict[str, str] = {}
        intent_ids: set[str] = set()
        selected_question_id = ""
        for index, template in enumerate(templates):
            mapped = resolver.resolve_legacy(
                ticker=ticker,
                cik=cik,
                exchange=exchange,
                question_key=template.question_key,
                title=template.title,
                objective=(selected_objective.strip()
                           if focused and index == 0 else template.objective),
                question_status="researching" if focused and index == 0 else "planned",
                materiality=template.default_materiality,
                origin_refs=(f"library:{self.library.version}:{template.question_key}",),
            )
            research_intent = mapped.intent
            if research_intent is not None:
                research_intent = resolver.upsert(research_intent).intent
                intent_ids.add(research_intent.intent_id)
            question_id = _stable_id(
                "agenda-question", ticker.upper(), template.question_key, selected_objective
            )
            id_by_key[template.question_key] = question_id
            priority = _priority_for(template, focused=focused, rank=index)
            items.append(ResearchAgendaItem(
                question_id=question_id,
                question_key=template.question_key,
                domain=template.domain,
                title=template.title,
                objective=(
                    selected_objective.strip()
                    if focused and index == 0
                    else template.objective
                ),
                decision_relevance=template.decision_relevance,
                materiality=template.default_materiality,
                priority=priority,
                source_families=list(template.suggested_source_families),
                analytical_tools=list(template.analytical_tools),
                confirming_evidence=list(template.confirming_evidence),
                contradicting_evidence=list(template.contradicting_evidence),
                expected_answer_shape=list(template.expected_answer_shape),
                selection_reason=(
                    "Selected investigation and material dependencies."
                    if focused else
                    "Applicable comprehensive research domain."
                ),
                coverage=EvidenceCoveragePlan(
                    confirming_needed=list(template.confirming_evidence),
                    contradicting_needed=list(template.contradicting_evidence),
                ),
                intent_id=(research_intent.intent_id if research_intent else ""),
                mechanism_key=(research_intent.mechanism_key if research_intent else ""),
                target_type=(research_intent.target_type if research_intent else "issuer"),
                target_id=(research_intent.target_id if research_intent else ticker.upper()),
                period_scope=(research_intent.period_scope if research_intent else "current_research_cutoff"),
                decision_dependency=(research_intent.decision_dependency if research_intent else "decision_quality"),
            ))
            if focused and index == 0:
                selected_question_id = question_id
        for item in items:
            template = self.library.get(item.question_key)
            if template is not None:
                item.dependency_question_ids = [
                    id_by_key[value.question_key]
                    for value in template.dependencies
                    if value.question_key in id_by_key
                ]

        items.extend(self._existing_items(
            ticker=ticker,
            existing_questions=existing_questions,
            existing_keys={item.question_key for item in items},
            existing_intent_ids=intent_ids,
            resolver=resolver,
            cik=cik,
            exchange=exchange,
        ))
        items.extend(self._model_items(
            ticker=ticker,
            additions=model_additions,
            existing_keys={item.question_key for item in items},
            existing_intent_ids=intent_ids,
            resolver=resolver,
            cik=cik,
            exchange=exchange,
        ))
        items.sort(key=lambda item: (-item.priority, item.domain, item.question_id))
        return ResearchAgenda(
            agenda_id=_stable_id(
                "research-agenda", ticker.upper(), research_profile, selected_objective,
                self.library.version,
            ),
            ticker=ticker.upper(),
            company_name=company_name,
            research_profile=research_profile,
            items=items,
            selected_question_id=selected_question_id,
            security_archetype=security_archetype,
            library_version=self.library.version,
            generated_at=_utc_now(),
            selection_summary=(
                f"Focused investigation plus {max(0, len(items) - 1)} material dependency question(s)."
                if focused else
                f"Seeded {len(items)} applicable research domains for model prioritization."
            ),
            security_key=(
                next(
                    (
                        intent.security_key
                        for intent in resolver.all()
                        if intent.security_key
                    ),
                    f"ticker:{ticker.upper()}",
                )
            ),
        )

    def _include_dependencies(
        self,
        templates: list[ResearchQuestionTemplate],
        *,
        security_archetype: str,
    ) -> list[ResearchQuestionTemplate]:
        result = list(templates)
        seen = {item.question_key for item in result}
        cursor = 0
        while cursor < len(result):
            item = result[cursor]
            cursor += 1
            for dependency in item.dependencies:
                if not dependency.material or dependency.question_key in seen:
                    continue
                target = self.library.get(dependency.question_key)
                if target is not None and _applies_to_archetype(
                    target, security_archetype
                ):
                    result.append(target)
                    seen.add(target.question_key)
        return result

    def _existing_items(
        self,
        *,
        ticker: str,
        existing_questions: Iterable[Any],
        existing_keys: set[str],
        existing_intent_ids: set[str],
        resolver: ResearchIntentResolver,
        cik: str,
        exchange: str,
    ) -> list[ResearchAgendaItem]:
        rows: list[ResearchAgendaItem] = []
        for index, value in enumerate(existing_questions):
            objective = str(
                getattr(value, "question", "")
                or getattr(value, "objective", "")
                or (value.get("question") if isinstance(value, dict) else "")
                or (value.get("objective") if isinstance(value, dict) else "")
                or ""
            ).strip()
            if not objective:
                continue
            matched = self.library.matching(objective)
            question_key = matched[0].question_key if matched else ""
            mapped = resolver.resolve_legacy(
                ticker=ticker,
                cik=cik,
                exchange=exchange,
                question_key=question_key,
                title=objective,
                objective=objective,
                origin_refs=(
                    str(getattr(value, "question_id", "") or f"legacy:{index}"),
                ),
            )
            research_intent = mapped.intent
            if research_intent is None:
                # Preserve genuinely new wording for coverage, but do not mint
                # a canonical identity from prose alone.
                question_key = f"existing_{_slug(objective)[:60]}"
            else:
                research_intent = resolver.upsert(research_intent).intent
                question_key = research_intent.question_key
            if question_key in existing_keys or (
                research_intent is not None
                and research_intent.intent_id in existing_intent_ids
            ):
                continue
            existing_keys.add(question_key)
            if research_intent is not None:
                existing_intent_ids.add(research_intent.intent_id)
            rows.append(ResearchAgendaItem(
                question_id=_stable_id("existing-research-question", ticker, objective),
                question_key=question_key,
                domain="Existing research questions",
                title=objective,
                objective=objective,
                decision_relevance="Preserved from the accumulated application research workflow.",
                priority=max(55, 70 - index),
                selection_reason="Existing application-generated question retained.",
                intent_id=(research_intent.intent_id if research_intent else ""),
                mechanism_key=(research_intent.mechanism_key if research_intent else ""),
                target_type=(research_intent.target_type if research_intent else "issuer"),
                target_id=(research_intent.target_id if research_intent else ticker.upper()),
                period_scope=(research_intent.period_scope if research_intent else "current_research_cutoff"),
                decision_dependency=(research_intent.decision_dependency if research_intent else "decision_quality"),
            ))
        return rows

    def _model_items(
        self,
        *,
        ticker: str,
        additions: Iterable[dict[str, Any]],
        existing_keys: set[str],
        existing_intent_ids: set[str],
        resolver: ResearchIntentResolver,
        cik: str,
        exchange: str,
    ) -> list[ResearchAgendaItem]:
        rows: list[ResearchAgendaItem] = []
        for index, value in enumerate(additions):
            objective = str(value.get("objective") or value.get("question") or "").strip()
            if not objective:
                continue
            supplied_key = str(value.get("question_key") or "")
            matched = self.library.matching(objective)
            question_key = supplied_key or (
                matched[0].question_key if matched else ""
            )
            mapped = resolver.resolve_legacy(
                ticker=ticker,
                cik=cik,
                exchange=exchange,
                question_key=question_key,
                mechanism_key=str(value.get("mechanism_key") or ""),
                target_type=str(value.get("target_type") or ""),
                target_id=str(value.get("target_id") or ""),
                period_scope=str(value.get("period_scope") or ""),
                event_id=str(value.get("event_id") or ""),
                decision_dependency=str(value.get("decision_dependency") or ""),
                title=str(value.get("title") or objective),
                objective=objective,
                materiality=str(value.get("materiality") or "High"),
                origin_refs=(str(value.get("question_id") or f"model:{index}"),),
            )
            research_intent = mapped.intent
            if research_intent is None:
                question_key = f"model_{_slug(objective)[:60]}"
            else:
                research_intent = resolver.upsert(research_intent).intent
                question_key = research_intent.question_key
            if question_key in existing_keys or (
                research_intent is not None
                and research_intent.intent_id in existing_intent_ids
            ):
                continue
            existing_keys.add(question_key)
            if research_intent is not None:
                existing_intent_ids.add(research_intent.intent_id)
            rows.append(ResearchAgendaItem(
                question_id=str(value.get("question_id") or _stable_id(
                    "model-research-question", ticker, objective
                )),
                question_key=question_key,
                domain=str(value.get("domain") or "Model-added research"),
                title=str(value.get("title") or objective),
                objective=objective,
                decision_relevance=str(
                    value.get("decision_relevance")
                    or "The research model identified a material coverage gap."
                ),
                materiality=str(value.get("materiality") or "High"),
                priority=_safe_priority(
                    value.get("priority"), max(60, 75 - index)
                ),
                source_families=[str(item) for item in value.get("source_families", [])],
                analytical_tools=[str(item) for item in value.get("analytical_tools", [])],
                confirming_evidence=[str(item) for item in value.get("confirming_evidence", [])],
                contradicting_evidence=[str(item) for item in value.get("contradicting_evidence", [])],
                selection_reason="Added by the research model with a recorded materiality assessment.",
                intent_id=(research_intent.intent_id if research_intent else ""),
                mechanism_key=(research_intent.mechanism_key if research_intent else ""),
                target_type=(research_intent.target_type if research_intent else "issuer"),
                target_id=(research_intent.target_id if research_intent else ticker.upper()),
                period_scope=(research_intent.period_scope if research_intent else "current_research_cutoff"),
                event_id=(research_intent.event_id if research_intent else ""),
                decision_dependency=(research_intent.decision_dependency if research_intent else "decision_quality"),
            ))
        return rows


def agenda_from_payload(payload: dict[str, Any]) -> ResearchAgenda:
    values = dict(payload)
    items = []
    for raw in values.pop("items", []) or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        coverage_payload = item.pop("coverage", {}) or {}
        answer_payload = item.get("answer")
        if isinstance(answer_payload, dict):
            from ..domain.agent_research import ResearchAnswer

            item["answer"] = ResearchAnswer(**answer_payload)
        items.append(ResearchAgendaItem(
            **item,
            coverage=EvidenceCoveragePlan(**coverage_payload),
        ))
    return ResearchAgenda(**values, items=items)


def _priority_for(
    template: ResearchQuestionTemplate,
    *,
    focused: bool,
    rank: int,
) -> int:
    materiality = template.default_materiality.casefold()
    base = {"critical": 90, "high": 78, "medium": 60, "low": 40}.get(materiality, 55)
    if focused and rank == 0:
        return 100
    if focused:
        return max(55, base - 10)
    return base


def _applies_to_archetype(
    template: ResearchQuestionTemplate,
    security_archetype: str,
) -> bool:
    archetypes = {
        _slug(value) for value in template.applicable_archetypes if value
    }
    return bool(
        not archetypes
        or "all" in archetypes
        or _slug(security_archetype) in archetypes
    )


def _stable_id(*parts: Any) -> str:
    return hashlib.sha256(
        "|".join(str(item) for item in parts).encode("utf-8")
    ).hexdigest()[:24]


def _slug(value: str) -> str:
    return "_".join(
        token for token in "".join(
            character if character.isalnum() else " " for character in value.casefold()
        ).split()
        if token
    )


def _safe_priority(value: Any, default: int) -> int:
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
