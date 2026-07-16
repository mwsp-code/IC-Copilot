from types import SimpleNamespace

import app

from equity_research.models import (
    CompanyIdentity,
    WisburgCoverageAudit,
    WisburgResearchLens,
    WisburgToolEntitlement,
)


class _Block:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def metric(self, *args, **kwargs):
        return None


class _StreamlitFixture:
    def __init__(self) -> None:
        self.tables: list[list[dict]] = []

    def columns(self, count):
        return [_Block() for _ in range(count)]

    def expander(self, *args, **kwargs):
        return _Block()

    def dataframe(self, rows, **kwargs):
        self.tables.append(list(rows))

    def markdown(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def write(self, *args, **kwargs):
        return None


class _StoreFixture:
    def latest_wisburg_delta(self, ticker):
        return None


def test_wisburg_streamlit_panel_renders_entitlement_model_without_attribute_errors(monkeypatch) -> None:
    identity = CompanyIdentity("EXM", "0000000001", "ExampleCo")
    lens = WisburgResearchLens(
        "EXM",
        "Available",
        "2026-07-15T00:00:00+00:00",
        coverage_audit=WisburgCoverageAudit(
            ticker="EXM",
            status="Available",
            observed_at="2026-07-15T00:00:00+00:00",
            endpoint="https://mcp.wisburg.com/mcp",
            authentication_status="authenticated",
            tool_discovery_status="confirmed",
            tools=[WisburgToolEntitlement(
                "list-company-reports", "available", "company",
            )],
        ),
    )
    streamlit = _StreamlitFixture()
    monkeypatch.setattr(app, "st", streamlit)
    monkeypatch.setattr(app, "ResearchStore", lambda: _StoreFixture())

    app.render_wisburg_research_lens(
        SimpleNamespace(identity=identity, wisburg_lens=lens)
    )

    coverage_table = next(
        table for table in streamlit.tables
        if table and "Tool" in table[0]
    )
    assert coverage_table[0]["Category"] == "company"
    assert coverage_table[0]["Entitlement"] == "available"
