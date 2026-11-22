"""Arithmetic invariants of the offline AWS cost model (scripts/aws_cost_model.py). No AWS access, no network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import aws_cost_model as m  # noqa: E402


def test_fixed_database_and_nat_arithmetic_matches_price_list():
    r = m.run()["low"]["list"]
    assert r["track_a"]["RDS db.t4g.micro (730 h) + 20 GB gp3"] == round(730 * 0.016 + 20 * 0.115, 2) == 13.98
    assert round(r["track_a_with_nat"]["total"] - r["track_a"]["total"], 2) == 32.85
    assert r["track_a"]["Networking (no NAT, no IPv4, no endpoints)"] == 0.0


def test_free_tier_never_exceeds_list_and_workloads_are_monotonic():
    res = m.run()
    for name, x in res.items():
        assert x["free"]["track_a"]["total"] <= x["list"]["track_a"]["total"]
        assert x["free"]["track_b"]["total"] <= x["list"]["track_b"]["total"]
    for key in ("track_a", "track_b"):
        t = [res[n]["list"][key]["total"] for n in ("low", "moderate", "high")]
        assert t == sorted(t)


def test_track_b_is_cheaper_than_track_a_at_every_workload_but_only_by_the_database():
    for x in m.run().values():
        assert x["list"]["track_b"]["total"] < x["list"]["track_a"]["total"]
        assert x["list"]["common_subtotal"] == round(x["list"]["track_a"]["total"] - 13.98, 2)


def test_webfunctions_memory_is_capped_at_one_always_on_environment():
    cap = 730 * 3600 * 1.0 * m.P["webfn_gb_s"]
    assert m.webfn_alternative(m.WORKLOADS["high"])["memory_while_alive"] <= round(cap, 2)


def test_connector_polling_dominates_api_volume_and_backoff_cuts_it():
    w = m.WORKLOADS["high"]
    base = m.volumes(w)
    tuned = m.volumes(w, {**m.A, "claim_poll_s": 5.0})
    assert base["kaggle_req"] > base["bff_req"]                      # the connector is the biggest API consumer at the default 1 s claim poll
    assert tuned["kaggle_req"] < base["kaggle_req"] * 0.4            # a 5 s idle claim poll removes most of it
    assert tuned["api_invocations"] < base["api_invocations"] * 0.65  # browser polling (unchanged) is the remainder


def test_idle_backoff_scenario_lowers_api_volume_and_cost_but_not_the_rds_floor():
    for name, c in m.tuned_comparison().items():
        assert c["api_invocations"][1] < c["api_invocations"][0]
        assert c["track_b"][1] <= c["track_b"][0] and c["track_a"][1] <= c["track_a"][0]
    low = m.tuned_comparison()["low"]
    assert low["track_a"][1] > 13.98                                   # the fixed RDS charge is untouched by polling
    assert m.claims_per_h({**m.A, "claim_idle_s": None}) == 3600.0 and m.claims_per_h({**m.A, "claim_idle_s": 5.0}) < 1100


def test_cloudfront_comparison_is_consistent_and_the_difference_is_small_in_dollars():
    c = m.cloudfront_comparison()
    for n, x in c.items():
        assert x["web_invocations"][1] > x["web_invocations"][0]                    # no CDN => more Lambda invocations
        assert abs(x["with_cloudfront"] - x["raw_function_url"]) < 5.0               # the money difference is single-digit dollars at every workload
    assert c["low"]["with_cloudfront"] < 2.0 and c["low"]["raw_function_url"] < 2.0


def test_amplify_alternative_is_itemised_and_priced_at_the_fetched_list_rates():
    for w in m.WORKLOADS.values():
        a = m.amplify_alternative(w)
        assert abs(a["total"] - sum(v for k, v in a.items() if k != "total")) < 0.02      # rounding only
        assert a["data_transfer"] >= 0 and a["builds"] == round(20 * 4.0 * 0.01, 2)
    lo, hi = (m.amplify_alternative(m.WORKLOADS[n]) for n in ("low", "high"))
    assert hi["total"] > lo["total"]
    assert m.P_AMPLIFY["dto_gb"] > m.P["cf_dto_gb"]                                       # Amplify transfer is dearer than CloudFront per GB
