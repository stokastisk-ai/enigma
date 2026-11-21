"""READ-ONLY pre-flight for the Enigma CloudWatch dashboard (infra/aws/dashboards/enigma-control-plane.json).

    /usr/local/bin/python3.13 scripts/check_dashboard_quota.py            # offline: validates the draft, prints the procedure, calls NOTHING
    /usr/local/bin/python3.13 scripts/check_dashboard_quota.py --read-only --profile <non-root-profile> --region <approved-region>

With --read-only it performs only sts:GetCallerIdentity, cloudwatch:ListDashboards and cloudwatch:ListMetrics. It REFUSES to run as the root
user. It contains no put_dashboard / delete_dashboard / any mutating call (tests/test_cloudwatch_dashboard.py asserts that), so it cannot
create or change anything. Passing it does NOT authorise a deployment: that still needs your explicit approval of the itemised manifest.

Free-tier rule checked (CloudWatch pricing page, to be re-verified at deploy time): the account-wide allowance is 3 custom dashboards, each with
up to 50 metrics. Whether the allowance applies to this PAID-plan account is UNVERIFIED; the script only reports slots, never costs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DASHBOARD = Path(__file__).resolve().parents[1] / "infra" / "aws" / "dashboards" / "enigma-control-plane.json"
FREE_DASHBOARDS, FREE_METRICS_PER_DASHBOARD = 3, 50
PLACEHOLDER = re.compile(r"\$\{([A-Z_]+)\}")


def metric_series(body: dict) -> list[list]:
    return [row for w in body.get("widgets", []) if w.get("type") == "metric" for row in w["properties"].get("metrics", [])]


def placeholders(body: dict) -> set[str]:
    return set(PLACEHOLDER.findall(json.dumps(body)))


def substitute(body: dict, values: dict[str, str]) -> dict:
    """Fill ${PLACEHOLDERS}; any placeholder left unfilled is an error (never deploy a draft with unresolved names)."""
    text = json.dumps(body)
    for k, v in values.items():
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", v):
            raise ValueError(f"unsafe value for {k}")
        text = text.replace("${" + k + "}", v)
    left = PLACEHOLDER.findall(text)
    if left:
        raise ValueError(f"unresolved placeholders: {sorted(set(left))}")
    return json.loads(text)


def identity_is_root(arn: str) -> bool:
    return arn.endswith(":root")


def assess(cw: Any, body: dict, name: str = "Enigma-Control-Plane") -> dict:
    """Read-only slot assessment. ``cw`` is a boto3 CloudWatch client (or a stub)."""
    existing: list[str] = []
    token = None
    for _ in range(20):                                              # bounded pagination
        r = cw.list_dashboards(**({"NextToken": token} if token else {}))
        existing += [d["DashboardName"] for d in r.get("DashboardEntries", [])]
        token = r.get("NextToken")
        if not token:
            break
    n_metrics = len(metric_series(body))
    others = [d for d in existing if d != name]
    return {"existing_dashboards": len(existing), "name_already_exists": name in existing, "new_dashboard_metrics": n_metrics,
            "metrics_within_limit": n_metrics <= FREE_METRICS_PER_DASHBOARD,
            "free_slot_available": len(others) + 1 <= FREE_DASHBOARDS,
            "verdict": "OK_TO_REQUEST_APPROVAL" if n_metrics <= FREE_METRICS_PER_DASHBOARD and len(others) + 1 <= FREE_DASHBOARDS else "STOP_NO_FREE_SLOT_OR_TOO_MANY_METRICS",
            "note": "Free-tier applicability to this PAID-plan account is unverified; this check never estimates cost."}


def missing_metrics(cw: Any, body: dict) -> list[str]:
    """After resources exist: which dashboard metrics have not been emitted yet (CloudWatch lists a metric only after its first datapoint)."""
    out = []
    for row in metric_series(body):
        ns, name, *rest = row
        dims = [{"Name": rest[i], "Value": rest[i + 1]} for i in range(0, len(rest) - 1, 2) if isinstance(rest[i], str)]
        r = cw.list_metrics(Namespace=ns, MetricName=name, Dimensions=dims)
        if not r.get("Metrics"):
            out.append(f"{ns}/{name} {[d['Value'] for d in dims]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--read-only", action="store_true", help="actually call the (read-only) AWS APIs")
    ap.add_argument("--profile"), ap.add_argument("--region")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="placeholder values (only needed with --read-only --verify-metrics)")
    ap.add_argument("--verify-metrics", action="store_true")
    a = ap.parse_args(argv)
    body = json.loads(DASHBOARD.read_text())
    print(f"draft: {len(metric_series(body))} metric series, placeholders {sorted(placeholders(body))}  (NOT DEPLOYED)")
    if not a.read_only:
        print("offline mode: no AWS call made. Re-run with --read-only --profile <non-root> --region <approved> to check account slots.")
        return 0
    if not (a.profile and a.region):
        print("refusing: --profile and --region are required (never the default/root credential chain)")
        return 2
    import boto3
    s = boto3.Session(profile_name=a.profile, region_name=a.region)
    arn = s.client("sts").get_caller_identity()["Arn"]
    if identity_is_root(arn):
        print("refusing: the caller is the ROOT user. Use the non-root deployer (docs/AWS_ROOT_KEY_REMEDIATION.md).")
        return 3
    cw = s.client("cloudwatch")
    print(json.dumps(assess(cw, body), indent=2))
    if a.verify_metrics:
        vals = dict(kv.split("=", 1) for kv in a.set)
        print("metrics not yet emitted:", missing_metrics(cw, substitute(body, vals)) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
