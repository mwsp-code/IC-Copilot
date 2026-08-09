# Sanitized AAPL freeze-failure fixtures

These fixtures retain only the lifecycle, validation, audit, scoring, and
source-origin metadata needed to reproduce two publication-gate failures.
They contain no API keys, licensed source text, hidden reasoning, or live
provider responses.

- `incomplete_newer_draft.json` represents run
  `1ddb8c37-7a71-4d2c-95d4-02558d7367d6`. The observed end-of-attempt time is
  stored separately; the normalized run deliberately leaves `completed_at`
  empty because the lifecycle was incomplete.
- `completed_nonpublishable.json` represents run
  `b3f8d260-a2a5-4c52-9ad9-80ab6fecc898`. Execution completed, but
  deterministic validation, final closure, semantic quality, and independent
  analog provenance did not satisfy publication policy.

The fixtures are regression inputs only. They must never be loaded as the
public AAPL demo.
