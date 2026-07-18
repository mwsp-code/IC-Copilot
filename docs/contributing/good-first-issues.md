# Good First Issues

These are review-ready contribution ideas. Open the matching GitHub issue form before starting so
the metric/source contract is clear.

## Add a sector playbook

Add one missing sector to `data/sector_kpi_playbooks.csv`, including material KPIs, causal drivers,
valuation methods, catalysts, falsification tests, and at least two fixture tests.

## Add an ADR/FPI profile

Extend `data/adr_profiles.csv` with listing ratio, reporting currency, fiscal year-end, primary forms,
home exchange, issuer IR sources, and a security-identity regression test.

## Resolve a canonical metric alias

Add a documented source tag to `data/metric_ontology.csv`. Include direct/derived provenance, unit,
period semantics, and a golden extraction test. Missing values must remain `Unknown`.

## Add a registered source adapter

Implement one official or licensed provider behind the existing adapter contracts. Include provider
health, entitlement errors, cache behavior, citation metadata, licensing policy, and malformed/timeout fixtures.

## Add a no-network demo case

Create a sanitized fixture that teaches one research lesson and exercises the current story-first UI.
Do not include raw vendor payloads, API keys, copyrighted full text, or unverifiable claims.
