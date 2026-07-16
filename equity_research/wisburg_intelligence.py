from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from datetime import datetime, timezone

from .models import (
    Citation,
    ClaimValidationResult,
    CompanyIdentity,
    FinancialMetric,
    ManagementSourcePackage,
    ResearchSourcePlan,
    ResearchSourceRequest,
    SourceCorroborationResult,
    WisburgCorroborationDecision,
    WisburgReportRecord,
    WisburgResearchLens,
    WisburgResearchTask,
    WisburgRevisionObservation,
    WisburgStructuredClaim,
)


METRIC_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Customer Management Revenue", ("cmr", "customer management revenue", "客户管理收入")),
    ("Cloud Revenue", ("cloud revenue", "云业务收入", "云收入")),
    ("Revenue", ("revenue", "营收", "营业收入", "收入")),
    ("Gross Margin", ("gross margin", "毛利率")),
    ("Operating Margin", ("operating margin", "经营利润率", "营业利润率")),
    ("EBITA", ("ebita",)),
    ("Net Income", ("net income", "净利润")),
    ("Free Cash Flow", ("free cash flow", "自由现金流")),
    ("Capital Expenditure", ("capex", "capital expenditure", "资本开支", "资本支出")),
    ("GMV", ("gmv", "商品交易总额")),
    ("Monthly Active Users", ("mau", "月活跃用户")),
    ("Target Price", ("target price", "price target", "目标价")),
    ("Earnings Per Share", ("eps", "每股收益")),
)

DRIVER_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Cloud", ("cloud", "云业务", "云智能", "maas", "ai")),
    ("China commerce", ("cmr", "taobao", "tmall", "淘宝", "天猫", "电商", "commerce")),
    ("International commerce", ("aidc", "international commerce", "国际商业", "aliexpress", "lazada")),
    ("Local services", ("instant retail", "local services", "即时零售", "闪购", "本地生活")),
    ("Margin / mix", ("margin", "ebita", "利润率", "盈利", "gross profit")),
    ("Capital allocation", ("buyback", "repurchase", "dividend", "回购", "股东回报")),
    ("Demand", ("demand", "gmv", "retail sales", "需求", "零售销售")),
    ("Regulation", ("regulation", "policy", "监管", "政策")),
    ("Valuation", ("valuation", "multiple", "sotp", "估值", "市盈率", "市销率")),
)

PUBLISHERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Citi", ("citi", "citigroup", "花旗")),
    ("Goldman Sachs", ("goldman", "高盛")),
    ("Morgan Stanley", ("morgan stanley", "摩根士丹利", "大摩")),
    ("J.P. Morgan", ("j.p. morgan", "jp morgan", "摩根大通")),
    ("UBS", ("ubs", "瑞银")),
    ("Bank of America", ("bank of america", "美银")),
    ("HSBC", ("hsbc", "汇丰")),
    ("Nomura", ("nomura", "野村")),
)


def extract_wisburg_report(
    identity: CompanyIdentity,
    *,
    report_id: str,
    category: str,
    title: str,
    published_at: str | None,
    observed_at: str,
    source_tier: int,
    detail_text: str,
    endpoint: str,
) -> tuple[WisburgReportRecord, list[WisburgStructuredClaim], list[WisburgRevisionObservation]]:
    clean = _clean_detail(detail_text)
    language = "zh" if re.search(r"[\u4e00-\u9fff]", clean) else "en"
    report_key = f"{category}:{report_id}"
    sections = [match.group(1).strip()[:120] for match in re.finditer(r"^#{2,4}\s+(.+)$", detail_text, re.MULTILINE)]
    relevant_lines = _candidate_lines(clean)
    excerpt = " ".join(relevant_lines[:5])[:1800] or clean[:1800]
    publisher = _publisher(f"{title} {clean[:4000]}")
    citation = Citation(
        source=f"Wisburg {category} report {report_id}",
        url=endpoint,
        filed=published_at,
        section=f"{report_key}:structured-detail",
        snippet=excerpt[:700],
        retrieved_at=observed_at,
        source_tier=source_tier,
    )
    report = WisburgReportRecord(
        report_key=report_key,
        ticker=identity.ticker.upper(),
        report_id=str(report_id),
        category=category,
        title=title[:300],
        published_at=published_at,
        observed_at=observed_at,
        source_language=language,
        source_tier=source_tier,
        publisher=publisher,
        detail_status="structured_extract_available" if clean else "detail_empty",
        content_scope="capped_structured_extract" if clean else "metadata_excerpt_only",
        sections_found=sections[:20],
        capped_excerpt=excerpt,
        citation=citation,
        content_fingerprint=hashlib.sha256(clean.encode("utf-8")).hexdigest()[:20] if clean else "",
    )
    claims = _extract_claims(identity, report, relevant_lines)
    revisions = _extract_revisions(identity, report, relevant_lines)
    return report, claims, revisions


