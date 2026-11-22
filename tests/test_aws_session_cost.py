"""The session cost estimator stays tied to the real connector defaults and behaves sanely. Offline; no AWS."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "connector"))
import aws_session_cost as M  # noqa: E402
from enigma_connector.config import Config  # noqa: E402

TOKEN = "enig_rt_abcd1234_" + "A" * 43


def test_assumed_connector_cadence_equals_the_real_defaults():
    c = Config(api_url="https://x.example", token=TOKEN, instance_id="i1")
    a = M.A
    assert (a["claim_poll_s"], a["claim_poll_idle_s"], a["claim_hot_window_s"]) == (c.claim_poll_s, c.claim_poll_idle_s, c.claim_hot_window_s)
    assert (a["heartbeat_s"], a["upload_s"], a["flush_interval_s"]) == (c.heartbeat_interval_s, c.upload_interval_s, c.flush_interval_s)


def test_idle_month_costs_pennies_and_has_no_connector_traffic():
    r = M.estimate(0, 0.0, False)
    v = r["volumes"]
    assert v["claims"] == v["heartbeats"] == v["telemetry_batches"] == v["event_posts"] == 0
    assert r["total"] < 0.05


def test_cost_scales_with_sessions_and_free_tier_never_costs_more():
    one, ten = M.estimate(1, 2.0, False)["total"], M.estimate(10, 2.0, False)["total"]
    assert 0 < one < ten < 10 * one + 0.01
    for s, h in ((1, 1.0), (10, 2.0)):
        assert M.estimate(s, h, True)["total"] <= M.estimate(s, h, False)["total"]


def test_idle_backoff_cuts_claim_polls():
    fast = dict(M.A, claim_poll_idle_s=0.0)
    assert M.volumes(1, 2.0)["claims"] < M.volumes(1, 2.0, fast)["claims"] * 0.5


def test_on_demand_beats_always_on_provisioned_for_a_session_workload():
    c = M.ddb_capacity_comparison(10, 2.0)
    assert c["on_demand_usd"] < c["provisioned_1_1_usd"] < c["provisioned_25_25_list_usd"]
    assert "on-demand" in c["verdict"]


def test_no_aws_compute_line_items_other_than_lambda():
    names = " ".join(M.estimate(10, 2.0, False)["lines"]).lower()
    for banned in ("ec2", "rds", "nat", "alb", "gpu", "provisioned concurrency", "waf", "cloudfront"):
        assert banned not in names
