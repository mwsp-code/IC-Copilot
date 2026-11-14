import unittest
from scripts.export_weekly_alpha_candidate import create_event
from scripts.dispatch_weekly_alpha_candidate import deliver,prepare,ENDPOINT
from tests.test_weekly_alpha_forecast_export import input_row

class DummyResponse:
    status=204
    def __enter__(self): return self
    def __exit__(self,*args): return False

class DispatchTests(unittest.TestCase):
    def event(self):
        return create_event(input_row(),cutoff_utc="2026-11-14T16:00:00Z",
                            producer_commit_sha="b"*40,forecast_id="SYN-1")
    def test_no_network_default(self):
        def fail(*args,**kwargs):raise AssertionError("should never call network")
        r=deliver(self.event(),token=None,opener=fail)
        self.assertEqual(r["state"],"DRY_RUN")
        self.assertEqual(r["api_calls"],0)
    def test_source_qa_gate(self):
        with self.assertRaises(ValueError):
            deliver(self.event(),token="f"*32,send=True,source_qa_confirmed=False)
    def test_secret_gate(self):
        with self.assertRaises(ValueError):
            deliver(self.event(),token=None,send=True,source_qa_confirmed=True)
    def test_mocked_event_correct_target_no_token_logging(self):
        seen={}
        def recorder(req,timeout):
            seen["url"]=req.full_url
            seen["body"]=req.data
            seen["timeout"]=timeout
            return DummyResponse()
        r=deliver(self.event(),token="f"*32,send=True,source_qa_confirmed=True,
                  opener=recorder)
        self.assertEqual(r["state"],"DISPATCH_ACCEPTED_NOT_REVIEWED")
        self.assertEqual(seen["url"],ENDPOINT)
        self.assertEqual(seen["timeout"],10)
        self.assertNotIn(b"ffffffff",seen["body"])
        self.assertIn(b"ic_copilot_forecast_candidate_v1",seen["body"])
    def test_source_unqualified_envelope_rejected(self):
        x=self.event();x["producer_repo"]="untrusted"
        with self.assertRaises(ValueError):prepare(x)

if __name__=="__main__":
    unittest.main()
