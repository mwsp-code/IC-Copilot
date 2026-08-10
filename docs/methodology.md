# Research Methodology

## North star

The product answers five questions:

1. What exactly changed?
2. Why could it matter economically?
3. What evidence confirms or contradicts the mechanism?
4. What does the current price appear to assume?
5. What should be monitored next?

## Source tiers

- **Tier 1:** SEC, issuer filings/releases, regulators, courts, and official company facts.
- **Tier 2:** earnings calls and official market or economic data.
- **Tier 3:** licensed consensus, identifiable institutional research, and reputable reporting.
- **Tier 4:** unofficial aggregators, narrative saturation, and inferred context.

Tier 4 context and LLM output never independently promote an idea. A constrained two-source Tier 3 exception may replace one unavailable Tier 1 gate only when independence, period alignment, corroborating quantitative evidence, and every other promotion gate pass.

## Causal thesis graph

Every thesis is represented as:

`Source event -> business driver -> operating KPI -> earnings/FCF -> valuation -> catalyst`

Each connection has its own evidence, confidence, falsification test, and next action. Weak links remain explicit.

## Idea stages

- **Research Question:** an anomaly or unresolved claim worth investigating.
- **Candidate:** a source-linked hypothesis with direction still provisional or material gaps outstanding.
- **Research-Ready:** a mapped driver, current price, counter-thesis, monitors, and auditable payoff assumptions.
- **High-Conviction:** primary or qualifying secondary-supported evidence, a complete causal bridge, no critical contradiction, and every strict gate passed.

Idea Quality Score measures research completeness, not success probability.

## Neutral-first policy

Ambiguous metrics do not receive automatic polarity. Goodwill is treated as acquisition accounting; capex as an investment-cycle signal; share-count changes require share-basis reconciliation. Constructive and adverse mechanisms are presented side by side until cited evidence supports direction.

## Market capture

Thesis validity and expectation capture are separate:

- Price reaction can support a **Price-only** capture assessment.
- Point-in-time consensus snapshots are required for claims such as `uncaptured`, `not priced in`, or `consensus did not react`.
- Current consensus is never substituted for missing historical consensus.

When historical revisions are unavailable, reverse DCF and implied operating assumptions ask what the market must already believe without pretending to know analyst forecasts.

## Scenario and probability policy

Bull/base/bear scenarios use model-derived or explicit user assumptions. Default probabilities are labelled illustrative and uncalibrated. Probability-based ranking remains disabled until at least 30 eligible resolved outcomes exist in the same signal family and horizon.

## No-lookahead

Historical research may use only filings, prices, consensus snapshots, macro releases, and external observations available on or before the relevant event cutoff. Retrieval time and source publication time are stored separately.

## App outcome memory and historical analogs

These are separate research objects:

- **App Outcome Memory** matches frozen ideas, realized returns, monitor outcomes, and post-mortems created inside IC Copilot.
- **Historical Analog Lab** tests same-company cycles, current-industry cycles, shared economic mechanisms, and broader case studies.

One reputable source may create a `Candidate Analog`. A displayed `Research Analog` requires at least two independent source-origin groups and at least one academic, institutional, quantitative, or contemporaneous source. Syndicated copies count once. Analog outcomes remain qualitative unless traceable financial and price data is attached.

Historical analogs may challenge scenarios, counter-theses, and monitor rules. They cannot independently set direction, fair value, probability, or promotion status.

## Public-result qualification

The app exposes one canonical public result per ticker. A result scoring at
least 85 with no residual qualification is labelled `Publication-quality
research`. A result scoring at least 75 may be labelled `Research completed
with residual diligence` only after deterministic validation and independent
closure pass and every limitation is stated explicitly.

Unsupported claims are removed from the thesis, timing, valuation, confidence,
and verdict before publication and become investigation actions. Partially
supported claims are narrowed to admitted evidence. Analog candidates without
independent provenance may remain useful research leads, but cannot influence
scores, valuation, or trade direction. Missing financial paths are eligible as
qualifications only when no displayed conclusion depends on them.

No score can override fabricated, missing, quarantined, unlicensed,
lookahead-violating, or otherwise inadmissible evidence; ignored material
contradictions; a critical semantic failure; failed sanitization; or absent or
failed closure. Those conditions remain hard blockers.

## News intelligence

The LLM may propose event-, driver-, peer-, and company-specific search terms
and source candidates. This is a discovery lane, not an evidence lane.
Deterministic admission classifies publisher ownership, authority, publication
date, relevance, licensing, syndication, prompt-injection risk, and citation
support before a registered retriever may fetch the underlying document.
Metadata, snippets, inaccessible pages, and model descriptions remain leads.
Articles are classified as reporting, opinion, analysis, or issuer material,
deduplicated by source origin, and clustered into causal themes.

News claims are labelled `Confirmed`, `Contradicted`, `Unverified`, `Opinion`, or `Narrative context`. Reporting remains a source lead until registered primary evidence confirms or contradicts the same claim. GDELT and Tier 4 aggregators remain narrative context.

When no claim is returned, the UI must show whether providers were disabled, unconfigured, inaccessible, outside the event window, or filtered for irrelevance.

## LLM guardrails

The LLM receives curated excerpts and structured evidence IDs only. Output that invents citations, facts, targets, probabilities, or unsupported recommendations is rejected. Provider timeout is reported as execution failure, not guardrail rejection, and deterministic research remains available.

## System-wide paired-model research

When explicitly enabled, Fast Screening, Adaptive IC, Deep Initiation, and
event-scoped research run two choreographies against the same immutable evidence
snapshot. The selected profile changes evidence breadth and latency:

