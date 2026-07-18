# IC Copilot Research Integrity Benchmark

- Status: **passed**
- Research-integrity cases: **25**
- Structural checks: **125/125 (100.0%)**
- Network required: **No**

> This benchmark measures evidence-chain integrity on sanitized fixtures. It does not measure forecast accuracy or returns.

## Coverage

| Ticker | Events | Ideas | Citation-bound ideas | Counter-thesis coverage | Monitor coverage | Unsupported high-conviction |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 2 | 2 | 2 | 2 | 2 | 0 |
| NVDA | 2 | 2 | 2 | 2 | 2 | 0 |
| BABA | 2 | 2 | 2 | 2 | 2 | 0 |
| TSLA | 2 | 2 | 2 | 2 | 2 | 0 |
| GS | 2 | 2 | 2 | 2 | 2 | 0 |

## Integrity Checks

| Case | Score | Check | Source |
|---|---:|---|---|
| AAPL-01 | 100 | Source event is dated, cited, and economically interpreted | Apple FY26 Q2 consolidated financial statements |
| AAPL-02 | 100 | Thesis remains connected to source evidence | TradeIdea |
| AAPL-03 | 100 | Counter-thesis and falsification remain visible | TradeIdea |
| AAPL-04 | 100 | Monitoring, market context, and payoff gaps are explicit | TradeIdea |
| AAPL-05 | 100 | Promotion gates cannot hide unsupported conviction | Deterministic gate |
| NVDA-01 | 100 | Source event is dated, cited, and economically interpreted | Demo 10-Q |
| NVDA-02 | 100 | Thesis remains connected to source evidence | TradeIdea |
| NVDA-03 | 100 | Counter-thesis and falsification remain visible | TradeIdea |
| NVDA-04 | 100 | Monitoring, market context, and payoff gaps are explicit | TradeIdea |
| NVDA-05 | 100 | Promotion gates cannot hide unsupported conviction | Deterministic gate |
| BABA-01 | 100 | Source event is dated, cited, and economically interpreted | Demo 20-F |
| BABA-02 | 100 | Thesis remains connected to source evidence | TradeIdea |
| BABA-03 | 100 | Counter-thesis and falsification remain visible | TradeIdea |
| BABA-04 | 100 | Monitoring, market context, and payoff gaps are explicit | TradeIdea |
| BABA-05 | 100 | Promotion gates cannot hide unsupported conviction | Deterministic gate |
| TSLA-01 | 100 | Source event is dated, cited, and economically interpreted | Demo 10-Q |
| TSLA-02 | 100 | Thesis remains connected to source evidence | TradeIdea |
| TSLA-03 | 100 | Counter-thesis and falsification remain visible | TradeIdea |
| TSLA-04 | 100 | Monitoring, market context, and payoff gaps are explicit | TradeIdea |
| TSLA-05 | 100 | Promotion gates cannot hide unsupported conviction | Deterministic gate |
| GS-01 | 100 | Source event is dated, cited, and economically interpreted | Demo 10-Q |
| GS-02 | 100 | Thesis remains connected to source evidence | TradeIdea |
| GS-03 | 100 | Counter-thesis and falsification remain visible | TradeIdea |
| GS-04 | 100 | Monitoring, market context, and payoff gaps are explicit | TradeIdea |
| GS-05 | 100 | Promotion gates cannot hide unsupported conviction | Deterministic gate |

## Reproduce

```bash
python scripts/run_benchmark.py
```