def listing_only_report(
    identity: CompanyIdentity,
    *,
    report_id: str,
    category: str,
    title: str,
    published_at: str | None,
    observed_at: str,
    source_tier: int,
    excerpt: str,
    citation: Citation | None,
) -> WisburgReportRecord:
    return WisburgReportRecord(
        report_key=f"{category}:{report_id}",
        ticker=identity.ticker.upper(),
        report_id=report_id,
        category=category,
        title=title[:300],
        published_at=published_at,
        observed_at=observed_at,
        source_language="zh" if re.search(r"[\u4e00-\u9fff]", f"{title} {excerpt}") else "en",
        source_tier=source_tier,
        detail_status="listing_only",
        content_scope="metadata_excerpt_only",
        capped_excerpt=excerpt[:1400],
        citation=citation,
    )


def corroborate_wisburg_lens(
    lens: WisburgResearchLens,
    metrics: list[FinancialMetric],
    validated_claims: ClaimValidationResult,
    management_sources: ManagementSourcePackage,
) -> WisburgResearchLens:
    metric_rows = list(metrics or [])
    primary_claims = list(validated_claims.claims or [])
    management_claims = list(getattr(management_sources, "claims", []) or [])
    decisions: list[WisburgCorroborationDecision] = []
    tasks: list[WisburgResearchTask] = []
    observed_at = _now()

    for claim in lens.structured_claims:
        terms = _claim_terms(claim)
        metric_context_matches = [
            row for row in metric_rows if _metric_matches_claim(claim, row)
        ]
        metric_matches = [
            row for row in metric_context_matches
            if _periods_compatible(claim.fiscal_period, row.fiscal_period or row.period_end)
        ]
        primary_matches = [
            row for row in primary_claims
            if row.is_substantive and _validated_claim_matches(claim, row)
        ]
        management_matches = [
            row for row in management_claims
            if _management_claim_matches(claim, row, terms)
        ]
        matched_ids = [f"metric:{row.name}:{row.period_end}" for row in metric_matches[:4]]
        matched_ids.extend(f"claim:{row.claim_id}" for row in primary_matches[:4])
        matched_ids.extend(f"management:{getattr(row, 'claim_id', index)}" for index, row in enumerate(management_matches[:4]))
        opposite = [
            row for row in primary_matches
            if claim.direction in {"positive", "negative"}
            and getattr(row, "direction", "neutral") in {"positive", "negative"}
            and getattr(row, "direction", "neutral") != claim.direction
        ]

        if opposite:
            status = "Contradicted by primary evidence"
            explanation = "A citation-bound issuer/filing claim points in the opposite direction."
            contradictory_ids = [f"claim:{row.claim_id}" for row in opposite]
        elif claim.claim_type in {"target", "rating"}:
            status = "External opinion; primary check not applicable"
            explanation = "Targets and ratings are analyst opinions, not issuer facts or official consensus snapshots."
            contradictory_ids = []
        elif claim.claim_type == "estimate" and (matched_ids or metric_context_matches):
            matched_ids.extend(
                f"metric_context:{row.name}:{row.period_end}"
                for row in metric_context_matches[:4]
                if f"metric:{row.name}:{row.period_end}" not in matched_ids
            )
            status = "Underlying driver corroborated; forecast unverified"
            explanation = "Primary evidence supports the driver context, but it cannot verify a forward analyst estimate."
            contradictory_ids = []
        elif matched_ids:
            status = "Primary context corroborated"
            explanation = "A period-aware filing fact, validated issuer claim, or management claim checks the same driver."
            contradictory_ids = []
        else:
            status = "Primary corroboration missing"
            explanation = "No normalized primary evidence in this run directly confirms or disproves the external claim."
            contradictory_ids = []

        claim.corroboration_status = status
        claim.primary_evidence_ids = matched_ids
        claim.corroboration_explanation = explanation
        decision = WisburgCorroborationDecision(
            claim_id=claim.claim_id,
            status=status,
            explanation=explanation,
            matched_primary_evidence_ids=matched_ids,
            contradictory_evidence_ids=contradictory_ids,
            required_primary_sources=_required_sources(claim),
            observed_at=observed_at,
        )
        decisions.append(decision)
        if status != "Primary context corroborated":
            tasks.append(_task_for_claim(claim, decision))

    lens.corroboration = decisions
    lens.research_tasks = tasks[:16]
    return lens


