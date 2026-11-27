# C1. Application credentials (Kaggle Secrets) and the diagnostic transport to the OCI deployment. Shared by notebooks 01, 02 and 03 (generated from
# scripts/kaggle_cells/diag_credentials.py; CPU tests exercise this exact text). Standard library only: no cloud SDK, no cloud-provider credential.
#
# Two DIFFERENT application credentials, never interchangeable:
#   ENIGMA_DIAG_KEY       edx_<8>_<43>      56 chars  authenticates ONLY POST /api/v1/diag/events (notebooks 01, 02 oci-assisted, 03)
#   ENIGMA_RUNTIME_TOKEN  enig_rt_<8>_<43>  60 chars  authenticates ONLY Enigma runtime registration / heartbeats / telemetry / jobs (notebook 03)
# Neither is a cloud-provider key. Values are never printed, stored in os.environ, written to evidence, or sent anywhere but their own endpoint; only a
# category, a code and lengths are ever recorded. Only a VALIDATED value is cached; a failed lookup never poisons the cache. After fixing a secret in
# Kaggle's sidebar (Add-ons -> Secrets), call refresh_credentials() (or just re-run the preflight cell): the attempt id, the event sequence, completed
# verdicts and evidence files are all kept. Note: reading a Kaggle Secret is itself a request to Kaggle's own backend (kaggle_secrets talks to the
# Kaggle web service from inside the VM), so no secret is read during an offline observation window.
import re, json, time, socket, ssl, urllib.request, urllib.error, urllib.parse

DIAG_URL = "__DIAG__"
CRED_SPECS = {
    "ENIGMA_DIAG_KEY": {"category": "diagnostic_key", "pattern": r"edx_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", "length": 56, "prefix": "edx_"},
    "ENIGMA_RUNTIME_TOKEN": {"category": "enigma_runtime_token", "pattern": r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", "length": 60, "prefix": "enig_rt_"},
}
CRED_GUIDANCE = {
    "SECRET_OK": "credential retrieved and validated (value not shown)",
    "SECRET_MISSING": "no Kaggle Secret with this exact name is available: add it in Add-ons -> Secrets, tick it for this notebook, then re-run this cell",
    "SECRET_ACCESS_DENIED": "the secret exists but this notebook may not read it: in Add-ons -> Secrets tick the checkbox next to it, then re-run this cell",
    "SECRET_EMPTY": "the Kaggle Secret is empty: paste the whole value again, then re-run this cell",
    "SECRET_FORMAT_INVALID": "the value does not have the expected format/length: paste exactly the one-line value (no quotes, no NAME=, no spaces), then re-run this cell",
    "SECRET_RETRIEVAL_FAILED": "Kaggle's secret service did not answer: check Internet is ON, then re-run this cell",
}
_WRONG_TYPE = {
    "ENIGMA_DIAG_KEY": ("Wrong credential type. ENIGMA_DIAG_KEY requires the diagnostic key from the local ENIGMA_DIAG_KEY.txt file, not the Enigma "
                        "runtime token."),
    "ENIGMA_RUNTIME_TOKEN": ("Wrong credential type. ENIGMA_RUNTIME_TOKEN requires the Enigma runtime key (enig_rt_...) issued in the Enigma web app "
                             "(Runtimes), not the diagnostic key."),
}
_AWS_ID = re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}")
_CRED_CACHE = {}                                       # name -> validated value ONLY
_DIAG = {"seq": 0, "last": None}                      # the event sequence survives credential refreshes


def _secret_client():
    from kaggle_secrets import UserSecretsClient
    return UserSecretsClient()


