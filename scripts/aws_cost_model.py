"""Itemised monthly AWS cost model for Enigma (region us-east-1). Pure stdlib; offline; NO AWS calls.

    /usr/local/bin/python3.13 scripts/aws_cost_model.py            # markdown tables
    /usr/local/bin/python3.13 scripts/aws_cost_model.py --json

Prices were read on 2026-11-21 from the AWS Price List API (`aws pricing get-products`, us-east-1, on-demand) unless tagged otherwise.
They are LIST prices. Each estimate is shown twice: LIST (no free tier -- the safe upper bound) and FREE (documented always-free
allowances subtracted; whether they apply to THIS PAID-plan account is unverified). A model is not a quote: change ASSUMPTIONS and re-run.
"""
from __future__ import annotations

import json
import sys

HOURS = 730.0
# ---------------------------------------------------------------------------------------------------- prices (USD, us-east-1)
P = {
    "lambda_req_per_m": 0.20, "lambda_gbs_x86": 0.0000166667, "lambda_gbs_arm": 0.0000133334,          # Price List API (AWSLambda)
    "webfn_req_per_m": 0.30, "webfn_vcpu_s": 0.0000276944, "webfn_gb_s": 0.0000036667,                  # Price List API; Web Functions are ARM-only
    "cf_https_per_10k": 0.0100, "cf_dto_gb": 0.085,                                                     # CloudFront pay-as-you-go page (US group)
    "cw_ingest_gb": 0.50, "cw_store_gb_mo": 0.03,                                                       # Price List API (AmazonCloudWatch)
    "cognito_lite": 0.0055, "cognito_essentials": 0.015,                                                # Price List API (AmazonCognito)
    "rds_t4g_micro_h": 0.016, "rds_gp3_gb": 0.115, "rds_backup_gb": 0.095,                              # Price List API (AmazonRDS, PostgreSQL Single-AZ)
    "ddb_wru_per_m": 0.625, "ddb_rru_per_m": 0.125, "ddb_storage_gb": 0.25,                             # Price List API (AmazonDynamoDB)
    "ddb_wcu_h": 0.00065, "ddb_rcu_h": 0.00013,
    "nat_h": 0.045, "nat_gb": 0.045, "ipv4_h": 0.005, "iface_endpoint_h": 0.01, "iface_endpoint_gb": 0.01,  # Price List API (AmazonEC2/AmazonVPC)
    "s3_gb_mo": 0.023, "s3_put_per_1k": 0.005,                                                          # Price List API (AmazonS3)
}
FREE = {"lambda_req": 1_000_000, "lambda_gbs": 400_000, "cf_req": 10_000_000, "cf_gb": 1000, "cw_gb": 5, "cognito_mau": 10_000, "ddb_gb": 25,
        "ddb_wcu": 25, "ddb_rcu": 25}

# ---------------------------------------------------------------------------------------------------- assumptions (all editable)
A = {
    "claim_poll_s": 1.0, "heartbeat_s": 10.0, "upload_s": 5.0,        # connector defaults (connector/enigma_connector/config.py)
    "dash_hooks": 3, "dash_poll_s": 5.0,                              # overview + gpu + vllm hooks (apps/web/components/enigma/use-metrics.ts)
    "chat_poll_s": 0.4,                                               # use-chat.ts POLL_FAST while a reply streams
    "api_mem_gb": 0.5, "api_ms": 50.0,                                # FastAPI+psycopg, short SQL call (ASSUMPTION; measure before approval)
    "web_mem_gb": 1.0, "ms_bff": 60.0, "ms_ssr": 150.0, "ms_asset": 15.0,   # Next.js standalone (ASSUMPTION)
    "assets_per_page": 15, "cdn_hit_ratio": 0.90, "page_mb": 1.0, "bff_kb": 15.0,
    "webfn_cpu_ms_bff": 10.0, "webfn_cpu_ms_ssr": 80.0, "webfn_cpu_ms_asset": 5.0, "webfn_alive_per_active_h": 2.0, "webfn_pv_per_alive_h": 30.0,
    "log_bytes_per_invocation": 700.0, "log_retention_days": 7,
    "rds_storage_gb": 20.0, "az_count": 1,
    "ddb_wru_per_telemetry_batch": 11.0, "ddb_rru_per_dash_req": 30.0, "ddb_wru_per_chat_h": 25_200.0, "ddb_rru_per_claim": 1.0,
}
WORKLOADS = {
    "low":      {"kaggle_h": 10,  "dash_h": 10,  "chat_h": 0.5, "page_views": 500,    "mau": 2},
    "moderate": {"kaggle_h": 60,  "dash_h": 60,  "chat_h": 3,   "page_views": 5_000,  "mau": 5},
    "high":     {"kaggle_h": 130, "dash_h": 130, "chat_h": 10,  "page_views": 30_000, "mau": 25},   # ~Kaggle's 30 GPU-h/week quota
}


