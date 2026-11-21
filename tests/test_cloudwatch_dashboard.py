"""Offline validation of the CloudWatch dashboard DRAFT (infra/aws/dashboards/enigma-control-plane.json) and its read-only pre-flight script.

Proves structure only: real AWS namespaces/metric names/dimensions/units, <= 50 metric series, no custom metrics, no log/query widgets,
bounded periods, valid grid layout, placeholders only. It does NOT prove that the metrics exist or that a dashboard fits the account's free
allowance: that is checked read-only after approval with scripts/check_dashboard_quota.py. Nothing here calls AWS.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_dashboard_quota as q  # noqa: E402

BODY = json.loads(q.DASHBOARD.read_text())

# AWS-documented metrics used by Enigma: namespace -> metric -> (required dimension names in order, unit word that must appear in the widget title)
CATALOG: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    "AWS/Lambda": {"Invocations": (("FunctionName",), "count"), "Errors": (("FunctionName",), "count"), "Throttles": (("FunctionName",), "count"),
                   "Duration": (("FunctionName",), "ms"), "ConcurrentExecutions": (("FunctionName",), "count")},
    "AWS/DynamoDB": {"ConsumedReadCapacityUnits": (("TableName",), "capacity units"), "ConsumedWriteCapacityUnits": (("TableName",), "capacity units"),
                     "ReadThrottleEvents": (("TableName",), "count"), "WriteThrottleEvents": (("TableName",), "count"),
                     "ConditionalCheckFailedRequests": (("TableName",), "count"), "TimeToLiveDeletedItemCount": (("TableName",), "count"),
                     "SystemErrors": (("TableName", "Operation"), "count"), "SuccessfulRequestLatency": (("TableName", "Operation"), "ms")},
    "AWS/CloudFront": {"Requests": (("DistributionId", "Region"), "count"), "BytesDownloaded": (("DistributionId", "Region"), "bytes"),
                       "4xxErrorRate": (("DistributionId", "Region"), "percent"), "5xxErrorRate": (("DistributionId", "Region"), "percent"),
                       "TotalErrorRate": (("DistributionId", "Region"), "percent")},
}
STATS = {"Sum", "Average", "Maximum", "Minimum", "SampleCount", "p50", "p90", "p95", "p99"}
PLACEHOLDERS = {"AWS_REGION", "WEB_FUNCTION_NAME", "API_FUNCTION_NAME", "DDB_TABLE_NAME", "CLOUDFRONT_DISTRIBUTION_ID"}
PERIODS = {60, 300, 900, 3600}
FORBIDDEN_WIDGET_KEYS = {"logQuery", "queryDetails", "query", "alarms", "annotations"}


def problems(body: dict) -> list[str]:
    out: list[str] = []
    series = q.metric_series(body)
    if len(series) > q.FREE_METRICS_PER_DASHBOARD:
        out.append(f"{len(series)} metric series exceeds {q.FREE_METRICS_PER_DASHBOARD}")
    if not q.placeholders(body) <= PLACEHOLDERS:
        out.append(f"unknown placeholders {sorted(q.placeholders(body) - PLACEHOLDERS)}")
    occupied: list[tuple[int, int, int, int]] = []
    for i, w in enumerate(body["widgets"]):
        if w["type"] not in ("text", "metric"):
            out.append(f"widget {i}: type {w['type']} not allowed (no log/explorer/alarm widgets)")
            continue
        x, y, wd, ht = w["x"], w["y"], w["width"], w["height"]
        if not (0 <= x and x + wd <= 24 and wd >= 1 and ht >= 1 and y >= 0):
            out.append(f"widget {i}: outside the 24-column grid")
        for (ox, oy, ow, oh) in occupied:
            if x < ox + ow and ox < x + wd and y < oy + oh and oy < y + ht:
                out.append(f"widget {i}: overlaps another widget")
        occupied.append((x, y, wd, ht))
        props = w["properties"]
        if FORBIDDEN_WIDGET_KEYS & set(props):
            out.append(f"widget {i}: forbidden keys {sorted(FORBIDDEN_WIDGET_KEYS & set(props))}")
        if w["type"] == "text":
            continue
        if props.get("period") not in PERIODS:
            out.append(f"widget {i}: period {props.get('period')} not in {sorted(PERIODS)}")
        if props.get("view") != "timeSeries":
            out.append(f"widget {i}: view must be timeSeries")
        title = props.get("title", "")
        units = set()
        for row in props["metrics"]:
            ns, name, *rest = row
            opts = rest[-1] if rest and isinstance(rest[-1], dict) else {}
            dims = rest[:-1] if opts else rest
            if ns not in CATALOG:
                out.append(f"widget {i}: namespace {ns} is not an approved AWS namespace (custom metrics are forbidden)")
                continue
            if name not in CATALOG[ns]:
                out.append(f"widget {i}: {ns}/{name} is not in the approved metric catalog")
                continue
            want_dims, unit = CATALOG[ns][name]
            got = tuple(dims[0::2])
            if got != want_dims:
                out.append(f"widget {i}: {ns}/{name} dimensions {got} != {want_dims}")
            if len(dims) % 2:
                out.append(f"widget {i}: dangling dimension in {ns}/{name}")
            if opts.get("stat") not in STATS:
                out.append(f"widget {i}: invalid stat {opts.get('stat')!r}")
            if ns == "AWS/CloudFront" and props.get("region") != "us-east-1":
                out.append(f"widget {i}: CloudFront metrics exist only in us-east-1")
            units.add(unit)
        if not any(f"({u}" in title.lower() or f"/ {u}" in title.lower() or f"{u})" in title.lower() or f"{u} /" in title.lower() for u in units):
            out.append(f"widget {i}: title {title!r} must state the unit(s) {sorted(units)}")
    return out


def test_the_draft_is_valid():
    assert problems(BODY) == []


def test_series_count_is_within_the_free_per_dashboard_limit_with_headroom():
    n = len(q.metric_series(BODY))
    assert 10 <= n <= 50 and n == 30


def test_draft_states_it_is_not_deployed_and_not_gpu_telemetry():
    text = BODY["widgets"][0]["properties"]["markdown"]
    assert "NOT DEPLOYED" in text and "GPU" in text and "Kaggle" in text
    assert not re.search(r"\b(nvidia|vllm|cuda)\b", json.dumps([w for w in BODY["widgets"] if w["type"] == "metric"]), re.I)


def test_every_metric_row_is_a_plain_aws_metric_without_math_or_search():
    for row in q.metric_series(BODY):
        assert row[0].startswith("AWS/") and not any(isinstance(c, str) and c.startswith(("SEARCH(", "METRICS(")) for c in row)
        assert "expression" not in json.dumps(row).lower()


def test_cloudfront_widgets_use_us_east_1_and_others_use_the_region_placeholder():
    for w in BODY["widgets"]:
        if w["type"] != "metric":
            continue
        ns = {r[0] for r in w["properties"]["metrics"]}
        assert w["properties"]["region"] == ("us-east-1" if ns == {"AWS/CloudFront"} else "${AWS_REGION}")


# ---- the validator can fail (each rule is exercised)
def mutate(fn):
    b = copy.deepcopy(BODY)
    fn(b)
    return problems(b)


def test_validator_rejects_custom_namespace():
    assert any("custom metrics" in p for p in mutate(lambda b: b["widgets"][1]["properties"]["metrics"].append(["Enigma/Custom", "Anything", {"stat": "Sum"}])))


def test_validator_rejects_more_than_50_series():
    assert any("exceeds 50" in p for p in mutate(lambda b: b["widgets"][1]["properties"]["metrics"].extend([["AWS/Lambda", "Errors", "FunctionName", "${WEB_FUNCTION_NAME}", {"stat": "Sum"}]] * 25)))


def test_validator_rejects_unknown_metric_wrong_dimension_bad_stat_bad_period():
    assert any("approved metric catalog" in p for p in mutate(lambda b: b["widgets"][1]["properties"]["metrics"].append(["AWS/Lambda", "NotAMetric", "FunctionName", "x", {"stat": "Sum"}])))
    assert any("dimensions" in p for p in mutate(lambda b: b["widgets"][1]["properties"]["metrics"].append(["AWS/Lambda", "Errors", "TableName", "x", {"stat": "Sum"}])))
    assert any("invalid stat" in p for p in mutate(lambda b: b["widgets"][1]["properties"]["metrics"][0][-1].update(stat="Median")))
    assert any("period" in p for p in mutate(lambda b: b["widgets"][1]["properties"].update(period=1)))


def test_validator_rejects_log_widgets_overlap_grid_and_unit_less_titles_and_unknown_placeholders():
    assert any("not allowed" in p for p in mutate(lambda b: b["widgets"].append({"type": "log", "x": 0, "y": 40, "width": 6, "height": 6, "properties": {}})))
    assert any("overlaps" in p for p in mutate(lambda b: b["widgets"][2].update(x=0, y=3)))
    assert any("24-column" in p for p in mutate(lambda b: b["widgets"][1].update(x=20)))
    assert any("must state the unit" in p for p in mutate(lambda b: b["widgets"][1]["properties"].update(title="Lambda stuff")))
    assert any("unknown placeholders" in p for p in mutate(lambda b: b["widgets"][1]["properties"]["metrics"][0].insert(3, "${SECRET_ARN}")))
    assert any("CloudFront metrics exist only in us-east-1" in p for p in mutate(lambda b: b["widgets"][-1]["properties"].update(region="eu-west-1")))


def test_validator_rejects_query_and_alarm_keys():
    assert any("forbidden keys" in p for p in mutate(lambda b: b["widgets"][1]["properties"].update(query="fields @message")))


# ---- substitution
def test_substitute_requires_every_placeholder_and_rejects_unsafe_values():
    vals = {"AWS_REGION": "us-east-1", "WEB_FUNCTION_NAME": "enigma-web-dev", "API_FUNCTION_NAME": "enigma-api-dev", "DDB_TABLE_NAME": "enigma-dev", "CLOUDFRONT_DISTRIBUTION_ID": "E1ABCDEF"}
    out = q.substitute(BODY, vals)
    assert not q.placeholders(out)                                                        # nothing left to fill: a substituted body is no longer a draft
    assert q.metric_series(out)[0][3] == "enigma-web-dev"
    with pytest.raises(ValueError, match="unresolved"):
        q.substitute(BODY, {k: v for k, v in vals.items() if k != "DDB_TABLE_NAME"})
    with pytest.raises(ValueError, match="unsafe"):
        q.substitute(BODY, {**vals, "DDB_TABLE_NAME": 'x"}, {"evil'})


# ---- quota pre-flight (stub client: no AWS, no moto needed)
class StubCW:
    def __init__(self, names, metrics=None): self.names, self.metrics, self.calls = names, metrics or {}, []
    def list_dashboards(self, **kw):
        self.calls.append("list_dashboards")
        return {"DashboardEntries": [{"DashboardName": n} for n in self.names]}
    def list_metrics(self, Namespace, MetricName, Dimensions):
        self.calls.append("list_metrics")
        return {"Metrics": [1] if (Namespace, MetricName) in self.metrics else []}


def test_quota_assessment_slots_and_limits():
    assert q.assess(StubCW([]), BODY)["verdict"] == "OK_TO_REQUEST_APPROVAL"
    assert q.assess(StubCW(["a", "b"]), BODY)["verdict"] == "OK_TO_REQUEST_APPROVAL"                  # third slot still free
    r = q.assess(StubCW(["a", "b", "c"]), BODY)
    assert r["free_slot_available"] is False and r["verdict"].startswith("STOP")
    assert q.assess(StubCW(["a", "b", "Enigma-Control-Plane"]), BODY)["name_already_exists"] is True      # updating our own does not use a new slot
    big = copy.deepcopy(BODY); big["widgets"][1]["properties"]["metrics"] += [["AWS/Lambda", "Errors", "FunctionName", "f", {"stat": "Sum"}]] * 25
    assert q.assess(StubCW([]), big)["metrics_within_limit"] is False


def test_missing_metrics_reports_not_yet_emitted():
    sub = q.substitute(BODY, {"AWS_REGION": "us-east-1", "WEB_FUNCTION_NAME": "w", "API_FUNCTION_NAME": "a", "DDB_TABLE_NAME": "t", "CLOUDFRONT_DISTRIBUTION_ID": "E1"})
    cw = StubCW([], {("AWS/Lambda", "Invocations")})
    miss = q.missing_metrics(cw, sub)
    assert len(miss) == len(q.metric_series(sub)) - 2 and not any("Invocations" in m for m in miss)


def test_root_identity_is_detected_and_cli_refuses_without_profile(capsys):
    assert q.identity_is_root("arn:aws:iam::123456789012:root") and not q.identity_is_root("arn:aws:sts::123456789012:assumed-role/EnigmaDeployer/x")
    assert q.main([]) == 0 and "no AWS call made" in capsys.readouterr().out
    assert q.main(["--read-only"]) == 2 and "refusing" in capsys.readouterr().out


def test_script_contains_no_mutating_aws_call():
    src = (ROOT / "scripts" / "check_dashboard_quota.py").read_text()
    calls = {n.func.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not {c for c in calls if re.match(r"(put|delete|create|update|tag|untag|enable|disable)_", c)}, calls
    assert {"list_dashboards", "list_metrics", "get_caller_identity"} <= calls                  # and it really does the read-only calls it claims to