def classify_secret(name, value):
    """(code, facts) for a raw secret value. facts never contain the value, a fragment of it, or its key id."""
    spec = CRED_SPECS[name]
    facts = {"credential": name, "expected_category": spec["category"], "expected_length": spec["length"]}
    if value is None:
        return "SECRET_MISSING", facts
    if not isinstance(value, str):
        return "SECRET_FORMAT_INVALID", facts
    v = value.strip()                                 # a trailing newline from copy/paste is tolerated; anything inside is not
    facts.update(actual_length=len(v), embedded_whitespace=any(c.isspace() for c in v), quoted=v[:1] in "'\"" and len(v) > 0,
                 looks_like_assignment=bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", v)))
    if not v:
        return "SECRET_EMPTY", facts
    if re.fullmatch(spec["pattern"], v):
        facts["supplied_category"] = spec["category"]
        return "SECRET_OK", facts
    for other, ospec in CRED_SPECS.items():
        if other != name and (v.startswith(ospec["prefix"]) or re.search(ospec["pattern"], v)):
            facts["supplied_category"] = ospec["category"]
            return "SECRET_WRONG_TYPE", facts
    if _AWS_ID.search(v):
        facts["supplied_category"] = "aws_access_key"
        return "SECRET_WRONG_TYPE", facts
    facts["supplied_category"] = "unknown"
    return "SECRET_FORMAT_INVALID", facts


def _retrieval_code(exc):
    """Classify a kaggle_secrets exception from its class and message WITHOUT recording the message."""
    if isinstance(exc, ImportError):
        return "SECRET_RETRIEVAL_FAILED"
    text = (type(exc).__name__ + " " + str(exc)).lower()
    if "no user secrets exist" in text or "not found" in text or "does not exist" in text:
        return "SECRET_MISSING"
    if any(w in text for w in ("permission", "denied", "unauthori", "forbidden", "not attached", "not authorized", "403", "401")):
        return "SECRET_ACCESS_DENIED"
    return "SECRET_RETRIEVAL_FAILED"


def credential_message(code, name):
    return _WRONG_TYPE[name] if code == "SECRET_WRONG_TYPE" else CRED_GUIDANCE.get(code, code)


def get_credential(name, refresh=False):
    """(value or None, result). Caches only a validated value; any failure clears the cache entry so a later call retries."""
    if refresh:
        _CRED_CACHE.pop(name, None)
    if name in _CRED_CACHE:
        return _CRED_CACHE[name], {"credential": name, "code": "SECRET_OK", "cached": True, "message": CRED_GUIDANCE["SECRET_OK"]}
    try:
        raw = _secret_client().get_secret(name)
    except Exception as exc:                          # noqa: BLE001 - classified; the exception text is never recorded
        _CRED_CACHE.pop(name, None)
        code = _retrieval_code(exc)
        return None, {"credential": name, "code": code, "error_type": type(exc).__name__, "message": credential_message(code, name)}
    code, facts = classify_secret(name, raw)
    if code != "SECRET_OK":
        _CRED_CACHE.pop(name, None)
        return None, {**facts, "code": code, "message": credential_message(code, name)}
    _CRED_CACHE[name] = raw.strip()
    return _CRED_CACHE[name], {**facts, "code": code, "cached": False, "message": CRED_GUIDANCE["SECRET_OK"]}


def refresh_credentials(*names):
    """Forget cached credentials (all, or the named ones) so the next use re-reads Kaggle Secrets. Nothing else is reset."""
    for n in names or list(_CRED_CACHE):
        _CRED_CACHE.pop(n, None)
    return sorted(_CRED_CACHE)


def check_credential(name, step):
    """Preflight verdict for one credential: PASS only for a correctly typed, correctly formatted value."""
    _, res = get_credential(name, refresh=True)
    ev = {k: v for k, v in res.items() if k != "message"}
    if res["code"] == "SECRET_OK":
        verdict(step, "PASS", f"{name}: {res['message']}", **ev)
        return res
    print(f"CREDENTIAL {name}: {res['code']} -> {res['message']}", flush=True)
    verdict(step, "INCONCLUSIVE", f"{name}: {res['code']}", **ev)


# ------------------------------------------------------------------------------------------------------------- transport to the OCI diagnostic route
DIAG_GUIDANCE = {
    "DIAGNOSTIC_PERSISTENCE_VERIFIED": "the OCI deployment acknowledged a durable write (stored or already stored)",
    "DIAGNOSTIC_AUTH_REJECTED": "the diagnostic route rejected ENIGMA_DIAG_KEY: re-check the Kaggle Secret against the key issued by scripts/diag_keys.py; do not retry blindly",
    "DIAGNOSTIC_KEY_EXPIRED_OR_REVOKED": "ENIGMA_DIAG_KEY is correct but expired or revoked: ask for a newly issued diagnostic key",
    "DIAGNOSTIC_QUOTA_EXCEEDED": "this diagnostic key used its whole event quota: ask for a newly issued diagnostic key",
    "DIAGNOSTIC_NETWORK_TIMEOUT": "the diagnostic endpoint did not answer in time",
    "DIAGNOSTIC_DNS_ERROR": "the diagnostic endpoint's host name did not resolve (Internet OFF?)",
    "DIAGNOSTIC_TLS_ERROR": "TLS / certificate verification failed (verification is never disabled)",
    "DIAGNOSTIC_CONNECTION_ERROR": "the connection was refused or reset",
    "DIAGNOSTIC_HTTP_ERROR": "the diagnostic endpoint answered with an HTTP error",
    "DIAGNOSTIC_INVALID_RESPONSE": "the diagnostic endpoint answered with a malformed acknowledgement",
    "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED": "the answer did not acknowledge every event as durably stored",
    "DIAGNOSTIC_CREDENTIAL_UNAVAILABLE": "no valid ENIGMA_DIAG_KEY: nothing was sent",
    "REMOTE_DISABLED": "remote diagnostics are disabled in this mode: nothing was sent",
}
_SERVER_ERRORS = {"unauthorized", "key_expired_or_revoked", "quota_exhausted", "too_large", "bad_json", "bad_events", "event_not_object",
                  "unknown_field", "missing_field", "bad_field", "bad_identity", "method_not_allowed", "unavailable"}
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}
_TRANSIENT_CODES = {"DIAGNOSTIC_NETWORK_TIMEOUT", "DIAGNOSTIC_CONNECTION_ERROR"}
REMOTE_ENABLED = True                                  # notebook 02 standalone mode turns this off: then no secret is read and nothing is sent
_REMOTE_FIELDS = {"stage", "phase", "http", "latency_s", "target", "exc", "exit_code", "signal", "last_ok", "verdict", "note", "gpu_count",
                  "gpu_mem_used_mib", "gpu_util_pct", "wheel_build", "wheel_sha256", "artifact", "offset_s", "child_pid", "child_exit", "outcome"}


