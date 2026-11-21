"""AWS operations visibility for the Enigma-admin-only "AWS Infrastructure" page. READ-ONLY, bounded, cached.

What this is: a small reader of AWS *control-plane* health (Lambda, DynamoDB, CloudFront, Cognito) for a short allow-list of resources named in an
operator-supplied manifest. What it is NOT: it never creates/updates/deletes anything, never calls Cost Explorer/Billing, never lists the account,
never reports GPU/vLLM data (that comes from the Kaggle session inside Enigma), and never returns ARNs, account ids, credentials or IAM details.

Modes (ENIGMA_AWS_OPS): ``off`` (default: honest "No Enigma AWS resources deployed"), ``mock`` (deterministic SYNTHETIC demo data, refused in
production), ``live`` (boto3 reads using the Lambda execution role; needs a manifest). Credentials are never read from settings.

Safety properties enforced here and in tests/api/test_awsops.py:
  * only whitelisted read calls (get_function_configuration, describe_table, describe_time_to_live, get_distribution, describe_user_pool,
    get_metric_data); resource ids come ONLY from the validated manifest, never from a request;
  * fixed time windows, bounded pagination, short timeouts, 2 retries, per-key TTL cache (default 60 s) with single-flight, stale-on-error;
  * every outgoing payload is scrubbed of ARNs, 12-digit account ids and access-key-looking strings.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

RANGES = {"1h": (3600, 300), "6h": (21600, 900), "24h": (86400, 3600)}        # window seconds, CloudWatch period seconds (<= 288 points)
MODES = ("off", "mock", "live")
MAX_PAGES = 3

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TABLE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_DIST = re.compile(r"^[A-Z0-9]{8,20}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_POOL = re.compile(r"^[A-Za-z0-9-]+_[A-Za-z0-9]+$")
_SCRUB = [(re.compile(r"arn:aws[a-z-]*:[^\s\"']+"), "[redacted-arn]"), (re.compile(r"\b[0-9]{12}\b"), "[redacted-id]"),
          (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "[redacted-key]")]

BILLING_LINKS = {                                                           # link-out only: no Cost Explorer / Billing API is ever called
    "billing_home": "https://console.aws.amazon.com/billing/home#/",
    "credits": "https://console.aws.amazon.com/billing/home#/credits",
    "free_tier": "https://console.aws.amazon.com/billing/home#/freetier",
    "cost_explorer": "https://console.aws.amazon.com/costmanagement/home#/cost-explorer",
}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    region: str
    environment: str
    web_function: str
    api_function: str
    ddb_table: str
    cloudfront_distribution_id: str | None = None
    user_pool_id: str | None = None

    @staticmethod
    def parse(text: str) -> "Manifest":
        try:
            d = json.loads(text)
        except ValueError as e:
            raise ManifestError("manifest is not valid JSON") from e
        allowed = {"region", "environment", "web_function", "api_function", "ddb_table", "cloudfront_distribution_id", "user_pool_id"}
        if not isinstance(d, dict) or set(d) - allowed:
            raise ManifestError(f"manifest keys must be within {sorted(allowed)}")
        checks = {"region": _REGION, "web_function": _NAME, "api_function": _NAME, "ddb_table": _TABLE, "cloudfront_distribution_id": _DIST, "user_pool_id": _POOL}
        for k, rx in checks.items():
            if d.get(k) is not None and not (isinstance(d[k], str) and rx.fullmatch(d[k])):
                raise ManifestError(f"manifest field {k} is malformed")
        for k in ("region", "web_function", "api_function", "ddb_table"):
            if not d.get(k):
                raise ManifestError(f"manifest field {k} is required")
        if not re.fullmatch(r"[a-z0-9-]{1,16}", str(d.get("environment", "dev"))):
            raise ManifestError("manifest field environment is malformed")
        return Manifest(d["region"], d.get("environment", "dev"), d["web_function"], d["api_function"], d["ddb_table"], d.get("cloudfront_distribution_id"), d.get("user_pool_id"))


def scrub(v: Any) -> Any:
    """Remove ARNs, account ids and key-like strings from any nested structure."""
    if isinstance(v, str):
        for rx, rep in _SCRUB:
            v = rx.sub(rep, v)
        return v
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [scrub(x) for x in v]
    return v


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- the metric series Enigma reads (kept in sync with infra/aws/dashboards/enigma-control-plane.json by a test)
def series_defs(m: Manifest) -> dict[str, dict[str, tuple[str, str, list[tuple[str, str]], str]]]:
    """group -> series key -> (namespace, metric, dimensions, stat). Only ids from the validated manifest are used."""
    fn = lambda name: [("FunctionName", name)]            # noqa: E731
    tb = [("TableName", m.ddb_table)]
    g: dict[str, dict[str, tuple[str, str, list[tuple[str, str]], str]]] = {
        "lambda_web": {"invocations": ("AWS/Lambda", "Invocations", fn(m.web_function), "Sum"), "errors": ("AWS/Lambda", "Errors", fn(m.web_function), "Sum"),
                       "throttles": ("AWS/Lambda", "Throttles", fn(m.web_function), "Sum"), "duration_avg_ms": ("AWS/Lambda", "Duration", fn(m.web_function), "Average"),
                       "duration_p95_ms": ("AWS/Lambda", "Duration", fn(m.web_function), "p95")},
        "lambda_api": {"invocations": ("AWS/Lambda", "Invocations", fn(m.api_function), "Sum"), "errors": ("AWS/Lambda", "Errors", fn(m.api_function), "Sum"),
                       "throttles": ("AWS/Lambda", "Throttles", fn(m.api_function), "Sum"), "duration_avg_ms": ("AWS/Lambda", "Duration", fn(m.api_function), "Average"),
                       "duration_p95_ms": ("AWS/Lambda", "Duration", fn(m.api_function), "p95")},
        "dynamodb": {"read_units": ("AWS/DynamoDB", "ConsumedReadCapacityUnits", tb, "Sum"), "write_units": ("AWS/DynamoDB", "ConsumedWriteCapacityUnits", tb, "Sum"),
                     "read_throttles": ("AWS/DynamoDB", "ReadThrottleEvents", tb, "Sum"), "write_throttles": ("AWS/DynamoDB", "WriteThrottleEvents", tb, "Sum"),
                     "conditional_check_failed": ("AWS/DynamoDB", "ConditionalCheckFailedRequests", tb, "Sum")},
    }
    if m.cloudfront_distribution_id:
        cf = [("DistributionId", m.cloudfront_distribution_id), ("Region", "Global")]
        g["cloudfront"] = {"requests": ("AWS/CloudFront", "Requests", cf, "Sum"), "error_4xx_pct": ("AWS/CloudFront", "4xxErrorRate", cf, "Average"),
                           "error_5xx_pct": ("AWS/CloudFront", "5xxErrorRate", cf, "Average")}
    return g


TOPOLOGY = [
    {"id": "browser", "label": "Browser", "kind": "client", "aws": False},
    {"id": "cloudfront", "label": "CloudFront", "kind": "edge", "aws": True},
    {"id": "web", "label": "Next.js 16 on Lambda", "kind": "compute", "aws": True},
    {"id": "api", "label": "FastAPI on Lambda (Python 3.13)", "kind": "compute", "aws": True},
    {"id": "cognito", "label": "Cognito user pool", "kind": "identity", "aws": True},
    {"id": "ddb", "label": "DynamoDB", "kind": "data", "aws": True},
    {"id": "kaggle", "label": "Kaggle 2x T4 (user-launched, NOT AWS)", "kind": "external-gpu", "aws": False},
]
TOPOLOGY_EDGES = [["browser", "cloudfront"], ["cloudfront", "web"], ["web", "api"], ["web", "cognito"], ["api", "ddb"], ["kaggle", "api"]]


class OpsService:
    def __init__(self, mode: str = "off", manifest: Manifest | None = None, *, client_factory: Callable[[str, str], Any] | None = None,
                 clock: Callable[[], float] = time.time, ttl_s: int = 60, build_sha: str = "", environment: str = "dev", api_info: Callable[[], dict] | None = None):
        if mode not in MODES:
            raise ValueError("mode must be off|mock|live")
        self.mode, self.manifest, self._clock = mode, manifest, clock
        self.ttl_s = max(30, int(ttl_s))                           # never poll AWS faster than every 30 s
        self.build_sha, self.environment = build_sha[:40], environment
        self._factory = client_factory or self._default_factory
        self._api_info = api_info or (lambda: {})
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()
        self.aws_calls = 0                                         # observable: how many AWS API calls were made (tests assert the cache holds)

    # ---------------------------------------------------------------- plumbing
    @staticmethod
    def _default_factory(service: str, region: str) -> Any:
        import boto3
        from botocore.config import Config
        return boto3.client(service, region_name=region, config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 2, "mode": "standard"}))

    def _client(self, service: str, region: str | None = None) -> Any:
        assert self.manifest
        return self._factory(service, region or self.manifest.region)

    def _cached(self, key: str, loader: Callable[[], Any]) -> tuple[Any, float, bool]:
        """(value, fetched_at, stale). Single-flight per key; a failed refresh serves the previous value flagged stale."""
        with self._glock:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            now = self._clock()
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.ttl_s:
                return hit[1], hit[0], False
            try:
                val = loader()
            except Exception:                                     # noqa: BLE001
                if hit:
                    return hit[1], hit[0], True
                raise
            self._cache[key] = (now, val)
            return val, now, False

    def _prov(self, source: str, fetched_at: float, stale: bool) -> dict:
        now = self._clock()
        return {"source": source, "synthetic": source == "synthetic", "mode": self.mode,
                "region": self.manifest.region if self.manifest else None, "environment": self.manifest.environment if self.manifest else self.environment,
                "fetched_at": _iso(fetched_at), "age_s": max(0, int(now - fetched_at)), "stale": stale, "ttl_s": self.ttl_s}

    @staticmethod
    def _classify(e: Exception) -> str:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
        if code in ("ResourceNotFoundException", "NoSuchDistribution", "NotFoundException"):
            return "NOT_DEPLOYED"
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "UnrecognizedClientException", "ExpiredToken", "ExpiredTokenException"):
            return "NOT_AUTHORIZED"
        return "UNKNOWN"

    # ---------------------------------------------------------------- inventory
    def _live_resources(self) -> list[dict]:
        m = self.manifest
        assert m
        out: list[dict] = []

        def one(key: str, service: str, name: str, fn: Callable[[], dict]) -> None:
            self.aws_calls += 1
            try:
                detail = fn()
                out.append({"key": key, "service": service, "name": name, "status": "DEPLOYED", "detail": detail})
            except Exception as e:                                # noqa: BLE001
                out.append({"key": key, "service": service, "name": name, "status": self._classify(e), "detail": {}})

        def lam(name: str) -> Callable[[], dict]:
            def f() -> dict:
                c = self._client("lambda").get_function_configuration(FunctionName=name)
                return {"runtime": c.get("Runtime"), "architectures": c.get("Architectures", []), "memory_mb": c.get("MemorySize"), "timeout_s": c.get("Timeout"),
                        "state": c.get("State"), "last_modified": c.get("LastModified"), "package": c.get("PackageType")}
            return f

        def ddb() -> dict:
            c = self._client("dynamodb")
            t = c.describe_table(TableName=m.ddb_table)["Table"]
            self.aws_calls += 1
            ttl = c.describe_time_to_live(TableName=m.ddb_table).get("TimeToLiveDescription", {})
            pt = t.get("ProvisionedThroughput", {}) or {}
            return {"status": t.get("TableStatus"), "billing_mode": (t.get("BillingModeSummary") or {}).get("BillingMode", "PROVISIONED"), "read_capacity": pt.get("ReadCapacityUnits"),
                    "write_capacity": pt.get("WriteCapacityUnits"), "item_count": t.get("ItemCount"), "size_bytes": t.get("TableSizeBytes"),
                    "gsi_count": len(t.get("GlobalSecondaryIndexes") or []), "ttl": ttl.get("TimeToLiveStatus"), "deletion_protection": t.get("DeletionProtectionEnabled")}

        one("web_lambda", "Lambda", m.web_function, lam(m.web_function))
        one("api_lambda", "Lambda", m.api_function, lam(m.api_function))
        one("dynamodb", "DynamoDB", m.ddb_table, ddb)
        if m.cloudfront_distribution_id:
            def cf() -> dict:
                d = self._client("cloudfront", "us-east-1").get_distribution(Id=m.cloudfront_distribution_id)["Distribution"]
                return {"status": d.get("Status"), "enabled": d.get("DistributionConfig", {}).get("Enabled"), "domain": d.get("DomainName")}
            one("cloudfront", "CloudFront", m.cloudfront_distribution_id, cf)
        if m.user_pool_id:
            def cg() -> dict:
                p = self._client("cognito-idp").describe_user_pool(UserPoolId=m.user_pool_id)["UserPool"]
                return {"name": p.get("Name"), "estimated_users": p.get("EstimatedNumberOfUsers"), "mfa": p.get("MfaConfiguration"), "created": str(p.get("CreationDate", ""))}
            one("cognito", "Cognito", m.user_pool_id.split("_", 1)[0] + "_…", cg)
        return out

    def _summary(self, resources: list[dict]) -> dict:
        states = {r["status"] for r in resources}
        if not resources or states == {"NOT_DEPLOYED"}:
            return {"state": "NOT_DEPLOYED", "message": "No Enigma AWS resources deployed."}
        if "NOT_AUTHORIZED" in states:
            return {"state": "NOT_AUTHORIZED", "message": "The reader role is not authorised for some resources."}
        if states == {"DEPLOYED"}:
            return {"state": "DEPLOYED", "message": "All manifest resources were found."}
        if "UNKNOWN" in states:
            return {"state": "UNKNOWN", "message": "Some resources could not be verified."}
        return {"state": "PARTIAL", "message": "Some manifest resources are not deployed."}

    def _base(self) -> dict:
        return {"mode": self.mode, "app": {"api": {"status": "ok", **self._api_info()}, "build_sha": self.build_sha or None, "environment": self.environment},
                "topology": TOPOLOGY, "topology_edges": TOPOLOGY_EDGES, "cost": self.cost(),
                "boundary": "AWS operations only. GPU / vLLM telemetry comes from the user-launched Kaggle 2x T4 session and is shown on the GPU pages, never here."}

    def status(self) -> dict:
        base = self._base()
        if self.mode == "off" or (self.mode == "live" and not self.manifest):
            msg = "No Enigma AWS resources deployed." if self.mode == "off" else "Live mode is on but no resource manifest is configured."
            return scrub({**base, "summary": {"state": "NOT_DEPLOYED", "message": msg}, "resources": [], "provenance": self._prov("none", self._clock(), False)})
        if self.mode == "mock":
            return scrub({**base, **_mock_status(self._clock()), "provenance": self._prov("synthetic", self._clock(), False)})
        res, at, stale = self._cached("inventory", self._live_resources)
        return scrub({**base, "summary": self._summary(res), "resources": res, "provenance": self._prov("aws", at, stale)})

    # ---------------------------------------------------------------- metrics
    def _live_metrics(self, rng: str) -> dict:
        m = self.manifest
        assert m
        window, period = RANGES[rng]
        end = self._clock()
        defs = series_defs(m)
        queries, index = [], {}
        for g, sd in defs.items():
            for k, (ns, name, dims, stat) in sd.items():
                qid = f"q{len(queries)}"
                index[qid] = (g, k)
                queries.append({"Id": qid, "MetricStat": {"Metric": {"Namespace": ns, "MetricName": name, "Dimensions": [{"Name": a, "Value": b} for a, b in dims]},
                                                          "Period": period, "Stat": stat}, "ReturnData": True})
        out: dict[str, dict[str, list]] = {g: {k: [] for k in sd} for g, sd in defs.items()}
        by_region: dict[str, list[dict]] = {}
        for q in queries:                                          # CloudFront metrics live only in us-east-1
            by_region.setdefault("us-east-1" if q["MetricStat"]["Metric"]["Namespace"] == "AWS/CloudFront" else m.region, []).append(q)
        for region, qs in by_region.items():
            token = None
            for _ in range(MAX_PAGES):
                self.aws_calls += 1
                kw: dict = dict(MetricDataQueries=qs, StartTime=datetime.fromtimestamp(end - window, timezone.utc), EndTime=datetime.fromtimestamp(end, timezone.utc),
                                ScanBy="TimestampAscending")
                if token:
                    kw["NextToken"] = token
                r = self._client("cloudwatch", region).get_metric_data(**kw)
                for res in r.get("MetricDataResults", []):
                    g, k = index[res["Id"]]
                    out[g][k] += [{"ts": int(t.timestamp() * 1000), "value": float(v)} for t, v in zip(res.get("Timestamps", []), res.get("Values", []))]
                token = r.get("NextToken")
                if not token:
                    break
        return {"period_s": period, "window_s": window, "groups": {g: {"state": "OK" if any(s for s in sd.values()) else "NO_DATA", "series": sd} for g, sd in out.items()}}

    def metrics(self, rng: str) -> dict:
        if rng not in RANGES:
            raise ValueError("range must be one of 1h, 6h, 24h")
        base = {"mode": self.mode, "range": rng}
        if self.mode == "off" or (self.mode == "live" and not self.manifest):
            return {**base, "period_s": RANGES[rng][1], "window_s": RANGES[rng][0], "groups": {}, "provenance": self._prov("none", self._clock(), False)}
        if self.mode == "mock":
            return {**base, **_mock_metrics(self._clock(), rng), "provenance": self._prov("synthetic", self._clock(), False)}
        try:
            val, at, stale = self._cached(f"metrics:{rng}", lambda: self._live_metrics(rng))
        except Exception as e:                                    # noqa: BLE001  never surface AWS error text; report a classified state only
            return {**base, "period_s": RANGES[rng][1], "window_s": RANGES[rng][0], "groups": {}, "state": self._classify(e), "provenance": self._prov("aws", self._clock(), False)}
        return scrub({**base, **val, "provenance": self._prov("aws", at, stale)})

    # ---------------------------------------------------------------- cost: link-out only
    def cost(self) -> dict:
        return {"mode": "link-out", "api_calls_made": False, "links": BILLING_LINKS,
                "credits": {"remaining": "UNKNOWN", "expiry": "UNKNOWN (read it in the Billing console, Credits page)"},
                "note": "Enigma does not call Cost Explorer or Billing APIs (they can be billed). Open the AWS console links with your own sign-in."}


# ---------------------------------------------------------------- deterministic SYNTHETIC data (dev / e2e only; never presented as real)
def _wave(t: float, period: float, lo: float, hi: float, phase: float = 0.0) -> float:
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * (t / period) + phase))


def _mock_status(now: float) -> dict:
    mk = lambda key, svc, name, detail: {"key": key, "service": svc, "name": name, "status": "SYNTHETIC", "detail": detail}  # noqa: E731
    res = [mk("web_lambda", "Lambda", "synthetic-web", {"runtime": "nodejs24.x", "architectures": ["arm64"], "memory_mb": 1024, "timeout_s": 20, "state": "Active"}),
           mk("api_lambda", "Lambda", "synthetic-api", {"runtime": "python3.13", "architectures": ["x86_64"], "memory_mb": 512, "timeout_s": 15, "state": "Active"}),
           mk("dynamodb", "DynamoDB", "synthetic-table", {"status": "ACTIVE", "billing_mode": "PAY_PER_REQUEST", "gsi_count": 0, "ttl": "ENABLED"}),
           mk("cloudfront", "CloudFront", "SYNTHETIC0000", {"status": "Deployed", "enabled": True}),
           mk("cognito", "Cognito", "synthetic_…", {"name": "synthetic", "estimated_users": 3, "mfa": "OFF"})]
    return {"summary": {"state": "SYNTHETIC", "message": "SYNTHETIC demo data: no AWS resource is being read."}, "resources": res}


def _mock_metrics(now: float, rng: str) -> dict:
    window, period = RANGES[rng]
    ts = [int((now - window + i * period) * 1000) for i in range(window // period + 1)]
    mk = lambda lo, hi, ph=0.0: [{"ts": t, "value": round(_wave(t / 1000, 1800, lo, hi, ph), 2)} for t in ts]  # noqa: E731
    zero = lambda: [{"ts": t, "value": 0.0} for t in ts]  # noqa: E731
    g = {"lambda_web": {"invocations": mk(20, 120), "errors": zero(), "throttles": zero(), "duration_avg_ms": mk(40, 90, 1), "duration_p95_ms": mk(90, 220, 1)},
         "lambda_api": {"invocations": mk(60, 400, 2), "errors": mk(0, 2, 2), "throttles": zero(), "duration_avg_ms": mk(20, 60, 3), "duration_p95_ms": mk(50, 140, 3)},
         "dynamodb": {"read_units": mk(50, 300, 4), "write_units": mk(80, 500, 5), "read_throttles": zero(), "write_throttles": zero(), "conditional_check_failed": mk(0, 6, 6)},
         "cloudfront": {"requests": mk(30, 200, 7), "error_4xx_pct": mk(0, 2, 7), "error_5xx_pct": zero()}}
    return {"period_s": period, "window_s": window, "groups": {k: {"state": "OK", "series": v} for k, v in g.items()}}
