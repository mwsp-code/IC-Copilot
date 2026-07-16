from __future__ import annotations

from dataclasses import asdict

from .models import AlertRecord, WisburgResearchLens, WisburgSnapshotDelta
from .research_store import ResearchStore


def compare_wisburg_lenses(
    current: WisburgResearchLens | dict,
    prior: WisburgResearchLens | dict | None,
) -> WisburgSnapshotDelta:
    current_payload = _payload(current)
    ticker = str(current_payload.get("ticker") or "").upper()
    observed_at = str(current_payload.get("observed_at") or "")
    current_excerpts = current_payload.get("excerpts") or []
    current_themes = current_payload.get("themes") or []
    current_narrative = current_payload.get("narrative_score") or {}
    current_revisions = current_payload.get("revisions") or []
    current_corroboration = current_payload.get("corroboration") or []

    caveats = [
        "Delta covers only the capped Wisburg search results observed by this app.",
        "Absence from a later capped result is not treated as report removal.",
        "Wisburg remains external context; primary or issuer evidence must corroborate thesis claims.",
    ]
    if not prior:
        return WisburgSnapshotDelta(
            ticker=ticker,
            status="Baseline",
            observed_at=observed_at,
            current_narrative_label=current_narrative.get("label"),
            summary=(
                f"Established the first capped Wisburg baseline with {len(current_excerpts)} item(s). "
                "Future snapshots can identify newly observed reports and theme-stance changes."
            ),
            caveats=caveats,
        )

    prior_payload = _payload(prior)
    prior_excerpts = prior_payload.get("excerpts") or []
    prior_themes = prior_payload.get("themes") or []
    prior_narrative = prior_payload.get("narrative_score") or {}
    prior_revisions = prior_payload.get("revisions") or []
    prior_corroboration = prior_payload.get("corroboration") or []
    prior_keys = {_report_key(item) for item in prior_excerpts}
    new_items = [item for item in current_excerpts if _report_key(item) not in prior_keys]

    prior_theme_map = {_theme_key(item): item for item in prior_themes}
    current_theme_map = {_theme_key(item): item for item in current_themes}
    new_themes = [
        str(item.get("label") or item.get("driver") or "Unmapped")
        for key, item in current_theme_map.items()
        if key not in prior_theme_map
    ]
    stance_changes: list[dict[str, str]] = []
    for key, item in current_theme_map.items():
        prior_item = prior_theme_map.get(key)
        if not prior_item:
            continue
        previous = str(prior_item.get("stance") or "unknown")
        current_stance = str(item.get("stance") or "unknown")
        if previous != current_stance:
            stance_changes.append({
                "theme": str(item.get("label") or item.get("driver") or key),
                "from": previous,
                "to": current_stance,
                "driver": str(item.get("driver") or "Unmapped"),
            })

    prior_revision_ids = {
        str(item.get("revision_id") or "") for item in prior_revisions
    }
    new_revisions = [
        item for item in current_revisions
        if str(item.get("revision_id") or "") not in prior_revision_ids
    ]
    prior_corroboration_map = {
        str(item.get("claim_id") or ""): str(item.get("status") or "Unknown")
        for item in prior_corroboration
    }
    corroboration_changes: list[dict[str, str]] = []
    for item in current_corroboration:
        claim_id = str(item.get("claim_id") or "")
        previous = prior_corroboration_map.get(claim_id)
        current_status = str(item.get("status") or "Unknown")
        if previous and previous != current_status:
            corroboration_changes.append({
                "claim_id": claim_id,
                "from": previous,
                "to": current_status,
                "explanation": str(item.get("explanation") or "")[:300],
            })

    prior_count = int(prior_narrative.get("item_count") or len(prior_excerpts))
    current_count = int(current_narrative.get("item_count") or len(current_excerpts))
    changed = bool(new_items or new_themes or stance_changes or new_revisions or corroboration_changes)
    status = "Changed" if changed else "No material change"
    summary = (
        f"Wisburg snapshot found {len(new_items)} newly observed report(s), "
        f"{len(new_themes)} new theme(s), {len(stance_changes)} stance change(s), "
        f"{len(new_revisions)} external revision observation(s), and "
        f"{len(corroboration_changes)} corroboration change(s). "
        "These are research leads, not standalone thesis confirmation."
        if changed else
        "No newly observed report or theme-stance change was found within the capped Wisburg result set."
    )
    return WisburgSnapshotDelta(
        ticker=ticker,
        status=status,
        observed_at=observed_at,
        prior_observed_at=str(prior_payload.get("observed_at") or "") or None,
        new_report_ids=[str(item.get("report_id") or item.get("excerpt_id") or "") for item in new_items],
        new_report_titles=[str(item.get("title") or "Untitled")[:220] for item in new_items],
        new_themes=new_themes,
        theme_stance_changes=stance_changes,
        prior_narrative_label=prior_narrative.get("label"),
        current_narrative_label=current_narrative.get("label"),
        item_count_change=current_count - prior_count,
        new_revision_ids=[str(item.get("revision_id") or "") for item in new_revisions],
        new_revision_summaries=[
            str(item.get("statement") or item.get("metric") or "External revision")[:300]
            for item in new_revisions
        ],
        corroboration_changes=corroboration_changes,
        summary=summary,
        caveats=caveats,
    )


