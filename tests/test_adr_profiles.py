from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research.adr_profiles import adr_profile_for, issuer_ir_sources_for


class AdrProfileTests(unittest.TestCase):
    def test_seeded_major_adr_profile_has_ratio_and_sources(self) -> None:
        profile = adr_profile_for("BABA")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.ordinary_share_ratio, 8.0)
        self.assertEqual(profile.home_exchange, "HKEX")
        self.assertIn("China commerce", profile.segment_drivers)
        self.assertIn("KWEB", profile.benchmark_tickers)
        self.assertEqual(profile.fx_proxy, "CNH")
        self.assertTrue(issuer_ir_sources_for("BABA"))

    def test_generic_fpi_profile_for_twenty_f_or_six_k(self) -> None:
        profile = adr_profile_for("XYZ", ["20-F", "6-K"])
        self.assertIsNotNone(profile)
        self.assertEqual(profile.source, "generic_fpi")
        self.assertEqual(profile.ordinary_share_ratio, 1.0)
        self.assertIn("Home-market demand", profile.segment_drivers)

    def test_csv_profile_overrides_built_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "adr_profiles.csv"
            path.write_text(
                "ticker,home_exchange,ordinary_share_ratio,reporting_currency,fiscal_year_end,primary_forms,issuer_ir_sources,segment_drivers,benchmark_tickers,fx_proxy,source_priority\n"
                "BABA,HKEX,4,CNY,03-31,\"20-F,6-K\",quarterly_results=https://example.test/ir,Cloud|Buybacks,KWEB|MCHI,CNH,\"6-K,issuer_ir\"\n",
                encoding="utf-8",
            )
            profile = adr_profile_for("BABA", csv_path=path)
            self.assertEqual(profile.ordinary_share_ratio, 4.0)
            self.assertEqual(profile.issuer_ir_sources[0][1], "https://example.test/ir")
            self.assertEqual(profile.segment_drivers, ("Cloud", "Buybacks"))
            self.assertEqual(profile.benchmark_tickers, ("KWEB", "MCHI"))

    def test_seeded_csv_covers_large_china_and_global_adrs(self) -> None:
        china_adrs = ["BABA", "JD", "PDD", "BIDU", "NTES", "TCOM", "NIO", "XPEV", "LI", "YUMC"]
        global_adrs = ["TSM", "ASML", "NVO", "INFY", "HDB", "TM", "SONY", "PBR", "VALE"]

        for ticker in china_adrs:
            profile = adr_profile_for(ticker)
            self.assertIsNotNone(profile, ticker)
            self.assertIn("CNY", {profile.reporting_currency, profile.fx_proxy or "CNY"}, ticker)
            self.assertTrue(profile.segment_drivers, ticker)
            self.assertTrue(profile.benchmark_tickers, ticker)
            self.assertIn("issuer_ir", profile.source_priority, ticker)

        for ticker in global_adrs:
            profile = adr_profile_for(ticker)
            self.assertIsNotNone(profile, ticker)
            self.assertTrue(profile.segment_drivers, ticker)
            self.assertTrue(profile.issuer_ir_sources, ticker)


if __name__ == "__main__":
    unittest.main()
