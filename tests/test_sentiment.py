from __future__ import annotations

import unittest

from equity_research.sentiment import score_text
from equity_research.rigor import compare_transcripts


class SentimentTests(unittest.TestCase):
    def test_constructive_finance_language_scores_positive(self) -> None:
        result = score_text("Cloud growth accelerated and gross margin expanded by 250 basis points.")
        self.assertEqual(result.label, "Constructive")
        self.assertGreater(result.score, 0)
        self.assertIn("growth", result.positive_terms)
        self.assertGreaterEqual(result.specificity_score, 2)

    def test_negative_language_scores_negative(self) -> None:
        result = score_text("Demand softened, competition increased, and margin pressure remains a headwind.")
        self.assertEqual(result.label, "Negative")
        self.assertLess(result.score, 0)

    def test_uncertainty_language_scores_cautious(self) -> None:
        result = score_text("The macro environment remains uncertain and difficult to forecast.")
        self.assertEqual(result.label, "Cautious")
        self.assertTrue(result.uncertainty_terms)

    def test_evasive_language_scores_evasive(self) -> None:
        result = score_text("We do not disclose that metric and are not prepared to comment.")
        self.assertEqual(result.label, "Evasive")
        self.assertTrue(result.evasion_terms)

    def test_promotional_language_is_flagged(self) -> None:
        result = score_text("This is a transformational and game changing massive opportunity.")
        self.assertEqual(result.label, "Promotional")
        self.assertTrue(result.promotional_terms)

    def test_neutral_language_is_neutral(self) -> None:
        result = score_text("Thank you for joining the call today.")
        self.assertEqual(result.label, "Neutral")

    def test_transcript_comparison_reports_tone_shifts(self) -> None:
        comparison = compare_transcripts([
            {
                "period": "current",
                "text": "Cloud growth accelerated by 30 percent. We do not disclose customer metrics.",
            },
            {
                "period": "prior",
                "text": "Macro uncertainty remains challenging and demand softened.",
            },
        ])
        self.assertEqual(comparison.status, "Available")
        self.assertIsNotNone(comparison.sentiment_shift)
        self.assertIsNotNone(comparison.evasion_shift)
        self.assertTrue(comparison.tone_shift_summary)


if __name__ == "__main__":
    unittest.main()
