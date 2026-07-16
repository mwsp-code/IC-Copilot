from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from equity_research.consensus_import import import_consensus_csv, write_consensus_csv_templates
from equity_research.providers import CsvConsensusProvider
from equity_research.research_store import ResearchStore


class ConsensusImportTests(unittest.TestCase):
    def test_csv_import_seeds_point_in_time_target_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _write_csv(directory / "targets.csv", [
                {
                    "ticker": "BABA", "as_of": "2026-03-25", "provider": "Manual",
                    "official": "true", "currency": "USD", "target_mean": "100",
                    "target_kind": "mean", "target_label": "Mean target",
                },
                {
                    "ticker": "BABA", "as_of": "2026-05-25", "provider": "Manual",
                    "official": "true", "currency": "USD", "target_mean": "110",
                    "target_kind": "mean", "target_label": "Mean target",
                },
                {
                    "ticker": "BABA", "as_of": "2026-06-25", "provider": "Manual",
                    "official": "true", "currency": "USD", "target_mean": "121",
                    "target_kind": "mean", "target_label": "Mean target",
                },
            ])
            store = ResearchStore(directory / "research.db")
            result = import_consensus_csv(directory, ["BABA"], store)
            revisions = {item.window_days: item for item in store.revisions("BABA", provider="Manual")}

        self.assertEqual(result.imported, 1)
        self.assertEqual(result.rows_by_file["targets.csv"], 3)
        self.assertEqual(result.rows_by_ticker["BABA"]["targets.csv"], 3)
        self.assertGreaterEqual(result.revision_windows_available, 2)
        self.assertGreaterEqual(result.revision_windows_incomplete, 0)
        self.assertTrue(any("revision history is usable" in message for message in result.messages))
        self.assertAlmostEqual(revisions[30].change_pct, 10.0)
        self.assertAlmostEqual(revisions[90].change_pct, 21.0)

    def test_csv_import_preserves_provider_metadata_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _write_csv(directory / "provider_metadata.csv", [{
                "ticker": "BABA",
                "provider": "Manual Broker",
                "field": "target_mean",
                "observed_at": "2026-06-25T08:00:00+00:00",
                "source_as_of": "2026-06-25",
                "entitlement_status": "available",
                "provenance": "licensed_user_upload",
                "official": "true",
                "notes": "Internal licensed snapshot.",
            }])
            _write_csv(directory / "targets.csv", [{
                "ticker": "BABA", "as_of": "2026-06-25", "provider": "Manual Broker",
                "official": "true", "currency": "USD", "target_mean": "121",
                "target_kind": "mean", "target_label": "Mean target",
            }])
            store = ResearchStore(directory / "research.db")
            result = import_consensus_csv(directory, ["BABA"], store)
            observations = store.list_provider_observations("BABA", field="target_mean")

        self.assertEqual(result.metadata_observations, 1)
        self.assertTrue(any(item.provider == "Manual Broker" for item in observations))
        self.assertTrue(any(item.value_text == "Internal licensed snapshot." for item in observations))

    def test_csv_provider_preserves_aggregate_target_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _write_csv(directory / "targets.csv", [{
                "ticker": "AAPL", "as_of": "2026-06-20", "provider": "Manual",
                "target_aggregate": "250.5", "target_kind": "aggregate",
                "target_label": "Aggregate Target", "official": "true",
            }])
            package = CsvConsensusProvider(directory).fetch_package("AAPL", 200)

        self.assertEqual(package.target.target_kind, "aggregate")
        self.assertEqual(package.target.target_label, "Aggregate Target")
        self.assertEqual(package.target.target_aggregate, 250.5)
        self.assertIsNone(package.target.target_mean)

    def test_template_writer_creates_point_in_time_csv_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = write_consensus_csv_templates(directory, ticker="BABA")

            self.assertIn("targets.csv", result.files_written)
            self.assertIn("provider_metadata.csv", result.files_written)
            target_header = (directory / "targets.csv").read_text(encoding="utf-8").splitlines()[0]
            revision_header = (directory / "estimate_revisions.csv").read_text(encoding="utf-8").splitlines()[0]
            metadata_header = (directory / "provider_metadata.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("observed_at", target_header)
            self.assertIn("window_days", revision_header)
            self.assertIn("source_as_of", metadata_header)
            self.assertIn("BABA", (directory / "targets.csv").read_text(encoding="utf-8"))

            second = write_consensus_csv_templates(directory, ticker="BABA")

        self.assertIn("targets.csv", second.files_existing)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