def enrich_source_plan_with_wisburg_tasks(
    plan: ResearchSourcePlan,
    lens: WisburgResearchLens,
) -> ResearchSourcePlan:
    existing = {(item.source_type, item.title.lower()) for item in plan.requests}
    for task in lens.research_tasks:
        title = f"Cross-check Wisburg claim: {task.action}"
        key = (task.source_type, title.lower())
        if key in existing:
            continue
        existing.add(key)
        plan.requests.append(ResearchSourceRequest(
            request_id=task.task_id,
            source_type=task.source_type,
            title=title,
            reason_to_inspect=(
                "Wisburg supplied Tier 3/4 external context; deterministic primary evidence must arbitrate it."
            ),
            expected_evidence_type=task.expected_evidence,
            priority=task.priority,
            cost_latency="Automatic registered-source check first; manual/licensed input only if unavailable.",
            confirms_or_disproves=task.confirms_or_disproves,
            status=task.status,
            provider=task.provider,
        ))
    priority = {"High": 0, "Medium": 1, "Low": 2}
    plan.requests = sorted(plan.requests, key=lambda item: (priority.get(item.priority, 1), item.title))[:24]
    return plan


def wisburg_source_corroboration_results(lens: WisburgResearchLens) -> list[SourceCorroborationResult]:
    claim_by_id = {claim.claim_id: claim for claim in lens.structured_claims}
    rows: list[SourceCorroborationResult] = []
    for decision in lens.corroboration:
        claim = claim_by_id.get(decision.claim_id)
        if not claim:
            continue
        rows.append(SourceCorroborationResult(
            result_id=_stable_id("wisburg-corroboration", decision.claim_id, decision.status),
            ticker=claim.ticker,
            claim_id=decision.claim_id,
            status=decision.status,
            driver_family=claim.driver,
            primary_source_status=decision.status,
            explanation=decision.explanation,
            required_sources=decision.required_primary_sources,
            matched_observation_ids=decision.matched_primary_evidence_ids,
            gaps=[] if decision.matched_primary_evidence_ids else decision.required_primary_sources,
            observed_at=decision.observed_at,
        ))
    return rows


def _extract_claims(
    identity: CompanyIdentity,
    report: WisburgReportRecord,
    lines: list[str],
) -> list[WisburgStructuredClaim]:
    claims: list[WisburgStructuredClaim] = []
    seen: set[str] = set()
    for line in lines[:80]:
        claim_type = _claim_type(line)
        if not claim_type:
            continue
        metric = _metric(line)
        driver = _driver(line)
        period = _period(line)
        value, unit, currency = _value(line, metric)
        revision_values = _numbers_without_period(line)
        current_value, previous_value = _revision_values(line, revision_values)
        if _has_revision_language(line):
            value = current_value if current_value is not None else value
        else:
            previous_value = None
        direction = _direction(line)
        normalized = re.sub(r"\s+", " ", line).strip()[:700]
        key = f"{claim_type}|{metric}|{period}|{normalized.lower()}"
        if key in seen:
            continue
        seen.add(key)
        citation = _claim_citation(report, normalized, claim_type)
        claims.append(WisburgStructuredClaim(
            claim_id=_stable_id(report.report_key, key),
            report_key=report.report_key,
            ticker=identity.ticker.upper(),
            claim_type=claim_type,
            statement=normalized,
            driver=driver,
            direction=direction,
            source_as_of=report.published_at,
            source_tier=report.source_tier,
            metric=metric,
            fiscal_period=period,
            value=value,
            previous_value=previous_value,
            unit=unit,
            currency=currency,
            confidence="Medium" if metric or claim_type in {"target", "rating"} else "Low",
            citation=citation,
        ))
        if len(claims) >= 24:
            break
    return claims