def volumes(w: dict, a: dict = A) -> dict:
    kaggle_req_h = 3600 / a["claim_poll_s"] + 3600 / a["heartbeat_s"] + 3600 / a["upload_s"]
    kaggle = w["kaggle_h"] * kaggle_req_h
    bff = w["dash_h"] * a["dash_hooks"] * 3600 / a["dash_poll_s"] + w["chat_h"] * 3600 / a["chat_poll_s"]
    pv = w["page_views"]
    assets_origin = pv * a["assets_per_page"] * (1 - a["cdn_hit_ratio"])
    assets_all = pv * a["assets_per_page"]
    return {"kaggle_req": kaggle, "bff_req": bff, "page_views": pv, "assets_origin": assets_origin, "assets_all": assets_all,
            "api_invocations": kaggle + bff, "web_invocations_cdn": bff + pv + assets_origin, "web_invocations_nocdn": bff + pv + assets_all}


def _lam(n_req: float, gbs: float, rate_gbs: float, free: bool) -> float:
    req = max(n_req - (FREE["lambda_req"] if free else 0), 0)
    g = max(gbs - (FREE["lambda_gbs"] if free else 0), 0)
    return req / 1e6 * P["lambda_req_per_m"] + g * rate_gbs


def estimate(w: dict, free: bool, a: dict = A) -> dict:
    v = volumes(w, a)
    lines: dict[str, float] = {}
    # compute: API (x86 Python Event function) and Web (Next.js standalone via Lambda Web Adapter, ARM64 Event function)
    api_gbs = v["api_invocations"] * a["api_ms"] / 1000 * a["api_mem_gb"]
    web_gbs = (v["bff_req"] * a["ms_bff"] + v["page_views"] * a["ms_ssr"] + v["assets_origin"] * a["ms_asset"]) / 1000 * a["web_mem_gb"]
    api_list = _lam(v["api_invocations"], api_gbs, P["lambda_gbs_x86"], False)
    web_list = _lam(v["web_invocations_cdn"], web_gbs, P["lambda_gbs_arm"], False)
    scale = 1.0
    if free and (api_list + web_list) > 0:
        # the account-wide Lambda free tier (1M requests, 400k GB-s) is shared: apply it ONCE to the combined usage (blended GB-s rate),
        # then split the result in proportion to each function's list cost
        n_req, gbs = v["api_invocations"] + v["web_invocations_cdn"], api_gbs + web_gbs
        blended = (api_gbs * P["lambda_gbs_x86"] + web_gbs * P["lambda_gbs_arm"]) / gbs
        scale = _lam(n_req, gbs, blended, True) / (api_list + web_list)
    lines["API Lambda (Python 3.13, 512 MB)"] = api_list * scale
    lines["Web Lambda (Next 16 via Web Adapter, ARM64, 1 GB)"] = web_list * scale
    lines["Lambda Function URLs (2)"] = 0.0                           # no separate charge found on the pricing pages (UNVERIFIED absence)
    # CloudFront in front of the web function
    viewer_req = v["bff_req"] + v["page_views"] * (1 + a["assets_per_page"])
    gb_out = (v["page_views"] * a["page_mb"] + v["bff_req"] * a["bff_kb"] / 1024) / 1024
    cf_req = max(viewer_req - (FREE["cf_req"] if free else 0), 0) / 10_000 * P["cf_https_per_10k"]
    cf_dto = max(gb_out - (FREE["cf_gb"] if free else 0), 0) * P["cf_dto_gb"]
    lines["CloudFront (requests + transfer out)"] = cf_req + cf_dto
    lines["S3 (deploy artifacts ~0.1 GB)"] = 0.1 * P["s3_gb_mo"] + 4 * P["s3_put_per_1k"] / 1000
    inv = v["api_invocations"] + v["web_invocations_cdn"]
    ingest_gb = inv * a["log_bytes_per_invocation"] / 1e9
    stored_gb = ingest_gb * a["log_retention_days"] / 30
    lines["CloudWatch Logs (7-day retention)"] = max(ingest_gb - (FREE["cw_gb"] if free else 0), 0) * P["cw_ingest_gb"] + stored_gb * P["cw_store_gb_mo"]
    lines["Cognito (Lite tier)"] = max(w["mau"] - (FREE["cognito_mau"] if free else 0), 0) * P["cognito_lite"]
    common = sum(lines.values())
    # ---- Track A: RDS PostgreSQL, private, Lambda in VPC, NO NAT (pinned JWKS + DSN in encrypted env var)
    rds = a["rds_storage_gb"] * P["rds_gp3_gb"] + HOURS * P["rds_t4g_micro_h"]       # backups: free allocation = provisioned size (not exceeded)
    net_a = 0.0
    # ---- Track B: DynamoDB (design only; NOT implemented)
    wru = w["kaggle_h"] * (3600 / a["upload_s"] * a["ddb_wru_per_telemetry_batch"] + 3600 / a["heartbeat_s"]) + w["chat_h"] * a["ddb_wru_per_chat_h"]
    rru = w["kaggle_h"] * 3600 / a["claim_poll_s"] * a["ddb_rru_per_claim"] + (w["dash_h"] * a["dash_hooks"] * 3600 / a["dash_poll_s"]) * a["ddb_rru_per_dash_req"]
    ddb_ondemand = wru / 1e6 * P["ddb_wru_per_m"] + rru / 1e6 * P["ddb_rru_per_m"]
    peak_rcu = a["dash_hooks"] / a["dash_poll_s"] * a["ddb_rru_per_dash_req"]                 # sustained RCU/s while one dashboard is open
    ddb_prov = 0.0 if (peak_rcu <= FREE["ddb_rcu"] and free) else ddb_ondemand
    ddb = ddb_prov if free else ddb_ondemand
    return {
        "volumes": {k: round(x) for k, x in v.items()},
        "lines": {k: round(x, 2) for k, x in lines.items()},
        "common_subtotal": round(common, 2),
        "track_a": {"RDS db.t4g.micro (730 h) + 20 GB gp3": round(rds, 2), "Networking (no NAT, no IPv4, no endpoints)": net_a,
                    "total": round(common + rds + net_a, 2)},
        "track_a_with_nat": {"NAT gateway (730 h)": round(HOURS * P["nat_h"], 2), "total": round(common + rds + HOURS * P["nat_h"], 2)},
        "track_b": {"DynamoDB (design only)": round(ddb, 2), "total": round(common + ddb, 2), "peak_sustained_rcu": round(peak_rcu, 1)},
    }