class DiagResult(dict):
    """Truthy only when the OCI deployment durably acknowledged every event."""
    def __bool__(self):
        return self.get("code") == "DIAGNOSTIC_PERSISTENCE_VERIFIED"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the Authorization header must never be replayed to another URL or host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener():
    return urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))


_OPENER = _build_opener()


def _diag_url_ok(url):
    u = urllib.parse.urlsplit(url)
    d = urllib.parse.urlsplit(DIAG_URL)
    # Exactly the generator-configured OCI host, https, the one diagnostic path; never another host (the Authorization header goes nowhere else).
    return bool(DIAG_URL) and u.scheme == "https" and bool(u.hostname) and u.hostname == d.hostname and u.port == d.port \
        and u.path == "/api/v1/diag/events" and not (u.username or u.password or u.query or u.fragment)


def _net_code(exc):
    r = exc.reason if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError) else exc
    if isinstance(r, socket.gaierror):
        return "DIAGNOSTIC_DNS_ERROR", "dns"
    if isinstance(r, ssl.SSLError) or isinstance(r, ssl.CertificateError):
        return "DIAGNOSTIC_TLS_ERROR", "tls"
    if isinstance(r, (TimeoutError, socket.timeout)):
        return "DIAGNOSTIC_NETWORK_TIMEOUT", "connect" if r is not exc else "read"
    if isinstance(r, (ConnectionError, OSError)):
        return "DIAGNOSTIC_CONNECTION_ERROR", "connect"
    return "DIAGNOSTIC_CONNECTION_ERROR", "unknown"