def _extract_revisions(
    identity: CompanyIdentity,
    report: WisburgReportRecord,
    lines: list[str],
) -> list[WisburgRevisionObservation]:
    revisions: list[WisburgRevisionObservation] = []
    for line in lines:
        lower = line.lower()
        if not any(token in lower for token in ("target", "目标价", "上调", "下调", "raised", "cut", "adjusted", "调整")):
            continue
        values = _numbers_without_period(line)
        if not values:
            continue
        metric = _metric(line) or ("Target Price" if "target" in lower or "目标价" in lower else "External estimate")
        current, previous = _revision_values(line, values)
        if current is None:
            continue
        change = (current / previous - 1) * 100 if previous not in (None, 0) else None
        direction = "raised" if change is not None and change > 0 else "cut" if change is not None and change < 0 else _direction(line)
        _value_number, unit, currency = _value(line, metric)
        statement = re.sub(r"\s+", " ", line).strip()[:700]
        citation = _claim_citation(report, statement, "revision")
        revisions.append(WisburgRevisionObservation(
            revision_id=_stable_id(report.report_key, metric, statement),
            report_key=report.report_key,
            ticker=identity.ticker.upper(),
            revision_type="target" if metric == "Target Price" else "estimate",
            metric=metric,
            source_as_of=report.published_at,
            direction=direction,
            current_value=current,
            previous_value=previous,
            change_pct=change,
            fiscal_period=_period(line),
            currency=currency,
            unit=unit,
            statement=statement,
            citation=citation,
            confidence="Medium" if previous is not None else "Low",
        ))
        if len(revisions) >= 12:
            break
    return revisions


def _clean_detail(text: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^\)]+\)", " ", text or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"^#{1,6}\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"\*\*|__|`", "", value)
    return "\n".join(line.strip(" -*\t") for line in value.splitlines() if line.strip())


def _candidate_lines(text: str) -> list[str]:
    rows = []
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        lower = clean.lower()
        if len(clean) < 18:
            continue
        if any(token in lower for token in (
            "expect", "forecast", "estimate", "target", "rating", "risk", "pressure", "growth",
            "margin", "revenue", "profit", "预计", "预测", "目标价", "评级", "风险", "承压",
            "增长", "利润率", "收入", "净利润", "下调", "上调", "受益于", "主要受",
        )):
            rows.append(clean[:900])
    return rows


def _claim_type(line: str) -> str | None:
    lower = line.lower()
    if "target price" in lower or "price target" in lower or "目标价" in lower:
        return "target"
    if any(token in lower for token in ("rating", "buy", "sell", "评级", "买入", "卖出")):
        return "rating"
    if any(token in lower for token in ("expect", "forecast", "estimate", "预计", "预测")):
        return "estimate"
    if any(token in lower for token in ("risk", "pressure", "weak", "风险", "承压", "疲软", "下滑")):
        return "risk"
    if any(token in lower for token in ("catalyst", "launch", "approval", "催化", "发布", "获批")):
        return "catalyst"
    if any(token in lower for token in ("growth", "margin", "revenue", "profit", "增长", "利润率", "收入", "盈利")):
        return "thesis_mechanism"
    return None


def _metric(line: str) -> str | None:
    lower = line.lower()
    return next((label for label, tokens in METRIC_PATTERNS if any(token in lower for token in tokens)), None)


def _driver(line: str) -> str:
    lower = line.lower()
    return next((label for label, tokens in DRIVER_PATTERNS if any(token in lower for token in tokens)), "Unmapped")


