from __future__ import annotations

import unittest

from equity_research.analysis import (
    build_financial_metrics,
    compare_filing_pair,
    extract_key_sections,
    financial_change_events,
    html_to_text,
    keyword_count,
)
from equity_research.models import FinancialMetric, FilingRecord


class AnalysisTests(unittest.TestCase):
    def test_html_to_text_removes_tables_and_scripts(self) -> None:
        raw = """
        <html><body><script>ignore me</script><p>Revenue improved.</p>
        <table><tr><td>table noise</td></tr></table><p>Margin expanded.</p></body></html>
        """
        text = html_to_text(raw)
        self.assertIn("Revenue improved", text)
        self.assertIn("Margin expanded", text)
        self.assertNotIn("ignore me", text)
        self.assertNotIn("table noise", text)

    def test_keyword_count_is_case_insensitive(self) -> None:
        self.assertEqual(keyword_count("Debt debt DEBT", ["debt"]), 3)

    def test_build_financial_metrics_extracts_latest_and_previous(self) -> None:
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "val": 100,
                                    "end": "2024-12-31",
                                    "filed": "2025-02-01",
                                    "form": "10-K",
                                    "fp": "FY",
                                    "fy": 2024,
                                },
                                {
                                    "val": 125,
                                    "end": "2025-12-31",
                                    "filed": "2026-02-01",
                                    "form": "10-K",
                                    "fp": "FY",
                                    "fy": 2025,
                                },
                            ]
                        }
                    }
                }
            }
        }
        metrics = build_financial_metrics(facts)
        revenue = next(metric for metric in metrics if metric.name == "Revenue")
        self.assertEqual(revenue.value, 125)
        self.assertEqual(revenue.previous_value, 100)
        self.assertAlmostEqual(revenue.yoy_change_pct or 0, 25)

    def test_build_financial_metrics_supports_ifrs_20f(self) -> None:
        facts = {
            "facts": {
                "ifrs-full": {
                    "Revenue": {
                        "units": {
                            "CNY": [
                                {
                                    "val": 100,
                                    "end": "2024-03-31",
                                    "filed": "2024-07-01",
                                    "form": "20-F",
                                    "fp": "FY",
                                    "fy": 2024,
                                },
                                {
                                    "val": 112,
                                    "end": "2025-03-31",
                                    "filed": "2025-07-01",
                                    "form": "20-F",
                                    "fp": "FY",
                                    "fy": 2025,
                                },
                            ]
                        }
                    }
                }
            }
        }
        metrics = build_financial_metrics(facts)
        revenue = next(metric for metric in metrics if metric.name == "Revenue")
        self.assertEqual(revenue.unit, "CNY")
        self.assertEqual(revenue.form, "20-F")
        self.assertAlmostEqual(revenue.yoy_change_pct or 0, 12)

    def test_build_financial_metrics_derives_gross_profit_and_margin_from_cost_of_revenue(self) -> None:
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 80, "end": "2025-03-31", "filed": "2025-04-25", "form": "10-Q", "fp": "Q1", "fy": 2025},
                                {"val": 100, "end": "2026-03-31", "filed": "2026-04-25", "form": "10-Q", "fp": "Q1", "fy": 2026},
                            ]
                        }
                    },
                    "CostOfRevenue": {
                        "units": {
                            "USD": [
                                {"val": 50, "end": "2025-03-31", "filed": "2025-04-25", "form": "10-Q", "fp": "Q1", "fy": 2025},
                                {"val": 60, "end": "2026-03-31", "filed": "2026-04-25", "form": "10-Q", "fp": "Q1", "fy": 2026},
                            ]
                        }
                    },
                }
            }
        }

        metrics = build_financial_metrics(facts)
        by_name = {metric.name: metric for metric in metrics}

        self.assertEqual(by_name["Gross Profit"].source_kind, "derived_companyfacts")
        self.assertEqual(by_name["Gross Profit"].value, 40)
        self.assertEqual(by_name["Gross Margin"].value, 40)

    def test_build_financial_metrics_uses_fresh_alias_when_first_concept_is_stale(self) -> None:
        facts = {
            "facts": {
                "us-gaap": {
                    "PaymentsToAcquirePropertyPlantAndEquipment": {
                        "units": {
                            "USD": [
                                {"val": 10, "end": "2020-03-31", "filed": "2020-04-25", "form": "10-Q", "fp": "Q1", "fy": 2020},
                            ]
                        }
                    },
                    "CapitalExpenditures": {
                        "units": {
                            "USD": [
                                {"val": 80, "end": "2025-03-31", "filed": "2025-04-25", "form": "10-Q", "fp": "Q1", "fy": 2025},
                                {"val": 120, "end": "2026-03-31", "filed": "2026-04-25", "form": "10-Q", "fp": "Q1", "fy": 2026},
                            ]
                        }
                    },
                    "ShortTermBorrowings": {
                        "units": {
                            "USD": [
                                {"val": 1, "end": "2018-06-30", "filed": "2018-08-03", "form": "10-K", "fp": "FY", "fy": 2018},
                            ]
                        }
                    },
                    "DebtCurrent": {
                        "units": {
                            "USD": [
                                {"val": 40, "end": "2025-03-31", "filed": "2025-04-25", "form": "10-Q", "fp": "Q1", "fy": 2025},
                                {"val": 50, "end": "2026-03-31", "filed": "2026-04-25", "form": "10-Q", "fp": "Q1", "fy": 2026},
                            ]
                        }
                    },
                    "InterestExpense": {
                        "units": {
                            "USD": [
                                {"val": 4, "end": "2024-03-31", "filed": "2024-04-25", "form": "10-Q", "fp": "Q1", "fy": 2024},
                            ]
                        }
                    },
                    "InterestExpenseNonoperating": {
                        "units": {
                            "USD": [
                                {"val": 5, "end": "2025-03-31", "filed": "2025-04-25", "form": "10-Q", "fp": "Q1", "fy": 2025},
                                {"val": 7, "end": "2026-03-31", "filed": "2026-04-25", "form": "10-Q", "fp": "Q1", "fy": 2026},
                            ]
                        }
                    },
                }
            }
        }

        metrics = build_financial_metrics(facts)
        by_name = {metric.name: metric for metric in metrics}

        self.assertEqual(by_name["Capital Expenditure"].period_end, "2026-03-31")
        self.assertEqual(by_name["Capital Expenditure"].value, 120)
        self.assertEqual(by_name["Current Debt"].period_end, "2026-03-31")
        self.assertEqual(by_name["Current Debt"].value, 50)
        self.assertEqual(by_name["Interest Expense"].period_end, "2026-03-31")
        self.assertEqual(by_name["Interest Expense"].value, 7)

    def test_large_share_count_change_requires_basis_normalization(self) -> None:
        events = financial_change_events(
            [
                FinancialMetric(
                    "Shares", 1_900_000_000, "shares", "2026-03-31",
                    previous_value=18_500_000_000,
                    yoy_change_pct=-89.7,
                    filed="2026-06-30",
                    form="20-F",
                    source_url="https://www.sec.gov/example",
                )
            ],
            "https://data.sec.gov/companyfacts/example.json",
        )

        self.assertEqual(events[0].title, "Shares basis requires normalization")
        self.assertEqual(events[0].direction, "neutral")
        self.assertTrue(events[0].metrics["normalization_required"])
        self.assertIn("ADR ratio", events[0].metrics["normalization_reason"])

    def test_extract_key_sections_supports_20f_items(self) -> None:
        filing = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-07-01",
            report_date="2026-03-31",
            primary_doc="baba-20f.htm",
            description="Annual report",
            url="https://example.com/baba-20f.htm",
        )
        text = """
        Item 3.D. Risk Factors
        Our business faces regulatory risks and customer concentration risk.
        """ + ("Risk detail. " * 100) + """
        Item 4. Information on the Company
        Item 5. Operating and Financial Review and Prospects
        Revenue grew while operating margin improved.
        """ + ("MD&A detail. " * 100) + """
        Item 6. Directors, Senior Management and Employees
        """
        sections = extract_key_sections(text, filing)
        self.assertIn("risk_factors", sections)
        self.assertIn("mda", sections)

    def test_forward_looking_boilerplate_does_not_generate_guidance_event(self) -> None:
        filing = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-07-01",
            report_date="2026-03-31",
            primary_doc="baba-20f.htm",
            description="Annual report",
            url="https://example.com/baba-20f.htm",
        )
        text = """
        Forward-looking statements may be identified by words or phrases such as may, will,
        expect, anticipate, future, target, guidance, outlook and other similar expressions.
        The forward-looking statements included in this annual report relate to our strategies.
        An uncertain economic outlook could have a material adverse effect.
        """ * 8
        events = compare_filing_pair(filing, text, None, None)
        self.assertFalse(any(event.category == "guidance" for event in events))

    def test_substantive_numeric_guidance_generates_guidance_event(self) -> None:
        filing = FilingRecord(
            form="8-K",
            accession="0000000000-26-000002",
            filing_date="2026-07-02",
            report_date="2026-06-30",
            primary_doc="earnings.htm",
            description="Earnings release",
            url="https://example.com/earnings.htm",
        )
        text = "Management guidance: we expect revenue between $10 billion and $12 billion for the next quarter. " * 4
        events = compare_filing_pair(filing, text, None, None)
        guidance = [event for event in events if event.category == "guidance"]
        self.assertEqual(len(guidance), 1)
        self.assertEqual(guidance[0].metrics["guidance_metric"], "Revenue")

    def test_keyword_language_without_prior_is_detected_not_moved(self) -> None:
        filing = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-05-20",
            report_date="2026-03-31",
            primary_doc="baba-20f.htm",
            description="Annual report",
            url="https://example.com/baba-20f.htm",
        )
        text = "Debt maturity liquidity refinancing covenant interest expense risk. " * 4

        events = compare_filing_pair(filing, text, None, None)
        debt = next(event for event in events if event.category == "debt_liquidity")

        self.assertEqual(debt.title, "Debt Liquidity discussion detected")
        self.assertEqual(debt.metrics["signal_method"], "disclosure_change_engine")
        self.assertEqual(debt.metrics["disclosure_event_type"], "observation")
        self.assertEqual(debt.metrics["comparison_status"], "no_comparable_prior")
        self.assertEqual(debt.metrics["comparison_reason_code"], "prior_filing_missing")
        self.assertIsNone(debt.metrics["previous_mentions"])
        audit = debt.metrics["prior_context_audit"]
        self.assertEqual(audit["status"], "prior_not_attempted")
        self.assertIn("prior_not_attempted", audit["stage_history"])
        self.assertFalse(audit["zero_mentions_is_valid"])
        self.assertIn("issuer_ir_report", audit["fallback_source_types"])
        self.assertTrue(audit["contextual_comparison_eligible"])
        self.assertEqual(debt.metrics["current_period"], "2026-03-31")
        self.assertIn("cannot determine whether disclosure increased", debt.summary)
        self.assertIn("Retrieve the previous comparable", debt.metrics["research_work_order"])

    def test_keyword_language_same_source_comparison_is_marked_invalid(self) -> None:
        current = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-05-20",
            report_date="2026-03-31",
            primary_doc="baba-20f.htm",
            description="Annual report",
            url="https://example.com/baba-20f.htm",
        )
        previous = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2025-05-20",
            report_date="2025-03-31",
            primary_doc="baba-20f.htm",
            description="Annual report",
            url="https://example.com/baba-20f.htm",
        )
        text = "Debt maturity liquidity refinancing covenant interest expense risk. " * 4

        events = compare_filing_pair(current, text, previous, text)
        debt = next(event for event in events if event.category == "debt_liquidity")

        self.assertEqual(debt.metrics["comparison_status"], "invalid_same_source")
        self.assertEqual(debt.metrics["comparison_reason_code"], "same_document_detected")
        self.assertIsNone(debt.metrics["previous_mentions"])
        self.assertIn("same accession", debt.summary)

    def test_baba_like_prior_debt_discussion_is_compared_when_available(self) -> None:
        current = FilingRecord(
            form="20-F",
            accession="0001193125-26-231755",
            filing_date="2026-05-20",
            report_date="2026-03-31",
            primary_doc="baba-20260331.htm",
            description="Annual report",
            url="https://example.com/baba-20260331.htm",
        )
        previous = FilingRecord(
            form="20-F",
            accession="0000950170-25-090161",
            filing_date="2025-06-26",
            report_date="2025-03-31",
            primary_doc="baba-20250331.htm",
            description="Annual report",
            url="https://example.com/baba-20250331.htm",
        )
        current_text = """
        Item 5. Operating and Financial Review and Prospects
        Liquidity resources include cash, credit facility availability and debt maturity management.
        We monitor refinancing risk, covenant flexibility and offshore liquidity for capital allocation.
        Debt liquidity covenant refinancing maturity risk. Debt liquidity covenant refinancing maturity risk.
        Item 6. Directors, Senior Management and Employees
        """
        previous_text = """
        Item 5. Operating and Financial Review and Prospects
        Liquidity resources include cash and debt maturity management.
        We monitor refinancing risk and covenant flexibility.
        Debt liquidity covenant refinancing maturity risk.
        Item 6. Directors, Senior Management and Employees
        """

        events = compare_filing_pair(current, current_text, previous, previous_text)
        debt = next(event for event in events if event.category == "debt_liquidity")

        self.assertIn(debt.metrics["comparison_status"], {"period_aligned", "comparable_imperfect"})
        self.assertEqual(debt.metrics["previous_mentions"], debt.metrics["prior_mentions"])
        self.assertGreater(debt.metrics["previous_mentions"], 0)
        self.assertGreater(debt.metrics["current_mentions_per_1000_words"], 0)
        self.assertGreater(debt.metrics["prior_mentions_per_1000_words"], 0)
        self.assertIn("mentions per 1,000 words", debt.summary)
        self.assertNotIn("versus 0", debt.summary)

    def test_prior_text_without_aligned_section_becomes_work_order_not_zero(self) -> None:
        current = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-05-20",
            report_date="2026-03-31",
            primary_doc="current.htm",
            description="Annual report",
            url="https://example.com/current.htm",
        )
        previous = FilingRecord(
            form="20-F",
            accession="0000000000-25-000001",
            filing_date="2025-05-20",
            report_date="2025-03-31",
            primary_doc="previous.htm",
            description="Annual report",
            url="https://example.com/previous.htm",
        )
        current_text = """
        Item 5. Operating and Financial Review and Prospects
        Debt liquidity covenant refinancing maturity risk. Debt liquidity covenant refinancing maturity risk.
        Item 6. Directors, Senior Management and Employees
        """
        previous_text = "This prior filing text is available, but no comparable capital resources section is extracted."

        events = compare_filing_pair(current, current_text, previous, previous_text)
        debt = next(event for event in events if event.category == "debt_liquidity")

        self.assertEqual(debt.metrics["comparison_status"], "no_comparable_prior")
        self.assertEqual(debt.metrics["comparison_reason_code"], "prior_section_missing")
        self.assertIsNone(debt.metrics["previous_mentions"])
        self.assertIn("align the prior 20-F debt liquidity section", debt.metrics["research_work_order"])

    def test_zero_prior_mentions_requires_loaded_aligned_section(self) -> None:
        current = FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-05-20",
            report_date="2026-03-31",
            primary_doc="current.htm",
            description="Annual report",
            url="https://example.com/current.htm",
        )
        previous = FilingRecord(
            form="20-F",
            accession="0000000000-25-000001",
            filing_date="2025-05-20",
            report_date="2025-03-31",
            primary_doc="previous.htm",
            description="Annual report",
            url="https://example.com/previous.htm",
        )
        current_text = """
        Item 5. Operating and Financial Review and Prospects
        Debt maturity liquidity refinancing covenant interest expense risk. Debt maturity liquidity refinancing covenant risk.
        """ + ("Debt maturity planning and liquidity resources support operating investment. " * 18) + """
        Item 6. Directors, Senior Management and Employees
        """
        previous_text = """
        Item 5. Operating and Financial Review and Prospects
        Operating cash flow funded investment and ordinary capital allocation during the period.
        """ + ("Operating performance funded investment and ordinary capital allocation during the period. " * 18) + """
        Item 6. Directors, Senior Management and Employees
        """

        events = compare_filing_pair(current, current_text, previous, previous_text)
        debt = next(event for event in events if event.category == "debt_liquidity")
        audit = debt.metrics["prior_context_audit"]

        self.assertEqual(debt.metrics["previous_mentions"], 0)
        self.assertEqual(audit["status"], "prior_loaded_zero_mentions")
        self.assertTrue(audit["text_loaded"])
        self.assertTrue(audit["text_parsed"])
        self.assertTrue(audit["section_matched"])
        self.assertTrue(audit["zero_mentions_is_valid"])
        self.assertTrue(audit["llm_comparison_ready"])
        self.assertIn("prior_loaded_no_mentions", audit["stage_history"])
        self.assertEqual(debt.metrics["alignment_type"], "same_section")
        self.assertTrue(debt.metrics["current_excerpt"])
        self.assertTrue(debt.metrics["prior_excerpt"])
        self.assertGreaterEqual(len(debt.citations), 2)

    def test_prior_context_audit_distinguishes_empty_fetch_and_parse_failure(self) -> None:
        current = FilingRecord(
            form="20-F", accession="current", filing_date="2026-05-20",
            report_date="2026-03-31", primary_doc="current.htm",
            description="Annual report", url="https://example.com/current",
        )
        previous = FilingRecord(
            form="20-F", accession="prior", filing_date="2025-05-20",
            report_date="2025-03-31", primary_doc="prior.htm",
            description="Annual report", url="https://example.com/prior",
        )
        current_text = "Debt liquidity maturity covenant refinancing risk. " * 5

        empty = compare_filing_pair(current, current_text, previous, None)
        empty_audit = next(item for item in empty if item.category == "debt_liquidity").metrics["prior_context_audit"]
        self.assertEqual(empty_audit["status"], "prior_text_empty")
        self.assertIn("prior_document_found", empty_audit["stage_history"])
        self.assertIn("prior_text_empty", empty_audit["stage_history"])

        failed = compare_filing_pair(
            current, current_text, previous, None,
            prior_search_audit={"search_attempted": True, "discovery_error": "timeout"},
        )
        failed_audit = next(item for item in failed if item.category == "debt_liquidity").metrics["prior_context_audit"]
        self.assertEqual(failed_audit["status"], "prior_fetch_failed")

        parse_failed = compare_filing_pair(
            current, current_text, previous, None,
            prior_search_audit={"search_attempted": True, "parse_failed": True},
        )
        parse_audit = next(item for item in parse_failed if item.category == "debt_liquidity").metrics["prior_context_audit"]
        self.assertEqual(parse_audit["status"], "prior_parse_failed")
        self.assertIn("prior_parse_failed", parse_audit["stage_history"])


if __name__ == "__main__":
    unittest.main()