def generate_wisburg_alerts(
    delta: WisburgSnapshotDelta,
    store: ResearchStore,
) -> list[AlertRecord]:
    if delta.status in {"Baseline", "No material change"}:
        return []
    queued: list[dict] = []
    for report_id, title in zip(delta.new_report_ids, delta.new_report_titles):
        queued.append({
            "ticker": delta.ticker,
            "alert_type": "wisburg_new_research",
            "title": f"New external research context: {title}"[:240],
            "message": (
                "Wisburg surfaced a newly observed external research item. Treat it as a lead and "
                "check registered SEC, issuer, transcript, valuation, price, or consensus sources before acting."
            ),
            "severity": 2,
            "dedupe_key": f"{delta.ticker}:wisburg_new_research:{report_id}",
            "fiscal_period": None,
        })
    for change in delta.theme_stance_changes:
        theme = change.get("theme", "External debate")
        queued.append({
            "ticker": delta.ticker,
            "alert_type": "wisburg_theme_stance_change",
            "title": f"External debate shifted: {theme}"[:240],
            "message": (
                f"The capped Wisburg lens moved from {change.get('from')} to {change.get('to')} for {theme}. "
                "This is narrative context and requires deterministic corroboration."
            ),
            "severity": 3,
            "dedupe_key": (
                f"{delta.ticker}:wisburg_theme_stance_change:"
                f"{theme.lower().replace(' ', '_')}:{change.get('to')}"
            ),
            "fiscal_period": None,
        })
    for revision_id, summary in zip(delta.new_revision_ids, delta.new_revision_summaries):
        queued.append({
            "ticker": delta.ticker,
            "alert_type": "wisburg_external_revision",
            "title": "New external analyst revision context",
            "message": (
                f"Wisburg surfaced: {summary} This is Tier 3/4 external research and not an "
                "official point-in-time consensus revision. Verify it against registered primary "
                "sources or licensed/manual consensus before using a market-capture claim."
            ),
            "severity": 2,
            "dedupe_key": f"{delta.ticker}:wisburg_external_revision:{revision_id}",
            "fiscal_period": None,
        })
    for change in delta.corroboration_changes:
        contradicted = change.get("to") == "Contradicted by primary evidence"
        queued.append({
            "ticker": delta.ticker,
            "alert_type": "wisburg_corroboration_change",
            "title": "External research cross-check changed",
            "message": (
                f"Wisburg claim {change.get('claim_id')} changed from {change.get('from')} to "
                f"{change.get('to')}. {change.get('explanation', '')}"
            )[:700],
            "severity": 3 if contradicted else 2,
            "dedupe_key": (
                f"{delta.ticker}:wisburg_corroboration_change:"
                f"{change.get('claim_id')}:{change.get('to')}"
            ),
            "fiscal_period": None,
        })
    if delta.prior_narrative_label != "Crowded" and delta.current_narrative_label == "Crowded":
        queued.append({
            "ticker": delta.ticker,
            "alert_type": "wisburg_narrative_crowding",
            "title": "External narrative became crowded",
            "message": (
                "The capped Wisburg item count crossed the app's crowding threshold. This may reduce novelty, "
                "but it does not prove the thesis is priced in."
            ),
            "severity": 2,
            "dedupe_key": f"{delta.ticker}:wisburg_narrative_crowding:crowded",
            "fiscal_period": None,
        })
    return store.create_alerts(queued)


def _payload(value: WisburgResearchLens | dict) -> dict:
    return asdict(value) if isinstance(value, WisburgResearchLens) else dict(value)


def _report_key(item: dict) -> str:
    category = str(item.get("category") or "unknown")
    report_id = str(item.get("report_id") or "")
    if report_id:
        return f"{category}:{report_id}"
    return f"{category}:{str(item.get('title') or '').strip().lower()}"


def _theme_key(item: dict) -> str:
    driver = str(item.get("driver") or "").strip().lower()
    label = str(item.get("label") or "").strip().lower()
    return f"{driver}:{label}"
