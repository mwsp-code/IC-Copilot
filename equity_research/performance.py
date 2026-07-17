from __future__ import annotations

from time import perf_counter

from .models import ProfilingReport, ProfilingStep


class ResearchProfiler:
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._start: float | None = None
        self._last: float | None = None
        self._steps: list[ProfilingStep] = []

    def start(self) -> None:
        if not self.enabled:
            return
        now = perf_counter()
        self._start = now
        self._last = now
        self._steps = []

    def checkpoint(self, name: str) -> None:
        if not self.enabled:
            return
        now = perf_counter()
        if self._start is None or self._last is None:
            self.start()
            return
        self._steps.append(ProfilingStep(
            name=name,
            duration_ms=(now - self._last) * 1000,
            started_at_ms=(self._last - self._start) * 1000,
            ended_at_ms=(now - self._start) * 1000,
        ))
        self._last = now

    def finish(self) -> ProfilingReport:
        if not self.enabled:
            return ProfilingReport(
                "Disabled",
                notes=["Research profiling was not enabled for this run."],
            )
        now = perf_counter()
        if self._start is None:
            return ProfilingReport("Unavailable", notes=["Profiler was enabled but never started."])
        if self._last is not None and now > self._last:
            self.checkpoint("unmeasured_tail")
        total_ms = (now - self._start) * 1000
        ordered = sorted(self._steps, key=lambda step: step.duration_ms, reverse=True)
        bottlenecks = [
            f"{step.name}: {step.duration_ms:.1f} ms ({step.duration_ms / total_ms * 100:.1f}% of run)"
            for step in ordered[:8]
            if total_ms > 0
        ]
        treatments = [
            _treatment_for_step(step.name)
            for step in ordered[:8]
            if total_ms > 0
        ]
        return ProfilingReport(
            "Available",
            total_ms=total_ms,
            steps=list(self._steps),
            bottlenecks=bottlenecks,
            treatments=_dedupe(treatments),
            notes=[
                "Stage timings preserve the full research workflow; they do not disable source collection or thesis gates."
            ],
        )


def _treatment_for_step(name: str) -> str:
    normalized = name.lower()
    if "entity" in normalized or "filing" in normalized:
        return "SEC/filings: parsed sections and historical pair summaries are cached by accession/parser version; remaining cold latency is document download and first-pass comparison."
    if "financial" in normalized or "facts" in normalized:
        return "XBRL/companyfacts: cache companyfacts by CIK/date, precompute canonical metric aliases, and derive common metrics once per period."
    if "price" in normalized:
        return "Prices: cache daily bars and event windows by ticker/provider/date; classify blocked providers once per run to avoid retry storms."
    if "consensus" in normalized or "expectation" in normalized:
        return "Consensus: prefer local point-in-time snapshots, cache daily provider observations, and compute minimum viable capture before full 7/30/90 windows."
    if "management" in normalized or "transcript" in normalized:
        return "Management sources: independent adapters run concurrently and missing live artifacts reuse normalized cached documents with original dates/source tiers preserved."
    if "source_plan" in normalized or "external" in normalized or "wisburg" in normalized:
        return "External evidence: independent providers run with bounded concurrency; same-day macro caches and capped external excerpts remain source-labelled and non-blocking."
    if "peer" in normalized or "attribution" in normalized:
        return "Peer/attribution: independent peers run concurrently, SEC/price caches are shared safely, and output is restored to curated peer order before analysis."
    if "thesis_synthesis" in normalized or "llm" in normalized:
        return "LLM: configured-secret redaction is snapshotted once per prompt pack, prompt hashes are cached, excerpts are capped, and timeout falls back to the deterministic brief."
    if "memo" in normalized or "render" in normalized:
        return "UI/memo: cache memo markdown by run hash, render summaries first, and lazy-load raw detail tables."
    if "persistence" in normalized:
        return "Persistence: unchanged normalized management evidence is content-addressed, historical analogs use compact projections, and every changed artifact still commits through durable SQLite tables."
    if "evidence" in normalized or "decision" in normalized or "calibration" in normalized:
        return "Evidence/decision models: historical analog matching reads only normalized comparison fields and resolved outcomes; evidence gates, no-lookahead rules, and calibration thresholds remain unchanged."
    return "General: inspect this stage before optimizing; do not skip evidence gates or accept stale/missing data for speed."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows
