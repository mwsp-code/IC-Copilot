# Development Guide

## One-command setup

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
python -m pip install -e ".[dev]"
streamlit run app.py
```

On Windows, the checked launcher runs a syntax preflight before starting Streamlit:

```powershell
.\scripts\run_streamlit.ps1
```

Terminal commands belong in PowerShell or a shell, never inside `app.py` or `server.py`.
The launcher and CI compile both entrypoints before startup or test collection.

The repository also includes a Dockerfile and devcontainer. Neither image contains API keys;
configure credentials at runtime through environment variables or the local OS keychain.

## Architecture boundaries

- `app.py`: thin compatibility entrypoint and composition root.
- `equity_research/presentation`: Streamlit routes, typed view models, and render-only intents.
- `equity_research/application`: lifecycle, Research Request, investigation, repair, and publication use cases.
- `equity_research/domain`: pure evidence, audit, lifecycle, publication, and coverage contracts.
- `equity_research/infrastructure`: SQLite repositories and registered provider/connector adapters.
- `server.py`: lightweight local HTTP interface.
- `equity_research/pipeline.py`: deterministic research orchestration.
- `equity_research/models.py`: normalized contracts shared by providers and UI.
- `equity_research/*_providers.py`: source adapters and provider health.
- `equity_research/storytelling.py`: presentation-only summaries built from validated objects.
- `equity_research/benchmarking.py`: fixture-only integrity benchmark.
- `data/*.csv`: configurable playbooks, metric ontology, ADR profiles, and coverage fixtures.

New research logic belongs in a focused `equity_research` module with fixture tests. Keep `app.py`
and `server.py` as renderers; do not put source parsing or promotion decisions in UI callbacks.

## Adding a publication gate

A contributor adding a publication gate must provide:

1. A controlled `PublicationFailureCode` and readable blocking rule.
2. A repair kind and strategy registered outside provider-specific orchestration.
3. Explicit completion criteria and idempotency inputs.
4. Durable manifest/run events and dependency invalidation inputs.
5. A presentation adapter that exposes no raw IDs in routine UI.
6. Sanitized offline fixtures for pass, repair, unavailable, and policy-upgrade behavior.

Do not lower a demo threshold or edit frozen JSON to make a result pass. Run
`python scripts/generate_hybrid_demo.py --ticker AAPL --latest-eligible`; a
failed candidate writes a repair report and preserves the published artifact.

## Verification

```bash
python -m compileall -q app.py server.py equity_research scripts
ruff check app.py server.py equity_research scripts tests
pytest -q
python scripts/run_benchmark.py
```

Live-provider tests must be optional. The normal suite and benchmark must run without network access,
credentials, paid data, or licensed text.

## First contribution

See [good first issues](contributing/good-first-issues.md), [architecture](architecture.md), and
[methodology](methodology.md). A source adapter must preserve provider health, source URL, retrieval
time, reporting period, unit/currency, licensing policy, and no-lookahead eligibility.
