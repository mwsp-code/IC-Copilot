# Research Integrity Benchmark

The benchmark checks whether IC Copilot preserves an auditable evidence chain on 25 sanitized,
no-network research-integrity cases across AAPL, NVDA, BABA, TSLA, and GS. Each ticker is tested for
event grounding, thesis grounding, counter-thesis handling, monitor/payoff readiness, and promotion integrity.

It measures:

- citation coverage;
- explicit event dates;
- economic relevance;
- bounded direction classification;
- source identity;
- counter-thesis and monitor coverage for generated ideas;
- whether any High-Conviction idea lacks required support.

It does **not** claim to measure stock-picking accuracy, future returns, or calibrated probabilities.
Those require a larger point-in-time outcome dataset with no lookahead.

```bash
python scripts/run_benchmark.py
```

The command regenerates [`baseline.json`](baseline.json) and [`baseline.md`](baseline.md).
