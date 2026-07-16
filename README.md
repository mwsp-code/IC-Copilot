# IC Copilot: Evidence-First Equity Research

[![Tests](https://github.com/mwsp-code/IC-Copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/mwsp-code/IC-Copilot/actions/workflows/tests.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-ff4b4b.svg)](https://streamlit.io/)

A Streamlit MVP for AI-assisted investment research workflows across US-listed equities. The product
north star is an **Investment Committee Copilot** for a personal analyst or PM: collect evidence
deterministically, decide whether a real investable thesis exists, show the strongest counter-thesis,
and define exactly what must be monitored next.

The app starts with SEC-backed workflows and keeps raw data panels as drilldowns:

- Map ticker to CIK.
- Pull SEC submissions, recent 10-K/10-Q/8-K filings, and XBRL company facts.
- Extract risk, MD&A, guidance, debt, margin, litigation, dilution, and customer concentration signals.
- Compare current filings against previous periods.
- Generate source-linked trade ideas, quality scores, market-capture assessments, monitoring criteria, and DD memo output.
- Synthesize one IC-ready thesis, one counter-thesis, and a source-linked action plan.

The first "wow" workflow is:

> Show me ideas where new evidence changed but price/consensus did not.

Research runs separate `Candidate` hypotheses from practical `Research-Ready` ideas and conservative
`High-Conviction` ideas that pass the applicable evidence, period-alignment, valuation, payoff,
monitoring, contradiction, and score gates. Scenario
returns are calculated from entry and internally modeled exit values; Idea Quality Score never sets a
price target or payoff. Default 25%/50%/25% scenario probabilities are labelled `Uncalibrated` and
remain excluded from EV ranking until 30 resolved outcomes exist for the same signal family and horizon.

The MVP supports **Budget Mode** so the open-source path can stay cheap while premium data remains
adapter-ready:

- `Free`: SEC/issuer evidence, official macro slots, EODHD free EOD prices when keyed, and CSV/manual imports.
- `Lean`: the free stack plus optional low-cost LLM synthesis after deterministic evidence extraction.
- `Stable`: Lean plus one low-cost paid data slot such as Tiingo/FMP when configured.
- `Premium`: richer paid-provider slots for estimates, prices/options, transcripts, and institutional data.

The shipped labels are defaults, not a payment system. Users subscribe to data/LLM vendors directly,
save keys through the OS keychain or environment variables, and then choose which sources each mode
may use. To customize the definitions without editing Python code, copy
`examples/budget_modes.example.json` to `data/budget_modes.json` or set `BUDGET_POLICY_PATH` to another
local JSON file. The file stores only routing and budget metadata, never API keys.

Every run also builds a **Company Economics + Industry Playbook** before idea generation. Filing,
transcript, price, and consensus signals are mapped to business drivers such as revenue growth,
margin/mix, cash generation, balance sheet, governance, or management credibility. Ideas are clustered
around those drivers so the IC output leads with the most convincing thesis, strongest counter-thesis,
valuation bridge, monitoring checklist, and explicit evidence gaps.

The app also builds a **Research Scout** agenda for each run. It turns weak thesis links into registered
source questions across company economics, sector KPIs, peer read-through, geography exposure, and
ADR/FPI specifics. The LLM can use this agenda to improve narrative synthesis, but unanswered scout
questions remain gaps and cannot promote an idea.

Coverage is configurable through CSV packs under `data/`: `source_requirements.csv`,
`sector_kpi_playbooks.csv`, `industry_playbooks.csv`, `peer_universes.csv`,
`geography_exposure_playbooks.csv`, and `adr_profiles.csv`. The seeded ADR list includes major China
internet, China EV, healthcare, India, Taiwan/Japan, Europe, and LatAm ADRs, with China ADR depth for
segment drivers, issuer IR sources, HK/China benchmarks, and FX/policy context.

Each idea now gets a **Conviction Chain**:

1. Source change.
2. Business driver.
3. KPI or forecast impact.
4. Valuation or payoff bridge.
5. Expectation gap.
6. Catalyst and timing.
7. Falsification tests.

Incomplete links stay visible as research actions. This is the main guardrail that keeps the app from
turning isolated signals into overconfident stock calls.

The decision workflow now adds five analyst-facing layers on top of that evidence base:

- **Automatic Evidence Closure** executes each open work order against registered SEC, issuer,
  transcript, macro/market, peer/global-peer, specialist primary-source, and paid-data adapters. It
  reports `resolved`, `contradicted`, `genuinely unavailable`, or `licensed/manual input required`.
- **Causal Thesis Graph** scores each connection independently from source event through business
  driver, operating KPI, earnings/FCF, valuation, and catalyst. The weakest edge names its exact gap
  and next automatic action.
- **Market-Implied Expectations** uses transparent reverse models to show the growth, margin,
  multiple, credit-cost, or commodity assumptions embedded in price when the required facts exist.
  It does not pretend these are analyst consensus estimates.
- **Earnings-Surprise Proxy** compares reported actuals with eligible contemporaneous estimates while
  explicitly separating the surprise from any later analyst-revision follow-through.
- **Recent Market Context** uses cached adjusted EOD bars for relative returns, realized volatility,
  drawdown, and volume-regime checks. These are market diagnostics, not proof of a fundamental cause.
- **Company Model Workspace** keeps historical line items, formulas, editable assumptions,
  bull/base/bear cases, and sensitivities together with source/formula/user-override provenance.
- **Driver-Specific Research Modes** provide focused earnings, margin, capital-allocation, credit,
  regulatory, product-cycle, management-credibility, and relative-value workflows.

The same artifacts are available from the local APIs: `/api/evidence-closure`,
`/api/causal-thesis-graph`, `/api/market-implied`, `/api/earnings-surprise-proxy`,
`/api/recent-market-context`, `/api/company-model`, and `/api/research-modes`.

## Adaptive research profiles

**Adaptive IC Research** is the default analyst workflow. It requests 12 quarters, 4 annual reports,
and 12 earnings calls, investigates the five most material or contradictory changes, and deepens to
five years for acquisitions, goodwill, restructuring, segment changes, debt, regulation, and tracked
management promises. The run manifest records both discovered documents and the subset actually
parsed and period-aligned; document inventory is never presented as completed analysis.

Three prominent alternatives let users control cost and depth:

- **Fast Screening:** 4 quarters, 2 annual reports, 4 calls, and the highest-ranked anomaly.
- **Deep Initiation:** 20 quarters, 5 annual reports, 20 calls, and every material contradiction.
- **Investigate This Event:** a filing-, call-, metric-, or news-scoped investigation that fetches only
  the relevant history, peers, and corroborating evidence.

Ambiguous metrics are neutral first. For example, goodwill is treated as acquisition accounting and
capex as an investment-cycle signal; each receives constructive and adverse hypotheses until cited
evidence validates a directional mechanism. Unknown metrics remain `Unmapped` and never fall back to
revenue growth.

Normalized Tier 3 research may strengthen hypotheses and Research-Ready narratives. A tightly audited
exception allows two independent, non-syndicated Tier 3 sources to replace exactly one unavailable
Tier 1 High-Conviction gate only when registered primary adapters were attempted, quantitative and
causal evidence agree, no primary source contradicts the claim, and every other gate passes. These
ideas are labelled `High-Conviction: secondary-supported` and capped at 75. Tier 4 sources, GDELT,
anonymous summaries, and LLM output never qualify for this exception.

Every live run records stage timings by default. To reproduce a redacted command-line profile and
confirm that faster execution did not reduce evidence coverage, run:

```powershell
python scripts/profile_research.py --ticker AAPL --profile adaptive_ic
```

The report includes stage durations plus quality counts for filings, calls, peers, events, ideas, and
validated claims. Independent provider and peer requests use bounded concurrency; output ordering,
point-in-time rules, citations, profile depth, and promotion gates remain unchanged. Parsed filing
sections and historical comparison summaries are cached by accession and parser version.

Financial coverage is explicit. New or registration-stage issuers fall back from SEC Company Facts to
tagged Inline XBRL in `S-1`, `F-1`, or `424B4` filings. Untagged tables are not guessed. Coverage states
such as `registration_only`, `facts_unmapped`, and `provider_failed` explain why a snapshot is missing.

Peer checks use curated, sector-aware universes. Event reactions are fixed 1/5/20-session returns from
the prior close, with market-, sector-, and beta-adjusted views. SEC evidence and price failures are
reported independently so one provider failure cannot silently erase the other result.

Consensus and transcript providers are intentionally adapter-shaped but optional in this MVP. The no-key version uses SEC data and Stooq price checks where available.

## Optional LLM thesis synthesis

The app can optionally use an LLM as a synthesis and critique layer. The deterministic pipeline first
builds curated excerpts, structured claims, valuation outputs, price attribution, consensus changes,
management claims, and citation IDs. Only that evidence pack is sent to the selected LLM provider.
The LLM is not allowed to invent facts, citations, price targets, probabilities, or recommendations;
if the evidence is insufficient, the IC brief should say **No convincing thesis yet**.

LLM synthesis is disabled by default. The recommended setup is the in-app **LLM Provider Vault**:
create one saved profile for the primary synthesis model and, optionally, one saved profile for the
secondary reader. Profile metadata is stored locally, while profile API keys are stored only through
the OS keychain secret backend.

DeepSeek is available as a first-class OpenAI-compatible preset using the endpoint
`https://api.deepseek.com`, with `deepseek-v4-pro` as the default primary synthesis model. Faster
or cheaper compatible models can still be configured manually as fallbacks. OpenAI, Anthropic, Qwen, Kimi, local Ollama-compatible servers, and custom
OpenAI-compatible providers remain available through the same vault UI.

Enable LLM synthesis from the app's Data Sources panel or with:

```powershell
$env:ENABLE_LLM_THESIS="true"
$env:ENABLE_SECONDARY_LLM_REVIEW="true"
$env:SECONDARY_LLM_MIN_STAGE="Research-Ready"
$env:LLM_LANGUAGE_POLICY="bilingual_audit"
```

Fixed environment variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `QWEN_API_KEY`,
`KIMI_API_KEY`, and `DEEPSEEK_API_KEY` remain backward-compatible fallbacks. ChatGPT Plus does not
automatically cover API usage; API billing is separate and provider-specific.

## Run the no-dependency local app

```powershell
python server.py --port 8501
```

Then open the URL printed in the terminal. If `8501` is already in use, the server automatically tries the next open port up to `8510`.

Start with the built-in **Load Demo** gallery before configuring API keys. The current AAPL, NVDA,
BABA, TSLA, and GS showcases use sanitized **Deep Initiation** fixtures: 20 quarters, five annual
reports, 20 calls, Premium-mode provider slots, official macro context, a bounded Wisburg research
lens, market-implied expectations, and guardrailed LLM research metadata. They remain instant and
no-network: no paid API is called and no licensed full-text payload is committed. SPCX/SPXC remains
an entity-resolution guardrail demo. See [docs/quickstart.md](docs/quickstart.md).

If `python` is not on PATH, use the Python executable available in your environment.

## Optional Streamlit UI

```powershell
pip install -r requirements.txt
streamlit run app.py
```

For SEC access, set a descriptive user agent:

```powershell
$env:SEC_USER_AGENT="Your Name your.email@example.com"
```

## Local API keys without committing secrets

The recommended path is the in-app OS keychain flow. Enter keys in the Data Sources panel, test them,
then choose **Save Valid Keys**. The app first uses Python `keyring` for the OS keychain. On Windows,
if `keyring` is not installed or unavailable, it falls back to a DPAPI-encrypted vault under the
gitignored `data/` directory. Password fields are never prepopulated, and keys are never written to
SQLite, browser storage, memos, logs, or API status responses.

The app also supports `.env` and `.env.local` as a manual fallback. Real OS environment variables take
priority over OS-keychain/DPAPI values, and saved local secrets take priority over `.env.local`. Local
secret files are gitignored, while `.env.example` is safe to commit.

Managed local secrets include Alpha Vantage, Finnhub, FRED, BEA, Census, Wisburg, LLM provider keys,
and the SEC user agent. Wisburg keys can be saved locally, but Wisburg evidence is treated as
third-party context only.

Manual fallback setup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_local_env.ps1
```

Then restart `streamlit run app.py` or `python server.py`. To edit manually, copy `.env.example` to
`.env.local` and fill in local values. Never put real keys in Python files, README examples, tests, or
committed config. If keys have been pasted into chats, screenshots, or issue trackers, rotate them
before publishing the project.

## Bring-your-own data

To keep the MVP cheap and open-source friendly, the app scans local CSV imports before treating missing
paid data as fatal. Put optional files under `data/manual_import/`:

- `segment_kpis.csv`: `ticker,segment,metric,value`
- `industry_data.csv`: `ticker,industry,metric,value`
- `report_excerpts.csv`: `ticker,source,excerpt`

Existing import folders for consensus, prices, and transcripts remain supported. Manual rows are labelled
as user-provided context and do not replace SEC/issuer citations for high-conviction evidence.

## Consensus, watchlists, and alerts

Alpha Vantage is the free primary source for its aggregate analyst target and rating mix. Finnhub
validates recommendation trends. Both keys can be entered in either app for the current session or
configured through `.env.local` or environment variables:

```powershell
$env:ALPHAVANTAGE_API_KEY="your-free-key"
$env:FINNHUB_API_KEY="your-free-key"
streamlit run app.py
```

FMP remains an optional provider through `FMP_API_KEY`. Environment variables are captured when the
app starts. Session password fields are never cached, logged, or written to SQLite.

EODHD can provide adjusted daily prices under its available entitlement. The free-tier integration is
used for event windows, peer/benchmark reactions, recent relative performance, volatility, drawdown,
volume context, current-price inputs, and reverse-implied expectations. Successful bars are cached in
SQLite, and the client defaults to an 18-call per-run ceiling so a broad peer run does not consume the
entire daily allowance. Override that ceiling with `EODHD_MAX_CALLS_PER_RUN` only when the subscribed
plan supports it. EODHD prices do not substitute for SEC/issuer facts or historical analyst consensus.

Macro attribution can use FRED, BLS, BEA, Census, Treasury/Fiscal Data, World Bank, IMF, and optional
GDELT. BLS and Treasury are enabled by default when default macro sources are on. FRED, BEA, and
Census run automatically only when their keys are configured. World Bank and IMF run by default for
ADR/FPI names or when `GLOBAL_MACRO_MODE=true`. GDELT remains opt-in because it is noisy narrative
context, not primary evidence.

Two no-key personal-research fallbacks are available only when explicitly enabled in the app or with:

```powershell
$env:ENABLE_NASDAQ_CONSENSUS="true"
$env:ENABLE_TRADINGVIEW_CONSENSUS="true"
```

Nasdaq supplies unofficial EPS forecasts with month-precision fiscal periods. TradingView supplies an
unofficial median/high/low target distribution. Missing source timestamps, currencies, and analyst
counts remain `Unknown`; unofficial-only data is labelled `Partial - unofficial only` and cannot
independently support a high-conviction or uncaptured classification. Yahoo remains disabled because
its raw quote-summary endpoint commonly returns `401 Unauthorized`.

Test entitlements for both AAPL and BABA without exposing the key:

```powershell
python scripts/test_fmp_consensus.py --tickers AAPL,BABA
```

Research runs save normalized point-in-time snapshots to `data/research.db`. Add tickers to the default watchlist in the Thesis Monitor, then run the daily collector after the US close. The collector snapshots consensus, caches available daily prices, and uses Wisburg automatically when its key is configured:

```powershell
python scripts/snapshot_consensus.py --watchlist default --wisburg auto
```

Use `--wisburg off` to skip external research or `--refresh-wisburg` to bypass the same-day Wisburg cache. A Wisburg snapshot stores only capped metadata/excerpts, normalized report fields, structured claims/revisions, citations, and a normalized lens. The delta monitor reports newly observed reports, external revision observations, theme-stance changes, and corroboration changes; it never interprets absence from a capped result as a deleted report. Wisburg targets and revisions remain external analyst context, not official point-in-time consensus.

On a Windows machine configured for Asia/Shanghai time, install the 08:00 daily task with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_snapshot_task.ps1 -At "08:00" -Watchlist "default" -Wisburg "auto"
```

Choose a local time after the US close. `08:00` is appropriate for many Asia-based workflows; a US-based installation should select an evening time. The collector skips weekends and standard US exchange holidays, reuses same-day Wisburg results, isolates provider failures, and records each provider's outcome in the Thesis Monitor.

## Research audit APIs

After a live research run, the local server exposes the normalized audit trail through:

- `GET /api/entity?ticker=...`
- `GET /api/financial-coverage?ticker=...`
- `GET /api/peers?ticker=...`
- `GET /api/ideas/{id}/audit`
- `POST /api/ideas/{id}/promote`
- `GET /api/calibration`
- `GET /api/snapshot-status?ticker=...`
- `GET /api/wisburg-delta?ticker=...`
- `GET /api/wisburg-coverage?ticker=...`
- `GET /api/wisburg-reports?ticker=...`
- `GET /api/wisburg-claims?ticker=...`
- `GET /api/wisburg-revisions?ticker=...`
- `GET /api/wisburg-work-orders?ticker=...`

Promotion succeeds only when the stored gate result is eligible. Versioned forecasts preserve evidence,
scenario assumptions, probabilities, payoff inputs, and thesis checks for later post-mortem analysis.

Without provider keys or enabled fallbacks, copy the header-only templates from `examples/consensus_import/` to
`data/consensus_import/` and populate them with licensed data. Missing provider data is displayed as
unavailable and never inferred.

## Wisburg MCP diagnostic

Wisburg is an optional external research layer for analyst narrative, counter-thesis, bilingual ADR/HK
context, and "what outside analysts are focused on" sections. It does not replace SEC/issuer evidence,
does not count as official consensus, and cannot independently promote an idea to High-Conviction.
The adapter audits MCP tool entitlement, consumes capped search/listing excerpts, and requests bounded
report/article detail only when the authenticated tool catalog permits it. Detail is normalized into report
metadata, cited structured claims, and external revision observations; full vendor payloads are not retained.
The research workflow then attempts registered SEC, issuer, transcript, regulator, or manual/consensus
work orders to corroborate each material external claim. The UI reports `Primary context corroborated`,
`Underlying driver corroborated; forecast unverified`, `Contradicted by primary evidence`, or
`Primary corroboration missing` rather than treating an external report as proof.
Unless the underlying publisher is independently identified, Wisburg items are conservatively treated as one
aggregator origin for source-independence checks.

The repository includes a read-only MCP diagnostic that keeps the token out of source control:

```powershell
$env:WISBURG_API_KEY="your-key"
python scripts/test_wisburg_mcp.py --query "Alibaba BABA" --first 3
Remove-Item Env:WISBURG_API_KEY
```

The diagnostic initializes the MCP session, lists advertised tools, and optionally tests company reports,
earnings calls, institutional research, articles, market dailies, and feed coverage.

## Notes

This tool produces research hypotheses, not financial advice. Every generated idea should be reviewed by a human analyst before being used in an investment process.
