# Runtime-endpoint capability probe (shared; generated from scripts/kaggle_cells/capability.py). ONE bounded, unauthenticated GET of the SELECTED
# endpoint's public /api/v1/health: https only, an explicit host allow-list, TLS verification, no redirect, no credential header, a bounded body and a
# bounded WALL-CLOCK time. Four results are reported independently and an HTTP 200 alone is never readiness:
#   CAPABILITY_HTTP_RESULT              the request itself (status / transport error class)
#   CAPABILITY_SCHEMA_RESULT            the body is the expected JSON object with typed fields and the expected API contract
#   CAPABILITY_DEPLOYMENT_RESULT        deployment label, store, auth provider AND the data-plane fingerprint equal the values expected for the mode
#   CAPABILITY_DURABLE_EVIDENCE_RESULT  AWS acknowledged BOTH the before- and the after-probe diagnostic markers
import ssl, threading, urllib.error, urllib.parse, urllib.request

HEALTH_SCHEMA = {"ok": bool, "version": str, "api_contract": int, "store": str, "auth": str, "deployment": str, "data_plane": str}


def _cap_opener():
    class _NoRedirectCap(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    return urllib.request.build_opener(_NoRedirectCap(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def probe_health(base_url, allowed_host, wall_timeout=20.0, max_bytes=4096, opener=None):
    """Returns {"http": {...}, "body": dict|None, "latency_s": float}. Never raises; never sends a credential."""
    u = urllib.parse.urlsplit(base_url)
    if u.scheme != "https" or u.hostname != allowed_host or u.username or u.password or u.port not in (None, 443):
        return {"http": {"result": "FAIL", "error": "endpoint_not_allowed"}, "body": None, "latency_s": 0.0}
    req = urllib.request.Request(base_url.rstrip("/") + "/api/v1/health", method="GET",
                                 headers={"User-Agent": "Enigma-Kaggle-Capability/7.0", "Accept": "application/json"})
    assert not any(k.lower() in ("authorization", "cookie") for k, _ in req.header_items()), "a public health probe never carries a credential"
    box, t0 = {}, time.monotonic()

    def run():
        try:
            with (opener or _cap_opener()).open(req, timeout=max(1.0, wall_timeout - 1.0)) as r:
                box["status"], box["raw"] = r.status, r.read(max_bytes + 1)
        except urllib.error.HTTPError as e:
            box["status"] = e.code                        # 3xx lands here too: redirects are never followed
        except (ssl.SSLError, ssl.CertificateError) as e:
            box["error"] = "tls:" + type(e).__name__
        except Exception as e:
            box["error"] = type(e).__name__
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(wall_timeout)
    lat = round(time.monotonic() - t0, 3)
    if th.is_alive():
        return {"http": {"result": "FAIL", "error": "wall_clock_timeout"}, "body": None, "latency_s": lat}
    if "error" in box:
        return {"http": {"result": "FAIL", "error": box["error"]}, "body": None, "latency_s": lat}
    st = box.get("status")
    if st != 200:
        return {"http": {"result": "FAIL", "status": st, "error": "redirect_refused" if st and 300 <= st < 400 else "http_status"}, "body": None, "latency_s": lat}
    raw = box.get("raw", b"")
    if len(raw) > max_bytes:
        return {"http": {"result": "FAIL", "status": st, "error": "body_too_large"}, "body": None, "latency_s": lat}
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return {"http": {"result": "PASS", "status": st}, "body": body, "latency_s": lat}


def check_schema(body, min_contract):
    if not isinstance(body, dict):
        return {"result": "FAIL", "why": "body is not a JSON object"}
    bad = [k for k, t in HEALTH_SCHEMA.items() if not isinstance(body.get(k), t) or (t is int and isinstance(body.get(k), bool))]
    if bad:
        return {"result": "FAIL", "why": f"missing or mistyped fields {bad}"}
    if body["ok"] is not True:
        return {"result": "FAIL", "why": "ok is not true"}
    if body["api_contract"] < min_contract:
        return {"result": "FAIL", "why": f"api_contract {body['api_contract']} < required {min_contract}"}
    return {"result": "PASS", "why": f"api_contract {body['api_contract']}, version {body['version']}"}


def check_deployment(body, expect):
    """expect = {"deployment", "store", "auth", "data_plane"}: every value must match exactly (the data-plane fingerprint is fixed at notebook
    generation time from the deployment's configured store, so a staging endpoint can never pass as production and vice versa)."""
    if not isinstance(body, dict):
        return {"result": "FAIL", "why": "no body"}
    diff = {k: [body.get(k), v] for k, v in expect.items() if body.get(k) != v}
    return {"result": "PASS" if not diff else "FAIL", "why": "all expected" if not diff else f"got/expected {diff}"}
