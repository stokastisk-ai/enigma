"""Diagnostic ingestion Lambda (infra/aws/diag_ingest/handler.py) against moto: authentication, quotas, validation, redaction, idempotency.
No AWS account, network or credential is used."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diag_handler", ROOT / "infra/aws/diag_ingest/handler.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

KID, SECRET = "abcd1234", "S" * 43
KEY = f"edx_{KID}_{SECRET}"


@pytest.fixture
def ddb(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
                 "AWS_SHARED_CREDENTIALS_FILE": "/dev/null", "AWS_CONFIG_FILE": "/dev/null"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        c.create_table(TableName=H.TABLE, KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                       AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
                       BillingMode="PROVISIONED", ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1})
        put_key(c)
        yield c


def put_key(c, kid=KID, secret=SECRET, max_events=50, expires_in=3600, revoked=False):
    c.put_item(TableName=H.TABLE, Item={"PK": {"S": f"DIAGKEY#{kid}"}, "SK": {"S": "#"}, "secret_hash": {"S": hashlib.sha256(secret.encode()).hexdigest()},
                                        "max_events": {"N": str(max_events)}, "used": {"N": "0"}, "revoked": {"BOOL": revoked},
                                        "expires_at": {"N": str(int(time.time()) + expires_in)}})


def ev(seq=1, **kw):
    return {"attempt": "20261127T000000Z-abc", "nb": "01_platform", "seq": seq, "marker": "before_railway", "utc": "2026-11-27T00:00:00Z", **kw}


def req(events, key=KEY, method="POST", raw=None):
    return {"requestContext": {"http": {"method": method}}, "headers": {"Authorization": f"Bearer {key}"},
            "body": raw if raw is not None else json.dumps({"events": events})}


def call(ddb, *a, **k):
    r = H.handle(req(*a, **k), ddb)
    return r["statusCode"], json.loads(r["body"])


def test_valid_events_are_stored_and_acknowledged_only_after_the_write(ddb):
    assert call(ddb, [ev(1), ev(2, phase="after", http=200, latency_s=0.12)]) == (200, {"stored": 2, "duplicates": 0})
    items = ddb.query(TableName=H.TABLE, KeyConditionExpression="PK = :p", ExpressionAttributeValues={":p": {"S": "ATT#20261127T000000Z-abc"}})["Items"]
    assert [i["SK"]["S"] for i in items] == ["EVT#00000001", "EVT#00000002"] and "ttl" in items[0]


def test_resubmission_is_a_duplicate_never_an_overwrite(ddb):
    call(ddb, [ev(1, note="first")])
    assert call(ddb, [ev(1, note="second")]) == (200, {"stored": 0, "duplicates": 1})
    item = ddb.get_item(TableName=H.TABLE, Key={"PK": {"S": "ATT#20261127T000000Z-abc"}, "SK": {"S": "EVT#00000001"}})["Item"]
    assert item["note"]["S"] == "first"


@pytest.mark.parametrize("key", ["", "edx_abcd1234_" + "X" * 43, "edx_zzzz9999_" + SECRET, "enig_rt_abcd1234_" + SECRET, "Bearer"])
def test_wrong_unknown_or_malformed_keys_get_the_same_401(ddb, key):
    assert call(ddb, [ev()], key=key) == (401, {"error": "unauthorized"})


def test_expired_and_revoked_keys_are_refused(ddb):
    put_key(ddb, kid="expired1", secret="E" * 43, expires_in=-10)
    put_key(ddb, kid="revoked1", secret="R" * 43, revoked=True)
    assert call(ddb, [ev()], key="edx_expired1_" + "E" * 43)[0] == 401
    assert call(ddb, [ev()], key="edx_revoked1_" + "R" * 43)[0] == 401


def test_quota_is_atomic_and_exhaustion_is_429(ddb):
    put_key(ddb, kid="small001", secret="Q" * 43, max_events=3)
    k = "edx_small001_" + "Q" * 43
    assert call(ddb, [ev(1), ev(2)], key=k)[0] == 200
    assert call(ddb, [ev(3), ev(4)], key=k) == (429, {"error": "quota_exhausted"})          # 2 + 2 > 3: nothing written
    assert call(ddb, [ev(3)], key=k)[0] == 200
    assert call(ddb, [ev(5)], key=k)[0] == 429


@pytest.mark.parametrize("bad", [
    {"secret": "x"},                                          # unknown field
    {"note": "x" * 301},                                      # too long
    {"note": "token enig_rt_abcd1234_" + "A" * 43},           # runtime key
    {"note": "AKIA" + "A" * 16},                              # AWS access key id
    {"note": "Authorization: Bearer abcdefghijkl"},           # bearer token
    {"note": "postgresql://u:p@h/db"},                        # connection string
    {"http": "200"}, {"seq": -1}, {"latency_s": float("nan")}, {"attempt": "../../etc"},
])
def test_invalid_or_secret_bearing_events_are_refused_and_do_not_consume_quota(ddb, bad):
    assert call(ddb, [ev(**bad)])[0] == 400
    item = ddb.get_item(TableName=H.TABLE, Key={"PK": {"S": f"DIAGKEY#{KID}"}, "SK": {"S": "#"}})["Item"]
    assert item["used"]["N"] == "0"


def test_size_count_and_method_limits(ddb):
    assert call(ddb, [ev(i) for i in range(26)])[0] == 400
    assert call(ddb, [], raw="x" * (H.MAX_BODY + 1))[0] == 413
    assert call(ddb, [ev()], method="GET")[0] == 405
    assert call(ddb, [], raw="not json")[0] == 400


def test_internal_failure_is_a_503_and_never_an_acknowledgement(ddb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("table gone")
    monkeypatch.setattr(ddb, "put_item", boom)
    H._DDB = ddb
    r = H.lambda_handler(req([ev()]), None)
    assert r["statusCode"] == 503 and "stored" not in r["body"]
