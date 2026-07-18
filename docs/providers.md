# Provider Configuration

The frozen demos need no credentials. Live research is free-first and degrades explicitly when a provider is unavailable.

## Budget modes

| Mode | Default intent | Typical sources |
|---|---|---|
| Free | Official and keyless research | SEC, issuer documents, Treasury, BLS, World Bank/IMF for ADRs, manual imports |
| Lean | Free stack plus low-cost synthesis | Free sources plus one optional LLM |
| Stable | More reliable market and expectations data | Lean plus Tiingo, FMP, EODHD, or another configured provider |
| Premium | Deeper paid context | Richer consensus, transcripts, options, news, and external research slots |

These labels are routing presets, not subscriptions. Users contract with vendors directly.

## Credential precedence

1. OS environment variables.
2. OS keychain or Windows DPAPI local vault.
3. `.env.local`.
4. Current Streamlit session input.
5. Missing.

Keys are never displayed in status APIs, written to SQLite, stored in browser local storage, or included in prompts and memos.

## Common variables

```text
SEC_USER_AGENT=
ALPHAVANTAGE_API_KEY=
FINNHUB_API_KEY=
FMP_API_KEY=
TIINGO_API_KEY=
EODHD_API_KEY=
FRED_API_KEY=
BEA_API_KEY=
CENSUS_API_KEY=
WISBURG_API_KEY=
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
```

Use `.env.example` as the complete placeholder reference. Never commit `.env`, `.env.local`, Streamlit secrets, databases, logs, or vendor payloads.

## Streamlit Cloud

Configure credentials in the app's Streamlit Cloud settings rather than the repository. Frozen demos remain available when no secrets are configured. SEC access should use a descriptive `SEC_USER_AGENT`.

## Consensus history

Daily provider observations become valid point-in-time history for future comparisons. A snapshot recorded today cannot establish pre-event expectations for an older event. Use previously stored snapshots or licensed/manual CSV imports for historical revision claims.

## External research

Wisburg and news providers are external context. They may suggest debate themes and follow-up sources but are not official consensus and cannot independently create High-Conviction evidence.