def post_events(events, key, url=None, timeout=15.0, max_attempts=3, backoff=1.0):   # bounded; the server answers only after the insert committed
    """POST {"events": [...]} once per attempt. Retries ONLY transient failures, with the SAME body (same attempt id and seq): the server's
    conditional write makes a resubmission a duplicate, never a second record. Never retries an authentication or validation answer."""
    url = url or DIAG_URL
    body = json.dumps({"events": events}).encode()
    res = DiagResult(code="DIAGNOSTIC_HTTP_ERROR", attempts=0)
    for i in range(max_attempts):
        res = DiagResult(_post_once(url, body, key, timeout, len(events)), attempts=i + 1)
        if not res.get("transient") or i + 1 == max_attempts:
            return res
        time.sleep(backoff * (2 ** i))
    return res


def _post_once(url, body, key, timeout, n):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                                                          "User-Agent": "Enigma-Kaggle-Diagnostic/5.0"})
    t = time.monotonic()
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(4097)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(513)
        except Exception:                             # noqa: BLE001
            raw = b""
        finally:
            e.close()
        err = None
        try:
            j = json.loads(raw[:512])
            err = j.get("error") if isinstance(j, dict) and j.get("error") in _SERVER_ERRORS else None
        except ValueError:
            pass
        out = {"http": e.code, "server_error": err, "latency_s": round(time.monotonic() - t, 3)}
        if 300 <= e.code < 400:
            return {**out, "code": "DIAGNOSTIC_HTTP_ERROR", "detail": "redirect_refused"}
        if e.code == 401:
            return {**out, "code": "DIAGNOSTIC_KEY_EXPIRED_OR_REVOKED" if err == "key_expired_or_revoked" else "DIAGNOSTIC_AUTH_REJECTED"}
        if e.code == 429 and err == "quota_exhausted":
            return {**out, "code": "DIAGNOSTIC_QUOTA_EXCEEDED"}
        return {**out, "code": "DIAGNOSTIC_HTTP_ERROR", "transient": e.code in _TRANSIENT_HTTP}
    except (urllib.error.URLError, OSError) as e:      # DNS, TLS, refused, connect timeout (URLError) / read timeout, reset (OSError)
        code, phase = _net_code(e)
        return {"code": code, "phase": phase, "error_type": type(e.reason if isinstance(e, urllib.error.URLError) else e).__name__,
                "transient": code in _TRANSIENT_CODES, "latency_s": round(time.monotonic() - t, 3)}
    out = {"http": status, "latency_s": round(time.monotonic() - t, 3)}
    if status != 200:
        return {**out, "code": "DIAGNOSTIC_HTTP_ERROR"}
    if len(raw) > 4096:
        return {**out, "code": "DIAGNOSTIC_INVALID_RESPONSE", "detail": "oversized"}
    try:
        ack = json.loads(raw)
    except ValueError:
        return {**out, "code": "DIAGNOSTIC_INVALID_RESPONSE", "detail": "not_json"}
    ints = isinstance(ack, dict) and all(isinstance(ack.get(k), int) and not isinstance(ack.get(k), bool) and ack[k] >= 0 for k in ("stored", "duplicates"))
    if not ints:
        return {**out, "code": "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED", "detail": "missing_or_invalid_fields"}
    out.update(stored=ack["stored"], duplicates=ack["duplicates"])
    if ack["stored"] + ack["duplicates"] != n:
        return {**out, "code": "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED", "detail": "count_mismatch"}
    return {**out, "code": "DIAGNOSTIC_PERSISTENCE_VERIFIED"}


