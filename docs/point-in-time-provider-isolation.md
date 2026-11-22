# Point-in-time revision isolation: source and fiscal period

**Scope:** review-only SQLite research-store correction in the public IC Copilot
source tree. This is not proof that this revision produced either deployed
Cloud Run image referenced in Weekly Alpha PR #100. No cloud calls, database
migration, cache change, or production deployment occurs in this patch.

## Decision-relevant defect

When a caller omitted `provider`, the target-revision code could take an
end-of-window snapshot from provider Alpha and compare it with a later-dated
start-of-window snapshot from provider Beta. The resulting percent change was
not a revision for either provider.

Estimate revisions had the same cross-provider risk, plus an independent
dimension: observations marked `annual` and `quarterly` can share metric
and period-end. Mixing them creates an economically meaningless percentage.

## Corrected behavior and regression coverage

- Target revisions select the most recent provider, then require its
  historical observation and earliest-history diagnostic to come from
  **that same provider**.
- Estimate revisions select the ending observation, then require the
  comparison observation to match both its **provider and period_type**.
- `estimate_revision(..., period_type=...)` is an optional additional
  parameter; existing callers remain valid. The expectations-history
  caller now passes its explicit estimate period type.
- Missing aligned history remains `insufficient_history`, not a
  reconstructed value borrowed from another source or fiscal period.
- Same-day provider ties are deterministic in these retrieval paths.
- Tests cover mixed sources, mixed fiscal periods, missing history,
  future-dated snapshots, and deterministic ties.

Run focused tests in a source checkout:

```sh
python -m pytest -q tests/test_pit_provider_isolation.py tests/test_research_store.py
```

## Further limits, not silently resolved

This change prevents source and fiscal-period *mixing* but does not prove a
historical database row was actually observed on its `as_of` date. The
current schema distinguishes some provider timestamps and observations in
models, but the consensus/estimate snapshot tables do not establish a
tamper-resistant first-observed date for retrospective backfills.
Do **not** claim historical point-in-time backtesting correctness merely
from `as_of <= event_date` until observation-time semantics are independently
validated with licensed source data. No Firestore deployed query caller,
Container Analysis provenance, or image-to-source attestation is established
by the IC Copilot GitHub repository or by these tests.

All cloud API enablement, production query/cache changes, billing inquiry,
registry deletion, deployment, merge, and trading gates remain closed.
