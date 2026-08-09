# Frozen Hybrid Demo Artifacts

This directory may contain sanitized five-round Deep Initiation artifacts named
`TICKER.json`.

Generate an artifact on a trusted maintainer workstation:

```bash
python scripts/generate_hybrid_demo.py --ticker AAPL --executor codex_local
```

Publish a specific saved run or the newest policy-eligible saved run without
calling a model:

```bash
python scripts/generate_hybrid_demo.py --ticker AAPL --run-id RUN_ID
python scripts/generate_hybrid_demo.py --ticker AAPL --latest-eligible
```

Request a standard idempotent live repair/resume from a saved draft:

```bash
python scripts/generate_hybrid_demo.py --ticker AAPL --resume-run-id RUN_ID
```

Live generation and repair require a valid DeepSeek profile and either a locally
authenticated Codex CLI or an OpenAI Responses credential. Stored-run selection
does not. The versioned quality policy freezes only a completed,
deterministically validated and independently closed result with audited analogs,
claim-level integrity, required AAPL financial paths, and sanitization. If no
candidate passes, the existing artifact is preserved and a repair report is
written. API keys, raw licensed payloads, hidden reasoning, and unsupported
claims are excluded.

Demo loading never calls a live model. When no generated artifact exists, the
app uses a truthful sanitized presentation fixture and does not claim that a
provider generated it.
