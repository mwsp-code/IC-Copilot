"""Minimal registered connector extension for IC Copilot.

This example is intentionally offline. Replace the fixture body with an
entitled deterministic client, preserve the return contract, and add the
connector to the infrastructure registry. Discovery metadata never bypasses
the common admission gateway.
"""

from equity_research.domain import RetrievedCandidateDocument


class ExampleIssuerConnector:
    connector_id = "example-issuer-connector"
    supported_source_families = ("issuer_ir",)

    def retrieve(self, request, candidate):
        return RetrievedCandidateDocument(
            candidate_id=candidate.candidate_id,
            connector_id=self.connector_id,
            canonical_url=candidate.url,
            content="Replace with parsed, citation-bound issuer content.",
            source_family="issuer_ir",
            source_type="issuer_announcement",
            publisher="Example issuer",
            publication_timestamp="2026-01-01T00:00:00+00:00",
            retrieval_timestamp="2026-01-01T01:00:00+00:00",
            licensing_policy="public_issuer_material",
            provenance="Example registered issuer connector.",
        )