def webfn_alternative(w: dict, a: dict = A) -> dict:
    """Same Next.js tier on Lambda Web Functions (Node, ARM, active-CPU billing). LIST prices, no free tier (none documented)."""
    v = volumes(w, a)
    reqs = v["web_invocations_cdn"]
    cpu_s = (v["bff_req"] * a["webfn_cpu_ms_bff"] + v["page_views"] * a["webfn_cpu_ms_ssr"] + v["assets_origin"] * a["webfn_cpu_ms_asset"]) / 1000
    alive_h = min(a["webfn_alive_per_active_h"] * (w["dash_h"] + w["chat_h"]) + w["page_views"] / a["webfn_pv_per_alive_h"], HOURS)   # single env, cap: one always-on environment
    mem_s = alive_h * 3600 * a["web_mem_gb"]
    return {"requests": round(reqs / 1e6 * P["webfn_req_per_m"], 2), "active_cpu": round(cpu_s * P["webfn_vcpu_s"], 2),
            "memory_while_alive": round(mem_s * P["webfn_gb_s"], 2),
            "total": round(reqs / 1e6 * P["webfn_req_per_m"] + cpu_s * P["webfn_vcpu_s"] + mem_s * P["webfn_gb_s"], 2)}


def run() -> dict:
    return {name: {"workload": w, "list": estimate(w, False), "free": estimate(w, True), "webfn_alt": webfn_alternative(w)}
            for name, w in WORKLOADS.items()}


