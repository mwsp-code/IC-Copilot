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
.\scripts\start_ic_copilot.cmd
```

The launcher tests candidate interpreters before using them. An incomplete
`.venv` is skipped instead of producing a late `ModuleNotFoundError`. Streamlit
and pypdf are required to launch; keyring is optional and affects only secure
OS-keychain persistence. To use the same Python runtime as an existing
Streamlit installation, pass it explicitly:

```powershell
.\scripts\start_ic_copilot.cmd -PythonPath "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe"
```

The `.cmd` entrypoints deliberately use a process-scoped PowerShell execution
policy so a normal Windows script policy does not block the checked launcher.
They do not change the machine or user execution policy.

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
- `equity_research/domain/research_ontology.py`: versioned issuer archetypes,
  mechanisms, safe phrase matching, typed lens contracts, and analog scope
  policy. Add new archetypes here without placing ticker conditionals in the
  workflow or presentation layers.

New research logic belongs in a focused `equity_research` module with fixture tests. Keep `app.py`
and `server.py` as renderers; do not put source parsing or promotion decisions in UI callbacks.

## Local Codex execution diagnostics

Local Independent QA uses saved `codex login` authentication and requires no
OpenAI API key. Observable execution is enabled by default. The app distinguishes
a 60-second startup deadline, a 180-second quiet indicator, and the bounded hard
deadline. Quiet execution is not cancelled merely for emitting no model event.

For local troubleshooting, check the app's connection readiness and expanded
Audit diagnostics. Routine UI intentionally omits Codex thread IDs and raw event
content. Update the Codex CLI when the app reports that observable JSONL
execution is unsupported; it does not fall back to an unobservable subprocess.

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

Windows contributors can bootstrap an isolated environment and run the entire
release gate with one command:

```powershell
.\scripts\quality.cmd -Bootstrap
```

If Streamlit already works under a particular interpreter, reuse it without
creating another environment:

```powershell
.\scripts\quality.cmd -PythonPath "C:\path\to\python.exe"
```

To diagnose only Python and dependency readiness without running the suite:

```powershell
.\scripts\quality.cmd -PythonPath "C:\path\to\python.exe" -RuntimeOnly
```

The script verifies the interpreter and dependencies before running compile,
lint, pytest/Streamlit AppTest, and the offline benchmark. A network-restricted
automation sandbox may be unable to install missing packages or execute a
user-owned Python installation; that is an environment boundary, not an app
test result. In that case, use the checked-in GitHub Actions matrix, which runs
Linux Python 3.9/3.11/3.13, a live Streamlit health check, and a targeted Windows
Python 3.9 saved-result/UI compatibility job.

The equivalent individual commands are:

```bash
python -m compileall -q app.py server.py equity_research scripts
ruff check app.py server.py equity_research scripts tests
python -m pytest -q
python scripts/run_benchmark.py
```

Live-provider tests must be optional. The normal suite and benchmark must run without network access,
credentials, paid data, or licensed text.

## First contribution

See [good first issues](contributing/good-first-issues.md), [architecture](architecture.md), and
[methodology](methodology.md). A source adapter must preserve provider health, source URL, retrieval
time, reporting period, unit/currency, licensing policy, and no-lookahead eligibility.
