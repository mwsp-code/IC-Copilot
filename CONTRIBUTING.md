# Contributing

Contributions are welcome from equity and credit analysts, portfolio managers, data engineers, and
AI builders. The project values auditable research coverage more than the number of generated ideas.

## Good first contributions

- Add a sector KPI playbook in `data/sector_kpi_playbooks.csv`.
- Add or improve an ADR/FPI profile in `data/adr_profiles.csv`.
- Add canonical source-tag aliases in `data/metric_ontology.csv`.
- Add a registered source adapter with provider health and citation metadata.
- Add a sanitized, no-network demo with a regression test.

## Evidence requirements

- Preserve source URL, publication or filing time, reporting period, unit, and currency.
- Keep missing values as `Unknown`; never convert missing data to zero.
- Enforce no-lookahead behavior for filings, prices, consensus, and macro releases.
- Treat external research and news as context unless independently corroborated.
- Do not let LLM output invent facts, citations, targets, probabilities, or promotion decisions.
- Do not commit API keys, licensed full-text reports, local databases, or vendor payloads.

## Development workflow

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
```

Add fixture-based tests for new providers and parsers. Live-network tests must remain optional and
must never be required by the normal suite.

By submitting a contribution, you agree that it may be distributed under the MIT License.
