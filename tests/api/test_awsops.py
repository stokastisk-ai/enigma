"""Admin-only AWS operations page: authorization, honesty states, read-only/bounded AWS access, caching, scrubbing.

The live reader is exercised against moto (an in-process emulator; no AWS account, network or credential: see tests/conftest.py network guard and
the fake-credential fixture) and against recording spies that prove which AWS calls are, and are not, ever made. Emulator results are NOT evidence
that a real account returns the same data or that the dashboard fits the account's free allowance.
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile

import boto3
import jwt
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from api_helpers import DEV_SECRET, dev_token
from enigma_api import awsops
from enigma_api.awsops import MAX_PAGES, Manifest, ManifestError, OpsService, scrub, series_defs
from enigma_api.main import create_app
from enigma_api.settings import Settings

MANIFEST = {"region": "us-east-1", "environment": "dev", "web_function": "enigma-web-dev", "api_function": "enigma-api-dev", "ddb_table": "enigma-dev",
            "cloudfront_distribution_id": "E1ABCDEFGHIJKL", "user_pool_id": "us-east-1_ABCdef123"}
ALLOWED_READ_CALLS = {"get_function_configuration", "describe_table", "describe_time_to_live", "get_distribution", "describe_user_pool", "get_metric_data"}
ARN_OR_ID = re.compile(r"arn:aws|\b[0-9]{12}\b|AKIA[0-9A-Z]{16}")


def app_client(**kw) -> TestClient:
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32, admin_subjects="user_admin", **kw)
    return TestClient(create_app(s), raise_server_exceptions=False)


def hdr(user: str) -> dict:
    return {"Authorization": f"Bearer {dev_token(user)}"}


# ---------------------------------------------------------------- authorization (default deny, enforced on the backend)
PATHS = ["/api/v1/app/admin/aws/status", "/api/v1/app/admin/aws/metrics?range=1h", "/api/v1/app/admin/aws/cost"]


@pytest.mark.parametrize("path", PATHS)
def test_anonymous_gets_401_nonadmin_gets_403_admin_gets_200(path):
    with app_client(aws_ops="off") as c:
        assert c.get(path).status_code == 401
        assert c.get(path, headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
        r = c.get(path, headers=hdr("user_bob"))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
        assert c.get(path, headers=hdr("user_admin")).status_code == 200


def test_nobody_is_admin_when_the_list_is_empty_and_ids_must_match_exactly():
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32, admin_subjects="")
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        assert c.get(PATHS[0], headers=hdr("user_admin")).status_code == 403
    with app_client(aws_ops="off") as c:
        for who in ("user_admin2", "USER_ADMIN", "user_admin ", "cognito:user_admin"):
            assert c.get(PATHS[0], headers=hdr(who)).status_code == 403, who
    # an expired or HS256-forged admin token is not an admin
    forged = jwt.encode({"sub": "user_admin", "aud": "enigma-dev", "exp": int(time.time()) + 60}, "x" * 40, algorithm="HS256")
    with app_client(aws_ops="off") as c:
        assert c.get(PATHS[0], headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_me_reports_is_admin_and_two_users_never_see_each_others_flag():
    with app_client(aws_ops="off") as c:
        assert c.get("/api/v1/app/me", headers=hdr("user_admin")).json()["is_admin"] is True
        assert c.get("/api/v1/app/me", headers=hdr("user_bob")).json()["is_admin"] is False


def test_admin_endpoints_are_read_only_and_reject_unknown_ranges():
    with app_client(aws_ops="off") as c:
        for method in ("post", "put", "patch", "delete"):
            assert getattr(c, method)(PATHS[0], headers=hdr("user_admin")).status_code in (404, 405)
        assert c.get("/api/v1/app/admin/aws/metrics?range=7d", headers=hdr("user_admin")).status_code == 422
        assert c.get("/api/v1/app/admin/aws/metrics?range=1h;drop", headers=hdr("user_admin")).status_code == 422


def test_admin_rate_limit_applies():
    with app_client(aws_ops="off") as c:
        codes = [c.get(PATHS[2], headers=hdr("user_admin")).status_code for _ in range(45)]
        assert 429 in codes and codes[0] == 200
        assert codes[:25] == [200] * 25                                                  # ordinary use (a dozen page views) is never throttled
        assert c.get(PATHS[2], headers=hdr("user_bob")).status_code == 403               # and a non-admin is still just denied, not rate-limited into a different answer


# ---------------------------------------------------------------- honesty states
def test_off_mode_says_nothing_is_deployed_and_fabricates_nothing():
    with app_client(aws_ops="off") as c:
        s = c.get(PATHS[0], headers=hdr("user_admin")).json()
        assert s["summary"] == {"state": "NOT_DEPLOYED", "message": "No Enigma AWS resources deployed."}
        assert s["resources"] == [] and s["provenance"]["source"] == "none" and s["provenance"]["synthetic"] is False
        m = c.get(PATHS[1], headers=hdr("user_admin")).json()
        assert m["groups"] == {} and m["provenance"]["source"] == "none"
        cost = c.get(PATHS[2], headers=hdr("user_admin")).json()
        assert cost["api_calls_made"] is False and cost["credits"]["expiry"].startswith("UNKNOWN") and all(u.startswith("https://console.aws.amazon.com/") for u in cost["links"].values())


def test_mock_mode_is_labelled_synthetic_everywhere():
    with app_client(aws_ops="mock") as c:
        s = c.get(PATHS[0], headers=hdr("user_admin")).json()
        assert s["summary"]["state"] == "SYNTHETIC" and s["provenance"]["synthetic"] is True and s["provenance"]["source"] == "synthetic"
        assert {r["status"] for r in s["resources"]} == {"SYNTHETIC"} and "SYNTHETIC" in s["summary"]["message"]
        m = c.get(PATHS[1], headers=hdr("user_admin")).json()
        assert m["provenance"]["synthetic"] is True and set(m["groups"]) == {"lambda_web", "lambda_api", "dynamodb", "cloudfront"}
        assert not ARN_OR_ID.search(json.dumps([s, m]))
        assert "Kaggle" in s["boundary"] and "GPU" in s["boundary"]            # explicit separation from GPU telemetry
        assert any(n["id"] == "kaggle" and n["aws"] is False for n in s["topology"])      # Kaggle is shown as NOT AWS
        assert not any("gpu" in g.lower() for g in m["groups"])


def test_mock_mode_is_refused_in_production_and_live_requires_manifest_and_admins():
    prod = dict(env="production", store="dynamodb", ddb_table="t", cron_secret="c" * 32, auth_provider="cognito", cognito_region="us-east-1",
                cognito_user_pool_id="us-east-1_POOL", cognito_app_client_id="clientid1234")
    assert any("mock" in p for p in Settings(**prod, aws_ops="mock").validate_for_startup())
    assert any("MANIFEST" in p for p in Settings(**prod, aws_ops="live", admin_subjects="x").validate_for_startup())
    assert any("ADMIN_SUBJECTS" in p for p in Settings(**prod, aws_ops="live", aws_manifest=json.dumps(MANIFEST)).validate_for_startup())
    assert not Settings(**prod, aws_ops="live", aws_manifest=json.dumps(MANIFEST), admin_subjects="cognito:abc").validate_for_startup()
    assert any("ENIGMA_AWS_OPS" in p for p in Settings(**prod, aws_ops="bogus").validate_for_startup())


# ---------------------------------------------------------------- manifest
def test_manifest_is_strict():
    assert Manifest.parse(json.dumps(MANIFEST)).ddb_table == "enigma-dev"
    bad = [{**MANIFEST, "web_function": "x; rm -rf"}, {**MANIFEST, "region": "us east"}, {**MANIFEST, "extra": 1}, {**MANIFEST, "ddb_table": "a"},
           {**MANIFEST, "cloudfront_distribution_id": "lower"}, {**MANIFEST, "user_pool_id": "evil/x"}, {k: v for k, v in MANIFEST.items() if k != "api_function"}]
    for b in bad:
        with pytest.raises(ManifestError):
            Manifest.parse(json.dumps(b))
    with pytest.raises(ManifestError):
        Manifest.parse("not json")


def test_dashboard_draft_and_reader_agree_on_metric_definitions():
    dash = json.loads(open("infra/aws/dashboards/enigma-control-plane.json").read())
    drafted = {(r[0], r[1], r[-1]["stat"]) for w in dash["widgets"] if w["type"] == "metric" for r in w["properties"]["metrics"]}
    for g, sd in series_defs(Manifest.parse(json.dumps(MANIFEST))).items():
        for k, (ns, name, dims, stat) in sd.items():
            assert (ns, name, stat) in drafted, (g, k)


# ---------------------------------------------------------------- scrubbing
def test_scrub_removes_arns_account_ids_and_key_like_strings():
    dirty = {"a": "arn:aws:iam::123456789012:role/x", "b": ["acct 123456789012 here", "AKIAABCDEFGHIJKLMNOP"], "c": {"d": "arn:aws-cn:lambda:cn-north-1:1:function:f"}, "n": 123456789012}
    out = scrub(dirty)
    assert not ARN_OR_ID.search(json.dumps({k: v for k, v in out.items() if k != "n"})) and out["n"] == 123456789012


# ---------------------------------------------------------------- live reader on moto + spies
@pytest.fixture
def aws(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing"); monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing"); monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null"); monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    with mock_aws():
        key = boto3.client("dynamodb")._request_signer._credentials.access_key
        assert key in ("testing", "FOOBARKEY") and not key.startswith(("AKIA", "ASIA"))
        yield


class Spy:
    """Wraps a real (moto) client: records method names, optionally injects failures."""
    def __init__(self, inner, log, fail=None):
        self._i, self._log, self._fail = inner, log, fail or {}

    def __getattr__(self, name):
        fn = getattr(self._i, name)
        if not callable(fn) or name.startswith("_") or name in ("meta", "exceptions"):
            return fn

        def w(*a, **kw):
            self._log.append((name, kw))
            if name in self._fail:
                raise ClientError({"Error": {"Code": self._fail[name], "Message": "x"}}, name)
            return fn(*a, **kw)
        return w


def make_service(log, clock=None, fail=None, manifest=MANIFEST, ttl=60):
    services: list[str] = []

    def factory(service, region):
        services.append(service)
        return Spy(boto3.client(service, region_name=region), log, fail)
    svc = OpsService("live", Manifest.parse(json.dumps(manifest)), client_factory=factory, clock=clock or time.time, ttl_s=ttl, build_sha="abc1234", environment="dev")
    svc._services = services
    return svc


def create_fixtures(with_lambda=True):
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.create_table(TableName="enigma-dev", BillingMode="PAY_PER_REQUEST", AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}])
    if with_lambda:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("h.py", "def handler(e, c): return 1")
        role = boto3.client("iam", region_name="us-east-1").create_role(RoleName="r", AssumeRolePolicyDocument=json.dumps(
            {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}))["Role"]["Arn"]
        lam = boto3.client("lambda", region_name="us-east-1")
        for name in ("enigma-api-dev",):
            lam.create_function(FunctionName=name, Runtime="python3.12", Role=role, Handler="h.handler", Code={"ZipFile": buf.getvalue()},
                                MemorySize=512, Timeout=15, Architectures=["x86_64"])


def test_live_inventory_reports_deployed_missing_and_unverified_honestly(aws):
    create_fixtures()                                                          # api lambda + table exist; web lambda, CloudFront, Cognito do not
    log: list = []
    s = make_service(log).status()
    by = {r["key"]: r for r in s["resources"]}
    assert by["api_lambda"]["status"] == "DEPLOYED" and by["api_lambda"]["detail"]["memory_mb"] == 512 and by["api_lambda"]["detail"]["architectures"] == ["x86_64"]
    assert by["dynamodb"]["status"] == "DEPLOYED" and by["dynamodb"]["detail"]["billing_mode"] == "PAY_PER_REQUEST" and by["dynamodb"]["detail"]["gsi_count"] == 0
    assert by["web_lambda"]["status"] == "NOT_DEPLOYED"
    assert by["cloudfront"]["status"] in ("NOT_DEPLOYED", "UNKNOWN") and by["cognito"]["status"] in ("NOT_DEPLOYED", "UNKNOWN")
    assert s["summary"]["state"] == "PARTIAL" and s["provenance"]["source"] == "aws" and s["provenance"]["synthetic"] is False
    assert s["provenance"]["region"] == "us-east-1" and s["app"]["build_sha"] == "abc1234"
    assert not ARN_OR_ID.search(json.dumps(s))


def test_zero_resource_account_shows_not_deployed_not_a_graph(aws):
    s = make_service([]).status()                                              # nothing created in the emulator
    assert s["summary"]["state"] == "NOT_DEPLOYED" and all(r["status"] != "DEPLOYED" for r in s["resources"])
    m = make_service([]).metrics("1h")
    assert all(g["state"] == "NO_DATA" for g in m["groups"].values())


def test_access_denied_is_reported_as_not_authorized_never_as_zero(aws):
    s = make_service([], fail={"get_function_configuration": "AccessDeniedException", "describe_table": "AccessDeniedException"}).status()
    assert {r["status"] for r in s["resources"] if r["service"] in ("Lambda", "DynamoDB")} == {"NOT_AUTHORIZED"} and s["summary"]["state"] == "NOT_AUTHORIZED"
    m = make_service([], fail={"get_metric_data": "AccessDeniedException"}).metrics("1h")
    assert m["state"] == "NOT_AUTHORIZED" and m["groups"] == {}


def test_only_whitelisted_read_calls_are_ever_made_and_no_cost_or_billing_client_exists(aws):
    create_fixtures()
    log: list = []
    svc = make_service(log)
    svc.status(); svc.metrics("1h"); svc.metrics("24h"); svc.cost()
    names = {n for n, _ in log}
    assert names <= ALLOWED_READ_CALLS, names - ALLOWED_READ_CALLS
    assert not any(re.match(r"(put|create|update|delete|tag|untag|enable|disable|start|stop|invoke|add|remove)_", n) for n in names)
    assert set(svc._services) <= {"lambda", "dynamodb", "cloudfront", "cognito-idp", "cloudwatch"}
    assert not {"ce", "cost-explorer", "budgets", "billing", "iam", "sts", "organizations", "resource-explorer-2"} & set(svc._services)


def test_resource_ids_in_requests_come_only_from_the_manifest(aws):
    create_fixtures()
    log: list = []
    make_service(log).metrics("1h")
    allowed = {MANIFEST["web_function"], MANIFEST["api_function"], MANIFEST["ddb_table"], MANIFEST["cloudfront_distribution_id"], "Global"}
    for n, kw in log:
        for q in kw.get("MetricDataQueries", []):
            vals = {d["Value"] for d in q["MetricStat"]["Metric"]["Dimensions"]}
            assert vals <= allowed, vals - allowed
            assert q["MetricStat"]["Metric"]["Namespace"] in ("AWS/Lambda", "AWS/DynamoDB", "AWS/CloudFront")     # never a custom namespace


def test_metric_windows_are_fixed_bounded_and_cloudfront_is_queried_in_us_east_1_only(aws):
    log: list = []
    svc = make_service(log, manifest={**MANIFEST, "region": "eu-west-1"})
    svc.metrics("6h")
    calls = [kw for n, kw in log if n == "get_metric_data"]
    assert len(calls) == 2                                                     # one for the manifest region, one for CloudFront (us-east-1)
    for kw in calls:
        span = (kw["EndTime"] - kw["StartTime"]).total_seconds()
        assert span == 21600 and all(q["MetricStat"]["Period"] == 900 for q in kw["MetricDataQueries"]) and len(kw["MetricDataQueries"]) <= 20
        assert 21600 / 900 <= 288


def test_pagination_is_bounded(aws):
    calls = {"n": 0}

    class Endless:
        def get_metric_data(self, **kw):
            calls["n"] += 1
            return {"MetricDataResults": [], "NextToken": "again"}
    svc = OpsService("live", Manifest.parse(json.dumps({**MANIFEST, "cloudfront_distribution_id": None, "user_pool_id": None})), client_factory=lambda s, r: Endless())
    svc.metrics("1h")
    assert calls["n"] == MAX_PAGES


def test_ttl_cache_prevents_aws_overuse_and_single_flights_concurrent_callers(aws):
    create_fixtures()
    clk = {"t": 1_000_000.0}
    log: list = []
    svc = make_service(log, clock=lambda: clk["t"], ttl=60)
    svc.status(); n1 = svc.aws_calls
    for _ in range(50):
        svc.status()
    assert svc.aws_calls == n1                                                 # 50 more page loads, zero extra AWS calls
    clk["t"] += 61
    svc.status()
    assert svc.aws_calls > n1
    import threading
    clk["t"] += 61
    before = svc.aws_calls
    ts = [threading.Thread(target=svc.status) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert svc.aws_calls - before == n1                                          # exactly ONE refresh for eight concurrent page loads


def test_ttl_floor_is_30_seconds():
    assert OpsService("off", ttl_s=1).ttl_s == 30


def test_stale_data_is_served_and_flagged_when_a_refresh_fails(aws):
    create_fixtures()
    clk = {"t": 2_000_000.0}
    fail: dict = {}
    svc = make_service([], clock=lambda: clk["t"], ttl=60, fail=fail)
    first = svc.metrics("1h")
    assert first["provenance"]["stale"] is False
    fail["get_metric_data"] = "ThrottlingException"
    clk["t"] += 120
    again = svc.metrics("1h")
    assert again["provenance"]["stale"] is True and again["provenance"]["age_s"] >= 120 and "groups" in again


def test_live_route_end_to_end_with_scrubbed_output(aws):
    create_fixtures()
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32, admin_subjects="user_admin", aws_ops="live", aws_manifest=json.dumps(MANIFEST), build_sha="deadbee")
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        r = c.get(PATHS[0], headers=hdr("user_admin"))
        assert r.status_code == 200 and r.json()["provenance"]["source"] == "aws" and r.json()["app"]["build_sha"] == "deadbee"
        assert not ARN_OR_ID.search(r.text) and "123456789012" not in r.text
        assert c.get(PATHS[0], headers=hdr("user_bob")).status_code == 403


def test_error_text_from_aws_never_reaches_the_response(aws):
    svc = make_service([], fail={"get_function_configuration": "SomethingWeird", "describe_table": "SomethingWeird"})
    txt = json.dumps(svc.status())
    assert "SomethingWeird" not in txt and "UNKNOWN" in txt


def test_real_datapoints_round_trip_from_cloudwatch_into_the_response(aws):
    """Publish datapoints (emulator) in AWS/Lambda and AWS/DynamoDB for the manifest resources; the reader must return exactly those values, per group."""
    from datetime import datetime, timedelta, timezone
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    now = datetime.now(timezone.utc)
    for i, v in enumerate((3.0, 5.0, 7.0)):
        ts = now - timedelta(minutes=40 - 10 * i)
        cw.put_metric_data(Namespace="AWS/Lambda", MetricData=[{"MetricName": "Invocations", "Dimensions": [{"Name": "FunctionName", "Value": "enigma-api-dev"}], "Timestamp": ts, "Value": v, "Unit": "Count"}])
        cw.put_metric_data(Namespace="AWS/DynamoDB", MetricData=[{"MetricName": "ConsumedWriteCapacityUnits", "Dimensions": [{"Name": "TableName", "Value": "enigma-dev"}], "Timestamp": ts, "Value": v * 10, "Unit": "Count"}])
    m = make_service([]).metrics("1h")
    api = m["groups"]["lambda_api"]
    assert api["state"] == "OK" and sorted(p["value"] for p in api["series"]["invocations"]) == [3.0, 5.0, 7.0]
    assert sorted(p["value"] for p in m["groups"]["dynamodb"]["series"]["write_units"]) == [30.0, 50.0, 70.0]
    assert m["groups"]["lambda_web"]["state"] == "NO_DATA"                      # no data for the web function: stated, not zero-filled
    ts_list = [p["ts"] for p in api["series"]["invocations"]]
    assert ts_list == sorted(ts_list) and all(isinstance(t, int) for t in ts_list)
