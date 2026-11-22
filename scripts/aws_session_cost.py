"""Session-based monthly AWS cost estimator for Enigma (Path A: Lambda Web Adapter + API Lambda + DynamoDB on-demand + Cognito). OFFLINE: no AWS calls.

    /usr/local/bin/python3.13 scripts/aws_session_cost.py                      # 1 h, 2 h, 10 x 1 h, 10 x 2 h, idle month
    /usr/local/bin/python3.13 scripts/aws_session_cost.py --sessions 8 --hours 1.5 [--json]

Model: AWS bills only while Kaggle (or a browser) is sending requests. A month is `sessions x hours` of connector traffic plus browser traffic,
plus a small always-on part (S3 artifact, retained logs, DynamoDB storage). Prices are the LIST prices in scripts/aws_cost_model.py (us-east-1, Price
List API 2026-11-21); LIST = no free tier (safe upper bound), FREE = documented always-free allowances (applicability to this PAID-plan account is
UNVERIFIED). Request/duration/DynamoDB-unit figures are ESTIMATES from reading the code (see ASSUMPTIONS); measure before relying on them. NOT a quote.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aws_cost_model import FREE, HOURS, P  # noqa: E402  (single source of truth for prices)

P_DTO_GB = 0.09          # Lambda Function URL / data transfer to the internet, US East (AWS public pricing; first 100 GB/month free account-wide)
FREE_DTO_GB = 100.0
P_DDB_PROV_WCU_H, P_DDB_PROV_RCU_H = P["ddb_wcu_h"], P["ddb_rcu_h"]

# ---------------------------------------------------------------------------------------------------- assumptions (edit and re-run)
A = {
    # connector cadence: MUST equal connector/enigma_connector/config.py defaults (tests/test_aws_session_cost.py enforces it)
    "claim_poll_s": 1.0, "claim_poll_idle_s": 5.0, "claim_hot_window_s": 30.0, "heartbeat_s": 10.0, "upload_s": 5.0, "flush_interval_s": 0.15,
    # a session's activity
    "prompts_per_session_h": 12.0, "stream_s_per_prompt": 10.0,           # ~12 prompts/hour, each streaming ~10 s
    "dash_open_fraction": 1.0, "dash_hooks": 4, "dash_poll_s": 5.0,      # worst case: dashboard open the whole session; 3 metric hooks + runtime list
    "chat_poll_s": 0.4,                                                   # hooks/use-chat.ts POLL_FAST while a reply streams (no Realtime on AWS)
    "page_views_per_session": 5.0, "assets_per_page": 15, "page_mb": 1.0, "bff_kb": 15.0,
    "offline_visits_per_month": 10.0, "offline_visit_min": 5.0, "offline_runtime_poll_s": 30.0,   # reading history while no session is running
    # Lambda
    "api_mem_gb": 0.5, "api_ms": 50.0, "web_mem_gb": 1.0, "ms_bff": 60.0, "ms_ssr": 150.0, "ms_asset": 15.0,
    "log_bytes_per_invocation": 700.0, "log_retention_days": 7,
    # DynamoDB units per request (ESTIMATES from apps/api/enigma_api/store/dynamodb.py: every runtime call reads the credential (1 RRU) and the runtime (1 RRU)
    # and touches the credential (1 WRU); a claim also queries the job queue twice (expire + select); telemetry writes ~10 rows + 1 batch marker)
    "rru_claim": 5.0, "wru_claim": 1.0, "rru_heartbeat": 2.0, "wru_heartbeat": 2.0, "rru_telemetry": 2.0, "wru_telemetry": 13.0,
    "rru_events": 3.0, "wru_events": 3.0, "rru_dash": 23.0, "rru_chat_poll": 3.0, "rru_other_bff": 4.0, "rru_misc": 4.0, "wru_misc": 6.0,   # rru_dash: avg of 3 metric reads (~30) + 1 runtime list (~3)
    "ddb_item_bytes_telemetry_session_h": 2_200_000, "ddb_telemetry_ttl_days": 14,    # 720 batches x ~12 rows x ~250 B
    "s3_gb": 0.1, "mau": 1,
}
SCENARIOS = {"1 session x 1 h": (1, 1.0), "1 session x 2 h": (1, 2.0), "10 sessions x 1 h": (10, 1.0), "10 sessions x 2 h": (10, 2.0), "idle month (0 sessions)": (0, 0.0)}


def hot_fraction(a: dict, hours: float) -> float:
    """Share of the session spent in the fast-claim window: session start + every prompt (stream time + the 30 s hot window that follows)."""
    if hours <= 0:
        return 0.0
    hot_s = a["claim_hot_window_s"] + a["prompts_per_session_h"] * hours * (a["stream_s_per_prompt"] + a["claim_hot_window_s"])
    return min(1.0, hot_s / (hours * 3600))


def volumes(sessions: float, hours: float, a: dict = A) -> dict:
    h = sessions * hours                                             # connector hours per month
    hot = hot_fraction(a, hours)
    claims = h * 3600 * (hot / a["claim_poll_s"] + (1 - hot) / max(a["claim_poll_idle_s"] or a["claim_poll_s"], a["claim_poll_s"]))
    hb, tel = h * 3600 / a["heartbeat_s"], h * 3600 / a["upload_s"]
    prompts = a["prompts_per_session_h"] * h
    stream_s = prompts * a["stream_s_per_prompt"]
    events = stream_s / a["flush_interval_s"] + prompts * 3                      # upper bound: a flush every 150 ms while streaming, + complete/claim bookkeeping
    dash = h * a["dash_open_fraction"] * 3600 / a["dash_poll_s"] * a["dash_hooks"]
    chat_polls = stream_s / a["chat_poll_s"]
    visits = a["offline_visits_per_month"]
    offline_polls = visits * a["offline_visit_min"] * 60 / a["offline_runtime_poll_s"]
    pages = sessions * a["page_views_per_session"] + visits
    bff = dash + chat_polls + offline_polls + prompts * 2 + pages * 6              # SSR pages also call the API a few times (me, workspaces, projects, runtimes...)
    api_inv = claims + hb + tel + events + bff
    assets = pages * a["assets_per_page"]
    web_inv = bff + pages + assets
    other = prompts * 2 + pages * 6 + offline_polls
    return {"dash_requests": dash, "chat_polls": chat_polls, "other_bff": other, "claims": claims, "heartbeats": hb, "telemetry_batches": tel, "event_posts": events, "bff_requests": bff, "page_views": pages,
            "api_invocations": api_inv, "web_invocations": web_inv, "connector_hours": h, "prompts": prompts}


def ddb_units(v: dict, a: dict = A) -> tuple[float, float]:
    rru = (v["claims"] * a["rru_claim"] + v["heartbeats"] * a["rru_heartbeat"] + v["telemetry_batches"] * a["rru_telemetry"] + v["event_posts"] * a["rru_events"]
           + v["dash_requests"] * a["rru_dash"] + v["chat_polls"] * a["rru_chat_poll"] + v["other_bff"] * a["rru_other_bff"] + v["prompts"] * a["rru_misc"])
    wru = (v["claims"] * a["wru_claim"] + v["heartbeats"] * a["wru_heartbeat"] + v["telemetry_batches"] * a["wru_telemetry"] + v["event_posts"] * a["wru_events"]
           + v["prompts"] * a["wru_misc"])
    return rru, wru


def lam(n_req: float, gbs: float, rate_gbs: float, free: bool) -> float:
    return max(n_req - (FREE["lambda_req"] if free else 0), 0) / 1e6 * P["lambda_req_per_m"] + max(gbs - (FREE["lambda_gbs"] if free else 0), 0) * rate_gbs


def estimate(sessions: float, hours: float, free: bool, a: dict = A) -> dict:
    v = volumes(sessions, hours, a)
    api_gbs = v["api_invocations"] * a["api_ms"] / 1000 * a["api_mem_gb"]
    web_gbs = (v["bff_requests"] * a["ms_bff"] + v["page_views"] * a["ms_ssr"] + (v["web_invocations"] - v["bff_requests"] - v["page_views"]) * a["ms_asset"]) / 1000 * a["web_mem_gb"]
    n_req, gbs = v["api_invocations"] + v["web_invocations"], api_gbs + web_gbs
    # account-wide Lambda free tier applied ONCE to the combined usage at the blended rate
    blended = (api_gbs * P["lambda_gbs_x86"] + web_gbs * P["lambda_gbs_arm"]) / gbs if gbs else 0.0
    lambda_cost = lam(n_req, gbs, blended, free)
    rru, wru = ddb_units(v, a)
    ddb_od = rru / 1e6 * P["ddb_rru_per_m"] + wru / 1e6 * P["ddb_wru_per_m"]
    stored_gb = sessions * hours * a["ddb_item_bytes_telemetry_session_h"] / 1e9            # telemetry rows live ttl_days; events 48 h; negligible vs telemetry
    ddb_store = max(stored_gb - (FREE["ddb_gb"] if free else 0), 0) * P["ddb_storage_gb"]
    ingest_gb = n_req * a["log_bytes_per_invocation"] / 1e9
    logs = max(ingest_gb - (FREE["cw_gb"] if free else 0), 0) * P["cw_ingest_gb"] + ingest_gb * a["log_retention_days"] / 30 * P["cw_store_gb_mo"]
    out_gb = (v["page_views"] * a["page_mb"] + v["bff_requests"] * a["bff_kb"] / 1024) / 1024
    transfer = max(out_gb - (FREE_DTO_GB if free else 0), 0) * P_DTO_GB
    cognito = max(a["mau"] - (FREE["cognito_mau"] if free else 0), 0) * P["cognito_lite"]
    s3 = a["s3_gb"] * P["s3_gb_mo"]
    lines = {"Lambda (API + web: requests + GB-s)": lambda_cost, "DynamoDB on-demand (reads + writes)": ddb_od, "DynamoDB storage": ddb_store,
             "CloudWatch Logs (ingest + 7-day storage)": logs, "Data transfer out (Function URLs)": transfer, "Cognito Lite (MAU)": cognito, "S3 artifact": s3}
    return {"volumes": {k: round(x) for k, x in v.items()}, "ddb_units": {"rru": round(rru), "wru": round(wru)}, "lines": {k: round(x, 4) for k, x in lines.items()},
            "total": round(sum(lines.values()), 4), "transfer_gb": round(out_gb, 3), "logs_gb": round(ingest_gb, 4)}


def ddb_capacity_comparison(sessions: float, hours: float, a: dict = A) -> dict:
    """On-demand vs provisioned for the same month. Provisioned bills every hour of the month whether or not Kaggle is running."""
    v = volumes(sessions, hours, a)
    rru, wru = ddb_units(v, a)
    od = rru / 1e6 * P["ddb_rru_per_m"] + wru / 1e6 * P["ddb_wru_per_m"]
    peak_wcu = a["wru_telemetry"] / a["upload_s"] + 1 / a["flush_interval_s"] * a["wru_events"] + 1 + 1 / a["heartbeat_s"] * a["wru_heartbeat"]   # during a stream
    prov = lambda r, w: HOURS * (r * P_DDB_PROV_RCU_H + w * P_DDB_PROV_WCU_H)   # noqa: E731
    return {"on_demand_usd": round(od, 4), "provisioned_1_1_usd": round(prov(1, 1), 2), "provisioned_25_25_list_usd": round(prov(25, 25), 2),
            "provisioned_25_25_with_free_tier_usd": 0.0, "peak_write_units_per_s_while_streaming": round(peak_wcu, 1),
            "verdict": "on-demand (pay per request, $0 while idle) unless the 25/25 always-free allowance is CONFIRMED for this account AND peak writes stay under 25/s"}


def abuse_exposure_per_million_junk_requests(a: dict = A) -> float:
    """Unauthenticated requests are rejected before any DynamoDB access (malformed tokens/JWTs fail in-process); cost is Lambda only (~10 ms @ 512 MB)."""
    return round(0.20 + 1e6 * 0.010 * a["api_mem_gb"] * P["lambda_gbs_x86"] + 1e6 * 400 / 1e9 * (P["cw_ingest_gb"]), 3)


def run(sessions: float | None = None, hours: float | None = None) -> dict:
    sc = SCENARIOS if sessions is None else {f"{sessions:g} sessions x {hours:g} h": (sessions, hours)}
    return {"scenarios": {n: {"list": estimate(s, h, False), "free_tier": estimate(s, h, True)} for n, (s, h) in sc.items()},
            "ddb_capacity": {n: ddb_capacity_comparison(s, h) for n, (s, h) in sc.items() if s},
            "abuse_usd_per_million_junk_requests": abuse_exposure_per_million_junk_requests()}


def markdown(r: dict) -> str:
    out = ["| Scenario | LIST (USD/mo) | With free tiers | API invocations | Web invocations | DDB RRU / WRU |", "|---|---:|---:|---:|---:|---:|"]
    for n, s in r["scenarios"].items():
        v, u = s["list"]["volumes"], s["list"]["ddb_units"]
        out.append(f"| {n} | {s['list']['total']:.2f} | {s['free_tier']['total']:.2f} | {v['api_invocations']:,} | {v['web_invocations']:,} | {u['rru']:,} / {u['wru']:,} |")
    out += ["", "LIST line items (USD/month):", "", "| Scenario | " + " | ".join(next(iter(r["scenarios"].values()))["list"]["lines"]) + " |", "|---|" + "---:|" * 7]
    for n, s in r["scenarios"].items():
        out.append(f"| {n} | " + " | ".join(f"{x:.4f}" for x in s["list"]["lines"].values()) + " |")
    out += ["", "DynamoDB capacity mode:", ""]
    for n, c in r["ddb_capacity"].items():
        out.append(f"- {n}: on-demand ${c['on_demand_usd']} vs provisioned 1/1 ${c['provisioned_1_1_usd']}, 25/25 ${c['provisioned_25_25_list_usd']} (list) - peak writes {c['peak_write_units_per_s_while_streaming']}/s")
    out.append(f"\nJunk-request exposure: about ${r['abuse_usd_per_million_junk_requests']} per million unauthenticated requests (Lambda + one log line; no DynamoDB access).")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=float)
    ap.add_argument("--hours", type=float)
    ap.add_argument("--json", action="store_true")
    a_ = ap.parse_args()
    if (a_.sessions is None) != (a_.hours is None):
        ap.error("--sessions and --hours go together")
    res = run(a_.sessions, a_.hours)
    print(json.dumps(res, indent=2) if a_.json else markdown(res))