- The primary is DeepSeek-led and GPT-5.6 Sol-audited.
- The challenger is Sol-led, with DeepSeek planning and adversarial audit.
- Planner requests are unioned into one initial retrieval pass; audit requests are unioned into one follow-up pass.
- Sol independently researches each stage and may adjudicate among validated
  interpretations, but it cannot replace deterministic facts or calculations.
- Accepted Sol additions are revalidated against existing evidence IDs before
  the DeepSeek-authored final narrative can use them.

Evidence retrieval occurs only through registered deterministic adapters or the
normalized evidence cache. Models may suggest arbitrary URLs in the discovery
lane, but those URLs cannot support claims until deterministic source admission,
fetching, parsing, citation binding, and validation pass. Every numerical or
dated model claim must reference a validated evidence ID; missing support
downgrades the claim to qualitative. Models cannot set calibrated probability,
fair value, idea stage, or promotion.

Every stage records evidence coverage, primary-source use, period accuracy,
causal completeness, peer/industry/geography reasoning, valuation and credit
effects, hidden risks, monitors, clarity, cost, latency, and failures. Recurring
gaps create review-only improvement candidates. Changes to prompts, adapters,
metric ontology, playbooks, fixtures, or UI require cross-ticker evaluation and
human activation; model output never mutates the running system.

### Input qualification before model comparison

The paired-model workflow does not treat its prompt as an arbitrary first-N
sample. It constructs:

- a materiality-ranked core evidence pack;
- a complete searchable metadata catalog;
- deterministic on-demand opening from the normalized cache;
- an exclusion audit for every item outside the core;
- explicit current/prior pairs for change claims; and
- mandatory retention of material contradictions.

Core ranking weights are materiality 25%, focal-driver relevance 20%, event and
fiscal-period alignment 15%, source quality 15%, contradiction/falsification
value 10%, freshness 10%, and source diversity 5%. Source excerpts are
untrusted input even when the source itself is authoritative; embedded webpage
instructions never change model or retrieval behavior.

Input sufficiency is scored out of 100: identity/security mapping 10, primary
event evidence 15, historical comparison 15, causal KPI coverage 15, peer and
industry context 10, management record 10, valuation and credit inputs 10,
market and macro context 5, contradictions 5, and citation/period integrity 5.
A case must reach 80 with no critical identity, period, citation, or
no-lookahead gap before DeepSeek-versus-Sol differences are interpreted as
model-quality evidence.

The quality-first diagnostic has no fixed pilot dollar cap. It retains one
automatic retry for an audited call, two retrieval passes, request
deduplication, unchanged-source reuse, cancellation, and a review trigger when
actual usage exceeds the preflight estimate by more than 2x. Cost observations
never change evidence or model quality scores.

Historical analogs progress from `Exploratory Candidate` to `Investigated Draft`, `Audited Research Analog`, and `Frozen Demo Dossier`. Current news does not qualify as historical evidence unless it explicitly analyzes the selected episode. Source matching uses entity, episode, date range, economic mechanism, outcome coverage, and source quality. Numeric similarity appears only after a two-origin dossier passes the Sol audit and deterministic validation.

Public execution uses session-only BYOK credentials and the OpenAI Responses background API. Local Codex execution is maintainer-only, read-only, disables web search, and requires an explicit local configuration flag. A Sol audit retries once; three consecutive audit failures raise provider-health warning. Any missing provider, timeout, cancellation, malformed output, or failed final critique preserves deterministic candidates and suppresses the model-written dossier.

Evidence readiness and model execution readiness are independent. The app may
report a benchmark-ready evidence pack while DeepSeek or Sol remains not
started. It reports adjudication only after both model lanes complete and the
final evidence-ID validation passes. Legacy zero-round records are preserved in
storage but normalized when read so they cannot display model scores, ties, or
accepted amendments. Source freshness uses cadence-specific rules: market data
is evaluated by trading sessions, while filings and macro series use slower
windows appropriate to their publication cadence.

Frozen demos store actual model/backend labels only when a maintainer generation run completed. Deterministic fallbacks explicitly say no live model produced them. Frozen artifacts store round completion, prompt and source-registry versions, evidence hash, generation time, and deterministic validation status. They never retain API keys, licensed raw payloads, or hidden model reasoning.

## Retrieval preflight and claim sufficiency

Before an expensive paired-model benchmark, run:

```powershell
python scripts/audit_historical_retrieval.py --ticker AAPL --profile deep_initiation --refresh
```

The command makes no LLM calls. It resolves public evidence, records the exact
retrieval lifecycle, audits material claims, and writes redacted JSON and
Markdown under `data/retrieval_audits/`. The 80/100 input score describes
coverage; claim-level audits remain authoritative. A catalog match or
metadata-only record cannot support a fact.

Official documents are normalized through structured tags, canonical aliases,
HTML/iXBRL, PDF text, optional approved OCR, accepted derivations, and
citation-bound LLM extraction drafts. The deterministic validator must still
confirm identity, period, scope, value, unit, currency, quotation, and source
status. Parse failures remain explicit and never become zero or “not
disclosed.”

## Regulatory Radar

Regulatory prospects progress through reporting, policy discussion, official
agenda or speech, consultation or draft, proposal or bill, final rule, and
effective or enforced action. Challenged, stayed, or repealed measures retain
their procedural history.

Unofficial reporting may create a watch item. Two independent reputable source
origins may support a carefully labelled reported prospect. An official or
semi-official procedural milestone is required before a regulatory prospect can
contribute to High-Conviction. Syndicated copies count once, and announcement
metadata alone cannot establish revenue, margin, capex, credit, or valuation
effects.