def _direction(line: str) -> str:
    lower = line.lower()
    positive = sum(lower.count(token) for token in ("growth", "improve", "raised", "upside", "增长", "提升", "上调", "改善", "收窄"))
    negative = sum(lower.count(token) for token in ("decline", "pressure", "cut", "weak", "risk", "下滑", "承压", "下调", "疲软", "风险", "亏损"))
    if positive and negative:
        return "mixed"
    return "positive" if positive else "negative" if negative else "neutral"


def _period(line: str) -> str | None:
    match = re.search(r"\b(FY\s?\d{1,2}Q\d{1,2}|FY\s?\d{2,4}|\d{4}Q[1-4]|Q[1-4]\s?\d{2,4})\b", line, re.IGNORECASE)
    return match.group(1).replace(" ", "").upper() if match else None


def _value(line: str, metric: str | None) -> tuple[float | None, str | None, str | None]:
    lower = line.lower()
    currency = "CNY" if any(token in lower for token in ("rmb", "cny", "人民币", "元")) else "HKD" if any(token in lower for token in ("hkd", "港元")) else "USD" if any(token in lower for token in ("usd", "美元")) else None
    unit = "%" if "%" in line else "CNY 100m" if "亿元" in line else currency
    numbers = _numbers_without_period(line)
    if not numbers:
        return None, unit, currency
    revised, _previous = _revision_values(line, numbers)
    value = revised if revised is not None and _has_revision_language(line) else numbers[-1] if metric == "Target Price" else numbers[0]
    return value, unit, currency


def _revision_values(line: str, values: list[float]) -> tuple[float | None, float | None]:
    lower = line.lower()
    if len(values) < 2:
        return (values[-1], None) if values else (None, None)
    number = r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"
    from_to = re.search(
        rf"from\s+(?:[a-z]{{3}}\s*)?{number}.{{0,80}}?to\s+(?:[a-z]{{3}}\s*)?{number}",
        lower,
    )
    if from_to:
        return _float(from_to.group(2)), _float(from_to.group(1))
    to_from = re.search(
        rf"to\s+(?:[a-z]{{3}}\s*)?{number}.{{0,80}}?from\s+(?:[a-z]{{3}}\s*)?{number}",
        lower,
    )
    if to_from:
        return _float(to_from.group(1)), _float(to_from.group(2))
    chinese_from_to = re.search(rf"从\s*{number}.{{0,80}}?[至到为]\s*{number}", line)
    if chinese_from_to:
        return _float(chinese_from_to.group(2)), _float(chinese_from_to.group(1))
    if any(token in lower for token in ("down to", "up to", "下调至", "上调至", "调整为")):
        return values[-1], values[-2]
    return values[-1], None


def _numbers_without_period(line: str) -> list[float]:
    scrubbed = re.sub(
        r"\b(?:FY\s?\d{1,4}(?:Q[1-4])?|\d{4}Q[1-4]|Q[1-4]\s?\d{2,4})\b",
        " ",
        line,
        flags=re.IGNORECASE,
    )
    return [
        _float(value)
        for value in re.findall(
            r"(?<![a-zA-Z])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)",
            scrubbed,
        )
    ]


def _has_revision_language(line: str) -> bool:
    lower = line.lower()
    return any(token in lower for token in (
        "raised", "cut", "adjusted", "from", "down to", "up to",
        "上调", "下调", "调整", "从",
    ))


def _float(value: str) -> float:
    return float(value.replace(",", ""))


def _publisher(text: str) -> str:
    lower = text.lower()
    return next((name for name, tokens in PUBLISHERS if any(token in lower for token in tokens)), "Unknown")


def _claim_citation(report: WisburgReportRecord, statement: str, claim_type: str) -> Citation:
    source = report.citation.source if report.citation else f"Wisburg report {report.report_id}"
    url = report.citation.url if report.citation else "https://mcp.wisburg.com/mcp"
    return Citation(
        source=source,
        url=url,
        filed=report.published_at,
        section=f"{report.report_key}:{claim_type}",
        snippet=statement[:700],
        retrieved_at=report.observed_at,
        source_tier=report.source_tier,
    )


