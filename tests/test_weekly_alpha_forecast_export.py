"""Offline, synthetic-only one-way public-to-private contract tests."""
from copy import deepcopy
import unittest
from scripts.export_weekly_alpha_candidate import create_event,digest


def input_row():
    return {
      "kind":"prospective_forecast","security_id":"TEST.SYN","metric":"operating_margin",
      "unit":"pct","currency":"USD","model_version":"synthetic-1",
      "rights":"user_authored","extraction_method":"manual_review",
      "original_url":"https://example.org/synthetic-original",
      "source_sha256":"a"*64,"source_vintage_id":"synth-q2",
      "public_release_utc":"2026-11-12T13:00:00Z",
      "first_observed_utc":"2026-11-12T15:00:00Z",
      "forecast_recorded_utc":"2026-11-14T16:00:00Z",
      "forecast_horizon_end_utc":"2027-05-14T16:00:00Z",
      "benchmark":"dated market mean",
      "falsifier":"margin is below 10 percent",
      "experiment_id":"synth-exp",
      "event_group":"synth-quarter",
      "threshold":10,
      "forecast_probability":0.65,
      "baseline_probability":0.50,
      "baseline_method":"dated-naive",
      "baseline_known_at_utc":"2026-11-13T12:00:00Z",
      "question":"Will margin exceed 10 percent?",
      "instrument":"TEST.SYN"}

def event(row=None):
    return create_event(input_row() if row is None else row,
        cutoff_utc="2026-11-14T16:00:00Z",
        producer_commit_sha="b"*40,
        forecast_id="SYN-1")


class ExportContractTests(unittest.TestCase):
    def test_prepare_no_transfer(self):
        v=event()
        self.assertEqual(v["record_sha256"],digest(v["record"]))
        self.assertNotIn("secret",v)
        self.assertEqual(v["producer_repo"],"mwsp-code/IC-Copilot")
        self.assertEqual(v["version"],1)
    def test_no_secret(self):
        x=input_row();x["api_key"]="bad"
        with self.assertRaisesRegex(ValueError,"unapproved_fields"):event(x)
    def test_baseline_future(self):
        x=input_row();x["baseline_known_at_utc"]="2026-11-15T12:00:00Z"
        with self.assertRaisesRegex(ValueError,"baseline_late"):event(x)
    def test_record_after_cutoff(self):
        x=input_row();x["forecast_recorded_utc"]="2026-11-15T12:00:00Z"
        with self.assertRaises(ValueError):event(x)
    def test_invalid_probability(self):
        for p in (1.1,-0.2,float("nan"),True):
            x=input_row();x["forecast_probability"]=p
            with self.subTest(p=p),self.assertRaises(ValueError):event(x)
    def test_initial_not_revision(self):
        with self.assertRaises(ValueError):
            create_event(input_row(),cutoff_utc="2026-11-14T16:00:00Z",
                         producer_commit_sha="b"*40,forecast_id="SYN-1",
                         initial_forecast_sha256="a"*64)
    def test_unlicensed_source(self):
        x=input_row();x["rights"]="scraped_transcript_unverified"
        with self.assertRaises(ValueError):event(x)


if __name__=="__main__":
    unittest.main()
