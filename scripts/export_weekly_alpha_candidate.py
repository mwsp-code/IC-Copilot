"""Prepare a one-way, secret-free forecast candidate for private Weekly Alpha.

Offline only, no API credentials, no dispatch, no storage in public IC Copilot
checkout, no LLM invocation. The forecast producer must call this AFTER its own
source/evidence QA and record the first independent timestamp separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

SOURCE_REPO = "mwsp-code/IC-Copilot"
ALLOWED = {
    "kind", "security_id", "metric", "unit", "currency",
    "model_version", "rights", "extraction_method", "original_url",
    "source_sha256", "source_vintage_id", "public_release_utc",
    "first_observed_utc", "forecast_recorded_utc", "forecast_horizon_end_utc",
    "benchmark", "falsifier", "experiment_id", "event_group",
    "threshold", "forecast_probability", "baseline_probability",
    "baseline_method", "baseline_known_at_utc", "question",
    "instrument",
}
REQUIRED = ALLOWED - {"first_observed_utc"}


def utc(s):
    if not isinstance(s, str):
        raise ValueError("timestamp_missing")
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError("not_utc_qualified")
    return d.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,
                      separators=(",",":"),ensure_ascii=False,
                      allow_nan=False).encode()).hexdigest()


def create_event(source, *, cutoff_utc, producer_commit_sha, forecast_id,
                 version=1, supersedes_sha256=None, initial_forecast_sha256=None):
    if not isinstance(source,dict) or not REQUIRED.issubset(source):
        raise ValueError("required_forecast_fields_missing")
    if set(source)-ALLOWED:
        raise ValueError("unapproved_fields_or_secrets")
    if source["kind"]!="prospective_forecast":
        raise ValueError("forecast_only")
    if not isinstance(producer_commit_sha,str) or len(producer_commit_sha)!=40 or any(x not in "0123456789abcdef" for x in producer_commit_sha):
        raise ValueError("commit_sha_invalid")
    if not isinstance(forecast_id,str) or not forecast_id or len(forecast_id)>120:
        raise ValueError("forecast_id_invalid")
    if type(version) is not int or version<1:
        raise ValueError("version_invalid")
    cutoff=utc(cutoff_utc)
    public=utc(source["public_release_utc"])
    recorded=utc(source["forecast_recorded_utc"])
    baseline=utc(source["baseline_known_at_utc"])
    horizon=utc(source["forecast_horizon_end_utc"])
    if not public<=recorded<=cutoff<horizon or baseline>recorded:
        raise ValueError("forecast_looks_ahead_or_baseline_late")
    if source["rights"] not in ("user_authored", "licensed_transformed_metrics", "public_original_short_excerpt"):
        raise ValueError("source_retention_rights_unqualified")
    if not isinstance(source["original_url"],str) or not source["original_url"].startswith("https://"):
        raise ValueError("source_url_invalid")
    if not isinstance(source["source_sha256"],str) or len(source["source_sha256"])!=64:
        raise ValueError("source_identity_unqualified")
    for k in ("forecast_probability","baseline_probability"):
        x=source[k]
        if type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1:
            raise ValueError("forecast_probability_invalid")
    if type(source["threshold"]) not in (int,float) or not math.isfinite(source["threshold"]):
        raise ValueError("threshold_invalid")
    if version==1 and (supersedes_sha256 or initial_forecast_sha256):
        raise ValueError("initial_forecast_is_not_revision")
    if version>1 and (not supersedes_sha256 or not initial_forecast_sha256):
        raise ValueError("revision_lineage_required")
    body = {
        "schema_version":"weekly-alpha-ic-event-v1",
        "producer_repo":SOURCE_REPO,
        "producer_commit_sha":producer_commit_sha,
        "kind":"prospective_forecast", "forecast_id":forecast_id,
        "version":version, "information_cutoff_utc":cutoff.isoformat(),
        "record":{k:source[k] for k in sorted(source)},
        "record_sha256":digest(source),
    }
    if version>1:
        body["supersedes_sha256"]=supersedes_sha256
        body["initial_forecast_sha256"]=initial_forecast_sha256
    if len(json.dumps({"event_type":"ic_copilot_forecast_candidate_v1","client_payload":{"forecast_event":body}}).encode())>60000:
        raise ValueError("dispatch_payload_limit")
    return body


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--forecast-file",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--cutoff-utc",required=True)
    p.add_argument("--producer-commit-sha",required=True)
    p.add_argument("--forecast-id",required=True)
    args=p.parse_args()
    source=json.loads(args.forecast_file.read_text(encoding="utf-8"))
    event=create_event(source,cutoff_utc=args.cutoff_utc,
                       producer_commit_sha=args.producer_commit_sha,
                       forecast_id=args.forecast_id)
    root=Path(__file__).resolve().parents[1]
    target=args.output.resolve()
    if target==root or root in target.parents:
        raise ValueError("refuse_to_save_forecast_in_public_repository")
    if target.exists():
        raise ValueError("no_overwrite_first_forecast")
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(event,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("Sanitized candidate prepared outside public repository; NO dispatch or admission performed.")


if __name__=="__main__":
    main()
