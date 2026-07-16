import unittest

from equity_research.evidence_work_order import build_evidence_work_order
from equity_research.global_coverage import (
    build_canonical_metric_ontology,
    build_metric_resolution_audit,
    coverage_case_for,
    global_coverage_work_order_items,
    security_type_profile_for,
    source_coverage_matrix_for,
)
from equity_research.models import CompanyIdentity, FilingRecord, FinancialMetric
from equity_research.sample_data import demo_result


class GlobalCoverageTests(unittest.TestCase):
    def test_baba_maps_to_adr_fpi_overlay_sources(self) -> None:
        case = coverage_case_for(
            "BABA",
            CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Limited"),
            filings=[FilingRecord("20-F", "acc", "2026-05-01", "2026-03-31", "baba.htm", "20-F", "https://sec.test/baba")],
        )
        matrix = source_coverage_matrix_for(case, [])

        self.assertEqual(case.security_type, "ADR")
        self.assertIn("hkex_document", case.primary_sources)
        self.assertIn("issuer_ir", case.primary_sources)
        self.assertTrue(any(entry.source_type == "openfigi" for entry in matrix.entries))
        self.assertTrue(all(entry.licensing_policy for entry in matrix.entries))

    def test_global_large_cap_universe_spans_geographies_and_security_types(self) -> None:
        tickers = ["AAPL", "ASML", "TM", "INFY", "BHP", "PBR", "BYDDF"]
        cases = [coverage_case_for(ticker) for ticker in tickers]

        geographies = {case.region for case in cases}
        security_types = {case.security_type for case in cases}
        self.assertIn("North America", geographies)
        self.assertIn("Europe", geographies)
        self.assertIn("Asia", geographies)
        self.assertIn("LatAm", geographies)
        self.assertIn("ADR/FPI", security_types)
        self.assertIn("OTC ADR / foreign ordinary", security_types)

    def test_metric_resolution_derives_gross_profit_and_margin_without_zero_filling(self) -> None:
        metrics = [
            FinancialMetric("Revenue", 100.0, "USD", "2026-03-31", previous_value=80.0),
            FinancialMetric("Cost of Revenue", 60.0, "USD", "2026-03-31", previous_value=50.0),
        ]

        audit = build_metric_resolution_audit("TEST", metrics, coverage_case_for("AAPL"), build_canonical_metric_ontology())
        by_metric = {item.metric: item for item in audit.items}

        self.assertEqual(by_metric["Gross Profit"].status, "metric derived")
        self.assertEqual(by_metric["Gross Profit"].value, 40.0)
        self.assertEqual(by_metric["Gross Margin"].status, "metric derived")
        self.assertEqual(by_metric["Gross Margin"].value, 40.0)
        self.assertEqual(by_metric["Operating Income"].status, "metric missing")
        self.assertIsNone(by_metric["Operating Income"].value)

    def test_global_coverage_work_order_adds_registered_source_and_metric_gaps(self) -> None:
        case = coverage_case_for("BYDDF")
        matrix = source_coverage_matrix_for(case, [])
        audit = build_metric_resolution_audit("BYDDF", [], case)

        rows = global_coverage_work_order_items(case, matrix, audit)
        actions = [row[2] for row in rows]

        self.assertTrue(any("HKEX" in action for action in actions))
        self.assertTrue(any("Resolve canonical metric Revenue" in action for action in actions))
        self.assertTrue(all("arbitrary" not in row[3].lower() for row in rows))

    def test_work_order_accepts_global_coverage_inputs(self) -> None:
        case = coverage_case_for("BYDDF")
        matrix = source_coverage_matrix_for(case, [])
        audit = build_metric_resolution_audit("BYDDF", [], case)

        order = build_evidence_work_order(None, None, [], None, None, case, matrix, audit)

        self.assertIn(order.status, {"Blocks High-Conviction", "Blocks Research-Ready"})
        self.assertTrue(any(item.origin == "global_coverage" for item in order.items))
        self.assertTrue(any(item.acceptance_criteria for item in order.items))
        self.assertTrue(any(item.falsification_tests for item in order.items))

    def test_security_type_profile_preserves_adr_normalization_rules(self) -> None:
        profile = security_type_profile_for("ADR")

        self.assertTrue(any("ADR ratio" in item for item in profile.required_identity_checks))
        self.assertTrue(any("ordinary shares" in item for item in profile.normalization_rules))

    def test_demo_result_exposes_global_coverage_sections(self) -> None:
        result = demo_result("BABA")

        self.assertEqual(result.coverage_case.security_type, "ADR")
        self.assertTrue(result.source_coverage_matrix.entries)
        self.assertTrue(result.metric_resolution_audit.items)
        self.assertIn("Global Coverage and Metric Resolution", result.memo_markdown)


if __name__ == "__main__":
    unittest.main()