def _claim_terms(claim: WisburgStructuredClaim) -> set[str]:
    text = " ".join(filter(None, [claim.metric, claim.driver, claim.statement])).lower()
    return {token for token in re.findall(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text) if token not in {"external", "report", "analyst"}}


def _overlap(terms: set[str], text: str) -> bool:
    lower = (text or "").lower()
    return bool(terms and any(term in lower for term in terms))


def _metric_matches_claim(claim: WisburgStructuredClaim, metric: FinancialMetric) -> bool:
    if not claim.metric:
        return False
    return _canonical(claim.metric) == _canonical(metric.name)


def _validated_claim_matches(claim: WisburgStructuredClaim, primary: object) -> bool:
    primary_metric = str(getattr(primary, "metric", "") or "")
    primary_driver = str(getattr(primary, "business_driver", "") or "")
    metric_match = bool(claim.metric and primary_metric and _canonical(claim.metric) == _canonical(primary_metric))
    driver_match = bool(
        claim.driver not in {"", "Unmapped"}
        and primary_driver not in {"", "Unmapped"}
        and _canonical(claim.driver) == _canonical(primary_driver)
    )
    if not (metric_match or driver_match):
        return False
    return _periods_compatible(claim.fiscal_period, str(getattr(primary, "period", "") or ""))


def _management_claim_matches(
    claim: WisburgStructuredClaim,
    management: object,
    terms: set[str],
) -> bool:
    management_metric = str(getattr(management, "metric", "") or "")
    if claim.metric and management_metric:
        if _canonical(claim.metric) != _canonical(management_metric):
            return False
    elif not _overlap(terms, str(getattr(management, "statement", "") or "")):
        return False
    return _periods_compatible(
        claim.fiscal_period,
        str(getattr(management, "period_end", "") or ""),
    )


def _periods_compatible(external_period: str | None, primary_period: str | None) -> bool:
    if not external_period or not primary_period:
        return True
    external_years = re.findall(r"(?:19|20)\d{2}", external_period)
    primary_years = re.findall(r"(?:19|20)\d{2}", primary_period)
    if external_years and primary_years and external_years[-1] != primary_years[-1]:
        return False
    external_quarters = re.findall(r"Q([1-4])", external_period.upper())
    primary_quarters = re.findall(r"Q([1-4])", primary_period.upper())
    return not (external_quarters and primary_quarters) or external_quarters[-1] == primary_quarters[-1]


def _canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value.lower())


def _required_sources(claim: WisburgStructuredClaim) -> list[str]:
    if claim.claim_type in {"target", "rating"}:
        return ["licensed point-in-time consensus or manual import"]
    if claim.driver in {"Cloud", "China commerce", "International commerce", "Local services", "Margin / mix"}:
        return ["issuer results deck", "earnings call transcript", "SEC/6-K/20-F segment disclosure"]
    if claim.driver == "Regulation":
        return ["official regulator release", "issuer filing"]
    return ["SEC/issuer filing", "issuer presentation or transcript"]


def _task_for_claim(claim: WisburgStructuredClaim, decision: WisburgCorroborationDecision) -> WisburgResearchTask:
    if claim.claim_type in {"target", "rating"}:
        source_type = "consensus_manual"
    elif claim.driver == "Regulation":
        source_type = "regulator_release"
    elif claim.claim_type == "estimate" or claim.driver in {"Cloud", "China commerce", "International commerce", "Local services", "Margin / mix"}:
        source_type = "presentation"
    else:
        source_type = "sec_filing"
    action = f"Verify {claim.metric or claim.driver} claim from {claim.report_key}"
    return WisburgResearchTask(
        task_id=_stable_id("wisburg-task", claim.claim_id, source_type),
        claim_id=claim.claim_id,
        priority="High" if claim.claim_type in {"estimate", "risk"} else "Medium",
        source_type=source_type,
        action=action,
        expected_evidence="; ".join(decision.required_primary_sources),
        confirms_or_disproves=(
            f"Confirms or disproves the Tier {claim.source_tier} external claim: {claim.statement[:260]}"
        ),
    )


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:14]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
