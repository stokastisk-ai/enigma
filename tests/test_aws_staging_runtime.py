"""scripts/aws_staging_runtime.py on moto (no real AWS): provisions a REAL (non-mock) staging runtime without ever printing its token, and a submitted
staging Playground job is reported as completed only after a runtime actually claimed, streamed and completed it."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
import threading
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "tests" / "api")]


@pytest.fixture
def mod(tmp_path, monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    spec = importlib.util.spec_from_file_location("aws_staging_runtime", ROOT / "scripts" / "aws_staging_runtime.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.LOCAL, m.TOKEN_FILE, m.STATE_FILE = tmp_path, tmp_path / "tok.txt", tmp_path / "state.json"
    m.ROOT = tmp_path.parent
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName=m.TABLE, BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
        yield m


def test_provision_writes_a_private_real_runtime_token_and_never_prints_it(mod, capsys):
    c = mod._client()
    assert mod.provision(c, rotate=False) == 0
    out = capsys.readouterr().out
    tok = mod.TOKEN_FILE.read_text().strip()
    assert tok.startswith("enig_rt_") and len(tok) == 60 and tok not in out
    assert stat.S_IMODE(mod.TOKEN_FILE.stat().st_mode) == 0o600
    st = json.loads(mod.STATE_FILE.read_text())
    assert st["is_mock"] is False and st["table"] == "enigma-rt-staging"
    me = c.get("/api/v1/runtimes/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["runtime_id"] == st["runtime_id"] and me["is_mock"] is False
    assert mod.provision(c, rotate=False) == 0 and mod.TOKEN_FILE.read_text().strip() == tok        # idempotent unless --rotate


def test_submit_reports_completion_only_after_a_runtime_completed_the_job(mod, capsys, monkeypatch):
    c = mod._client()
    mod.provision(c, rotate=False)
    tok = mod.TOKEN_FILE.read_text().strip()
    rh = {"Authorization": f"Bearer {tok}", "X-Enigma-Instance": "kaggle-1"}
    gpus = [{"index": 0, "uuid": "GPU-1", "name": "Tesla T4", "memory_total_mib": 15360}]
    assert c.post("/api/v1/runtimes/register", json={"connector_version": "t", "is_mock": False, "served_model": "m", "tp": 1, "pp": 1,
                                                     "max_model_len": 2048, "gpus": gpus}, headers=rh).status_code == 200

    def gpu_side():
        for _ in range(100):
            job = c.post("/api/v1/jobs/claim", json={}, headers=rh).json()["job"]
            if job:
                c.post(f"/api/v1/jobs/{job['job_id']}/events", json={"events": [{"event_seq": 1, "kind": "delta", "text": "one two"}]}, headers=rh)
                c.post(f"/api/v1/jobs/{job['job_id']}/complete", json={"final_seq": 1, "text_sha256": hashlib.sha256(b"one two").hexdigest()},
                       headers=rh)
                return
            time.sleep(0.05)

    t = threading.Thread(target=gpu_side)
    t.start()
    rc = mod.submit(c, "count", wait_s=20, poll_s=0.05)
    t.join()
    out = capsys.readouterr().out
    assert rc == 0 and "STAGING_PLAYGROUND_JOB_PASS" in out and '"one two"' in out and tok not in out


def test_submit_is_refused_while_the_runtime_is_offline(mod, capsys):
    c = mod._client()
    mod.provision(c, rotate=False)
    assert mod.submit(c, "count", wait_s=0.2, poll_s=0.05) == 1
    assert "STAGING_PLAYGROUND_JOB_NOT_SUBMITTED" in capsys.readouterr().out


def test_an_unclaimed_job_is_not_reported_as_completed(mod, capsys):
    c = mod._client()
    mod.provision(c, rotate=False)
    rh = {"Authorization": f"Bearer {mod.TOKEN_FILE.read_text().strip()}", "X-Enigma-Instance": "kaggle-1"}
    gpus = [{"index": 0, "uuid": "GPU-1", "name": "Tesla T4", "memory_total_mib": 15360}]
    c.post("/api/v1/runtimes/register", json={"connector_version": "t", "is_mock": False, "served_model": "m", "tp": 1, "pp": 1, "max_model_len": 2048,
                                              "gpus": gpus}, headers=rh)
    assert mod.submit(c, "count", wait_s=0.3, poll_s=0.05) == 1
    assert "STAGING_PLAYGROUND_JOB_NOT_COMPLETED" in capsys.readouterr().out
