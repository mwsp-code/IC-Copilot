# Development Guide

## One-command setup

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
python -m pip install -e ".[dev]"
streamlit run app.py
```

The repository also includes a Dockerfile and devcontainer. Neither image contains API keys;
configure credentials at runtime through environment variables or the local OS keychain.

## Architecture boundaries

- `app.py`: Streamlit composition and presentation.
- `server.py`: lightweight local HTTP interface.
- `equity_research/pipeline.py`: deterministic research orchestration.
- `equity_research/models.py`: normalized contracts shared by providers and UI.
- `equity_research/*_providers.py`: source adapters and provider health.
- `equity_research/storytelling.py`: presentation-only summaries built from validated objects.
- `equity_research/benchmarking.py`: fixture-only integrity benchmark.
- `data/*.csv`: configurable playbooks, metric ontology, ADR profiles, and coverage fixtures.

New research logic belongs in a focused `equity_research` module with fixture tests. Keep `app.py`
and `server.py` as renderers; do not put source parsing or promotion decisions in UI callbacks.

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
