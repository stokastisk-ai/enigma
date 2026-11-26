"""Live, bounded verification of the Kaggle diagnostic credential against the REAL deployed AWS diagnostic Lambda (no mocks).

    AWS_PROFILE=<sso-profile> python3.13 scripts/verify_kaggle_diagnostic_credentials.py [--key-file .enigma-local/ENIGMA_DIAG_KEY.txt]

What it does (each step prints only codes, counts and lengths, never a credential, key id fragment or response body):
  1. Reads the diagnostic key from the private local file and checks it with the SAME classifier the notebooks use (scripts/kaggle_cells).
  2. Checks the file is 0600 and git-ignored.
  3. Sends ONE synthetic event (unique attempt id ``verify-<UTC>-<rand>``, seq 1) with the notebook transport (same headers, no redirects).
  4. Resends the identical event: it must be acknowledged as a duplicate (durable record, no overwrite).
  5. Sends two synthetic invalid credentials (correct shape, wrong secret / malformed): each must get 401 exactly once (no retries).
  6. Reads the stored DynamoDB item back (boto3, the caller's SSO profile) and finds the CloudWatch line for the attempt.
Uses 2 events of the key's quota. It never submits a runtime token or any AWS credential to the endpoint."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_kaggle_diagnostic_notebooks import DIAG_URL  # noqa: E402

REGION, TABLE, LOG_GROUP = "us-east-1", "enigma-diag", "/aws/lambda/enigma-diag-ingest"


def load_cell() -> dict:
    """The notebooks' exact credential + transport code, with a local stand-in for mark()/verdict()."""
    ns: dict = {"mark": lambda name, **f: {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "t": 0.0, "pid": os.getpid(),
                                           "pgid": os.getpgid(0), "sid": os.getsid(0), **f},
                "verdict": lambda *a, **k: None, "NOTEBOOK": "verify_kaggle_diagnostic_credentials", "EXPERIMENT": "live-verification"}
    src = (ROOT / "scripts" / "kaggle_cells" / "diag_credentials.py").read_text().replace("__DIAG__", DIAG_URL)
    exec(compile(src, "diag_credentials.py", "exec"), ns)
    return ns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-file", default=str(ROOT / ".enigma-local" / "ENIGMA_DIAG_KEY.txt"))
    a = ap.parse_args()
    results: dict = {}
    ns = load_cell()
    kf = Path(a.key_file)
    if not kf.is_file():
        print(json.dumps({"LOCAL_DIAGNOSTIC_KEY_FILE": "MISSING"})); return 2
    raw = kf.read_text()
    code, facts = ns["classify_secret"]("ENIGMA_DIAG_KEY", raw)
    mode = stat.S_IMODE(kf.stat().st_mode)
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(kf)]).returncode == 0
    results["key_file"] = {"code": code, "length": facts.get("actual_length"), "category": facts.get("supplied_category"), "mode": oct(mode),
                           "git_ignored": ignored, "embedded_whitespace": facts.get("embedded_whitespace"), "quoted": facts.get("quoted"),
                           "assignment": facts.get("looks_like_assignment")}
    if code != "SECRET_OK" or mode & 0o077 or not ignored:
        print(json.dumps(results, indent=1)); return 2
    key = raw.strip()
    del raw

    attempt = time.strftime("verify-%Y%m%dT%H%M%SZ-", time.gmtime()) + secrets.token_hex(4)
    ns["ATTEMPT"] = attempt
    ns["_secret_client"] = lambda: type("C", (), {"get_secret": staticmethod(lambda n: key if n == "ENIGMA_DIAG_KEY" else None)})()
    first = ns["rmark"]("live_verification", stage="verify", phase="probe", note="bounded live credential verification")
    dup = ns["resubmit_last"]()
    results["live"] = {"attempt": attempt, "first": {k: first.get(k) for k in ("code", "http", "stored", "duplicates", "attempts", "latency_s")},
                       "resubmission": {k: dup.get(k) for k in ("code", "http", "stored", "duplicates", "attempts")}}

    # Negative tests: synthetic credentials only (correct shape with a wrong secret; malformed). One request each, never retried.
    neg = {}
    for label, fake in (("wrong_secret", "edx_zz00zz00_" + "N" * 43), ("malformed", "edx_short")):
        r = ns["post_events"]([{"attempt": attempt, "nb": "verify", "seq": 99, "marker": "negative", "utc": "2026-01-01T00:00:00Z"}], fake)
        neg[label] = {k: r.get(k) for k in ("code", "http", "server_error", "attempts")}
    results["negative"] = neg

    import boto3
    sess = boto3.session.Session(region_name=REGION)
    results["caller"] = sess.client("sts").get_caller_identity()["Arn"].split("/")[-2]          # role name only
    item = sess.client("dynamodb").get_item(TableName=TABLE, ConsistentRead=True,
                                            Key={"PK": {"S": f"ATT#{attempt}"}, "SK": {"S": "EVT#00000001"}}).get("Item") or {}
    results["dynamodb"] = {"found": bool(item), "marker": item.get("marker", {}).get("S"), "seq": item.get("seq", {}).get("N"),
                           "ttl_set": "ttl" in item, "fields": sorted(item)}
    logs, lines = sess.client("logs"), []
    for _ in range(12):                                                    # CloudWatch ingestion lag: bounded wait (<= ~60 s)
        ev = logs.filter_log_events(logGroupName=LOG_GROUP, startTime=int((time.time() - 900) * 1000), filterPattern=f'"{attempt}"').get("events", [])
        lines = [json.loads(e["message"]) for e in ev if e["message"].lstrip().startswith("{")]
        if len(lines) >= 2:
            break
        time.sleep(5)
    results["cloudwatch"] = {"lines": len(lines), "stored": [l.get("stored") for l in lines], "duplicates": [l.get("duplicates") for l in lines],
                             "key_in_log": any(key in json.dumps(l) for l in lines)}
    ok = (first and first.get("stored") == 1 and dup and dup.get("duplicates") == 1 and results["dynamodb"]["found"]
          and neg["wrong_secret"] == {"code": "DIAGNOSTIC_AUTH_REJECTED", "http": 401, "server_error": "unauthorized", "attempts": 1}
          and neg["malformed"]["http"] == 401 and neg["malformed"]["attempts"] == 1 and len(lines) >= 2 and not results["cloudwatch"]["key_in_log"])
    results["VERDICT"] = "VERIFIED" if ok else "FAILED"
    out = json.dumps(results, indent=1)
    assert key not in out
    print(out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
