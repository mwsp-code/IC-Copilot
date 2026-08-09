"""Minimal structured LLM provider adapter for IC Copilot.

This offline example is safe to import in tests. A real adapter should perform
its bounded network call inside ``complete_json`` and translate provider errors
into the registry's timeout, entitlement, retryable, or terminal categories.
"""


class ExampleStructuredLlmProvider:
    provider_name = "example_structured_provider"
    model = "example-model-v1"

    def complete_json(self, prompt_pack: dict) -> dict:
        # Never return hidden reasoning. Preserve the requested schema and keep
        # factual statements tied to admitted evidence IDs supplied in the pack.
        return {
            "status": "completed",
            "summary": "Offline example response.",
            "admitted_evidence_ids": list(
                prompt_pack.get("admitted_evidence_ids") or []
            ),
            "research_requests": [],
        }