def _event(name, rec, fields, seq):
    ev = {"attempt": ATTEMPT, "nb": NOTEBOOK, "seq": seq, "marker": name, "utc": rec["utc"], "t": rec["t"], "experiment": EXPERIMENT,
          "pid": rec["pid"], "pgid": rec["pgid"], "sid": rec["sid"], "mem_available_mib": rec.get("memavailable_mib"),
          "cgroup_current_mib": rec.get("cgroup_current_mib"), "cgroup_max_mib": rec.get("cgroup_max_mib"),
          "oom_kill": rec.get("cgroup_events", {}).get("oom_kill"), "vm_uptime_s": rec.get("vm_uptime_s"),
          "oom_state": "available" if "oom_kill" in rec.get("cgroup_events", {}) else "unavailable", "transport": globals().get("RUNTIME_TRANSPORT")}
    ev.update({k: v for k, v in fields.items() if k in _REMOTE_FIELDS and v is not None})
    return {k: v for k, v in ev.items() if v is not None}


def rmark(name, **fields):
    """mark() locally, then (if remote diagnostics are enabled) one bounded event to the OCI diagnostic route. Returns a DiagResult: truthy only after it
    acknowledged the durable write. A 401 forgets the cached key (fix the secret, then re-run). Nothing secret is ever recorded."""
    rec = mark(name, **fields)
    if not REMOTE_ENABLED:
        return DiagResult(code="REMOTE_DISABLED", marker=name)
    key, cred = get_credential("ENIGMA_DIAG_KEY")
    if key is None:
        mark("remote_skipped", marker_name=name, credential_code=cred["code"])
        print(f"REMOTE NOT-SENT {name}: {cred['code']} -> {cred['message']}", flush=True)
        return DiagResult(code="DIAGNOSTIC_CREDENTIAL_UNAVAILABLE", credential_code=cred["code"], marker=name)
    _DIAG["seq"] += 1
    ev = _event(name, rec, fields, _DIAG["seq"])
    if not _diag_url_ok(DIAG_URL):
        return DiagResult(code="DIAGNOSTIC_HTTP_ERROR", detail="diag_url_not_allowed", marker=name, seq=ev["seq"])
    res = post_events([ev], key)
    res.update(marker=name, seq=ev["seq"])
    if res["code"] in ("DIAGNOSTIC_AUTH_REJECTED", "DIAGNOSTIC_KEY_EXPIRED_OR_REVOKED"):
        _CRED_CACHE.pop("ENIGMA_DIAG_KEY", None)
    _DIAG["last"] = {"event": ev, "result": dict(res)}
    mark("remote_result", **{f"remote_{k}": v for k, v in res.items()})
    print(f"REMOTE {'ACK' if res else 'NOT-ACKNOWLEDGED'} {name} seq={ev['seq']} {res['code']}"
          + ("" if res else f" -> {DIAG_GUIDANCE.get(res['code'], '')}"), flush=True)
    return res


def resubmit_last():
    """Resend the last event UNCHANGED (same attempt id, same seq). A durable record answers duplicates == 1."""
    last = _DIAG.get("last")
    key, _ = get_credential("ENIGMA_DIAG_KEY")
    if not last or key is None:
        return DiagResult(code="DIAGNOSTIC_CREDENTIAL_UNAVAILABLE" if last else "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED")
    res = post_events([last["event"]], key)
    res.update(marker=last["event"]["marker"], seq=last["event"]["seq"])
    mark("remote_resubmit_result", **{f"remote_{k}": v for k, v in res.items()})
    return res


def rcheck(step, name, **fields):
    """A marker that MUST be durable before the next (risky or expensive) operation. Local-only when remote diagnostics are disabled."""
    res = rmark(name, **fields)
    if not REMOTE_ENABLED:
        return res
    if not res:
        verdict(step, "INCONCLUSIVE", f"{name}: {res['code']} -> {DIAG_GUIDANCE.get(res['code'], '')}", remote_code=res["code"])
    return res
