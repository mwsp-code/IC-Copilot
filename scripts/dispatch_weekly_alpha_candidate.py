"""Optional explicit GitHub event delivery after a genuinely finalized forecast.

The public repository never receives a credential. Dry run by default; sending
requires both an authorized source-reviewed release invocation and a scoped
secret in the trusted producing runner. This does not trigger on its own.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

ENDPOINT = "https://api.github.com/repos/mwsp-code/weekly-market-research-data/dispatches"


def prepare(envelope):
    if not isinstance(envelope,dict) or envelope.get("schema_version")!="weekly-alpha-ic-event-v1":
        raise ValueError("invalid_envelope")
    if envelope.get("producer_repo")!="mwsp-code/IC-Copilot":
        raise ValueError("untrusted_producer")
    if envelope.get("kind")!="prospective_forecast" or envelope.get("version") is None:
        raise ValueError("forecast_identity_missing")
    data = {"event_type":"ic_copilot_forecast_candidate_v1",
            "client_payload":{"forecast_event":envelope}}
    blob=json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode("utf-8")
    if len(blob)>60000:
        raise ValueError("dispatch_payload_exceeds_limit")
    return blob


def deliver(envelope, *, token, source_qa_confirmed=False, send=False, opener=urlopen):
    body=prepare(envelope)
    if not send:
        return {"state":"DRY_RUN","payload_bytes":len(body),"api_calls":0}
    if not source_qa_confirmed:
        raise ValueError("source_qa_gate_not_approved")
    if not isinstance(token,str) or len(token)<20:
        raise ValueError("narrowly_scoped_secret_not_connected")
    req=Request(ENDPOINT,data=body,headers={
      "Accept":"application/vnd.github+json",
      "Authorization":"Bearer "+token,
      "X-GitHub-Api-Version":"2022-11-28",
      "User-Agent":"IC-Copilot-private-research-bridge"},
      method="POST")
    # GitHub returns 204 for an accepted event; no forecast accepted by receiver
    # is implied. Do not log token, body, or server exception response headers.
    with opener(req,timeout=10) as resp:
        status=resp.status
    if status != 204:
        raise ValueError("github_dispatch_not_accepted")
    return {"state":"DISPATCH_ACCEPTED_NOT_REVIEWED","payload_bytes":len(body),"api_calls":1}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--envelope-file",required=True,type=Path)
    p.add_argument("--send",action="store_true")
    p.add_argument("--source-qa-confirmed",action="store_true")
    args=p.parse_args()
    env=json.loads(args.envelope_file.read_text(encoding="utf-8"))
    result=deliver(env,token=os.environ.get("WEEKLY_ALPHA_RESEARCH_DISPATCH_TOKEN"),
                   source_qa_confirmed=args.source_qa_confirmed,send=args.send)
    print(result["state"]+"; endpoint execution="+str(result["api_calls"]))


if __name__=="__main__":
    main()