def markdown() -> str:
    r = run()
    out: list[str] = []
    out.append("| Workload | Kaggle GPU h/mo | dashboard-open h | chat-streaming h | page views | MAU | API invocations | Web invocations (with CDN) |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for n, x in r.items():
        w, v = x["workload"], x["list"]["volumes"]
        out.append(f"| {n} | {w['kaggle_h']} | {w['dash_h']} | {w['chat_h']} | {w['page_views']:,} | {w['mau']} | {v['api_invocations']:,} | {v['web_invocations_cdn']:,} |")
    for n, x in r.items():
        out.append(f"\n**{n}** — USD / month, us-east-1\n")
        out.append("| Line item | LIST (no free tier) | With documented free tiers |")
        out.append("|---|---:|---:|")
        for k in x["list"]["lines"]:
            out.append(f"| {k} | {x['list']['lines'][k]:.2f} | {x['free']['lines'][k]:.2f} |")
        out.append(f"| *Subtotal common to both tracks* | *{x['list']['common_subtotal']:.2f}* | *{x['free']['common_subtotal']:.2f}* |")
        out.append(f"| **Track A** RDS db.t4g.micro + 20 GB gp3 (private, no NAT/IPv4/endpoints) | {x['list']['track_a']['RDS db.t4g.micro (730 h) + 20 GB gp3']:.2f} | {x['free']['track_a']['RDS db.t4g.micro (730 h) + 20 GB gp3']:.2f} |")
        out.append(f"| **Track A total** | **{x['list']['track_a']['total']:.2f}** | **{x['free']['track_a']['total']:.2f}** |")
        out.append(f"| Track A if a NAT gateway were needed (+32.85) | {x['list']['track_a_with_nat']['total']:.2f} | {x['free']['track_a_with_nat']['total']:.2f} |")
        out.append(f"| **Track B** DynamoDB (design only) | {x['list']['track_b']['DynamoDB (design only)']:.2f} | {x['free']['track_b']['DynamoDB (design only)']:.2f} |")
        out.append(f"| **Track B total** | **{x['list']['track_b']['total']:.2f}** | **{x['free']['track_b']['total']:.2f}** |")
        wf = x["webfn_alt"]
        out.append(f"| Alternative web tier on Lambda Web Functions (replaces the Web Lambda line; list) | {wf['total']:.2f} (req {wf['requests']:.2f} + CPU {wf['active_cpu']:.2f} + mem {wf['memory_while_alive']:.2f}) | n/a |")
    return "\n".join(out)


if __name__ == "__main__":
    print(json.dumps(run(), indent=1) if "--json" in sys.argv else markdown())
