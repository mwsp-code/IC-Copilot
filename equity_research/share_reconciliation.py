from __future__ import annotations

import re

from .adr_profiles import adr_profile_for
from .models import ChangeEvent, Citation, CompanyIdentity, ShareReconciliation


SHARE_RECONCILIATION_PATTERN = re.compile(
    r"(?P<label>ordinary shares|ads|adss|american depositary shares|weighted average shares|"
    r"weighted-average shares|shares outstanding|repurchase|repurchased|buyback|split|share subdivision)"
    r"[^0-9]{0,80}(?P<value>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<scale>million|billion|thousand|m|bn)?",
    re.IGNORECASE,
)
REVERSE_SHARE_RECONCILIATION_PATTERN = re.compile(
    r"(?P<value>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<scale>million|billion|thousand|m|bn)?\s+"
    r"(?P<label>ordinary shares|ads|adss|american depositary shares|weighted average shares|"
    r"weighted-average shares|shares outstanding)",
    re.IGNORECASE,
)
BUYBACK_AMOUNT_PATTERN = re.compile(
    r"(?:repurchase|repurchased|buyback)[^0-9]{0,80}(?:US\$|\$)?\s*"
    r"(?P<value>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<scale>million|billion|thousand|m|bn)?",
    re.IGNORECASE,
)


def reconcile_share_event(identity: CompanyIdentity, event: ChangeEvent) -> ShareReconciliation | None:
    metric_name = str(event.metrics.get("metric_name") or event.title)
    if "share" not in metric_name.lower():
        return None
    profile = adr_profile_for(identity.ticker)
    citations = list(event.citations)
    text = " ".join(
        part for part in [
            event.summary,
            str(event.metrics.get("changed_text") or ""),
            str(event.metrics.get("supporting_quote") or ""),
            " ".join(citation.snippet or "" for citation in citations),
        ]
        if part
    )
    parsed = extract_share_reconciliation_text(
        identity.ticker,
        text,
        citations[0] if citations else None,
        adr_ratio=profile.ordinary_share_ratio if profile else None,
    )
    large_move = bool(event.metrics.get("normalization_required"))
    is_adr = profile is not None
    if parsed.status == "Reconciled" and not large_move:
        return parsed
    gaps = list(parsed.data_gaps)
    if is_adr and parsed.adr_ratio and not any("ADR ratio" in gap for gap in gaps):
        gaps.append(f"Verify the {parsed.adr_ratio:g}:1 ordinary-share-to-ADS ratio against the latest 20-F or depositary terms.")
    if large_move:
        gaps.append(
            "Share-count move exceeds the app threshold; verify ordinary vs ADS basis, weighted average vs period-end shares, split/corporate action, and XBRL concept."
        )
    if is_adr and parsed.status != "Reconciled":
        gaps.append("ADR/FPI share-count signal cannot support dilution or buyback until a share reconciliation is found.")
    status = "Needs normalization" if (large_move or is_adr) else parsed.status
    return ShareReconciliation(
        status=status,
        basis=parsed.basis,
        adr_ratio=parsed.adr_ratio,
        period=str(event.metrics.get("period_end") or event.event_date or "") or parsed.period,
        ordinary_share_count=parsed.ordinary_share_count,
        ads_share_count=parsed.ads_share_count,
        weighted_average_shares=parsed.weighted_average_shares,
        period_end_shares=parsed.period_end_shares,
        buyback_amount=parsed.buyback_amount,
        split_or_corporate_action=parsed.split_or_corporate_action,
        xbrl_concept_consistent=False if large_move else parsed.xbrl_concept_consistent,
        source=parsed.source or "event_citation",
        citations=citations,
        data_gaps=_dedupe(gaps),
    )


def extract_share_reconciliation_text(
    ticker: str,
    text: str,
    citation: Citation | None = None,
    *,
    adr_ratio: float | None = None,
) -> ShareReconciliation:
    ordinary = ads = weighted = period_end = buyback = None
    split = bool(re.search(r"\b(split|share subdivision|corporate action)\b", text or "", re.IGNORECASE))
    basis = "Unknown"
    for match in list(SHARE_RECONCILIATION_PATTERN.finditer(text or "")) + list(REVERSE_SHARE_RECONCILIATION_PATTERN.finditer(text or "")):
        label = match.group("label").lower()
        value = _scaled_value(match.group("value"), match.group("scale"))
        if value is None:
            continue
        if "ordinary" in label:
            ordinary = value
            basis = "ordinary shares"
        elif "ads" in label or "american depositary" in label:
            ads = value
            basis = "ADS"
        elif "weighted" in label:
            weighted = value
        elif "outstanding" in label:
            period_end = value
        elif "repurchase" in label or "buyback" in label:
            buyback = value
        elif "split" in label or "subdivision" in label:
            split = True
    if buyback is None:
        match = BUYBACK_AMOUNT_PATTERN.search(text or "")
        if match:
            buyback = _scaled_value(match.group("value"), match.group("scale"))
    if ordinary and ads:
        basis = "ordinary_vs_ads"
    reconciled = bool((ordinary or ads or weighted or period_end) and (adr_ratio or ordinary or ads))
    gaps: list[str] = []
    if not reconciled:
        gaps.append("No explicit ordinary-share, ADS, weighted-average, or period-end share reconciliation was found in the supplied text.")
    if adr_ratio and not (ordinary and ads):
        gaps.append("ADR ratio is known, but ordinary-share and ADS counts were not both found in the same source.")
    return ShareReconciliation(
        status="Reconciled" if reconciled else "Unavailable",
        basis=basis,
        adr_ratio=adr_ratio,
        ordinary_share_count=ordinary,
        ads_share_count=ads,
        weighted_average_shares=weighted,
        period_end_shares=period_end,
        buyback_amount=buyback,
        split_or_corporate_action=split,
        xbrl_concept_consistent=reconciled,
        source=f"share_reconciliation:{ticker.upper()}",
        citations=[citation] if citation else [],
        data_gaps=gaps,
    )


def _scaled_value(value: str | None, scale: str | None) -> float | None:
    if not value:
        return None
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return None
    label = (scale or "").lower()
    if label in {"billion", "bn"}:
        return number * 1_000_000_000
    if label in {"million", "m"}:
        return number * 1_000_000
    if label == "thousand":
        return number * 1_000
    return number


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
