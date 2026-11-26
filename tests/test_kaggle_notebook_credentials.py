"""CPU-only tests of the EXACT credential / transport / redaction code inlined into the three Kaggle notebooks (scripts/kaggle_cells/*.py).
Synthetic credentials only; no network except a loopback HTTP server for the redirect test; no GPU."""
from __future__ import annotations

import http.server
import json
import socket
import ssl
import threading
import urllib.error
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CRED_SRC = (ROOT / "scripts" / "kaggle_cells" / "diag_credentials.py").read_text()
EVID_SRC = (ROOT / "scripts" / "kaggle_cells" / "evidence.py").read_text()

# Synthetic fixtures, deliberately fake (never real credentials).
DIAG = "edx_abcd1234_" + "A" * 43
RT = "enig_rt_abcd1234_" + "B" * 43
AWS_ID = "AKIA" + "Z" * 16
assert len(DIAG) == 56 and len(RT) == 60


class Stop(Exception):
    pass


class FakeSecrets:
    def __init__(self, values=None, exc=None):
        self.values, self.exc, self.calls = dict(values or {}), exc, 0

    def get_secret(self, name):
        self.calls += 1
        if self.exc:
            raise self.exc
        if name not in self.values:
            raise RuntimeError(f"Unexpected response from the service. No user secrets exist for kernel id 1 and label {name}")
        return self.values[name]


class Resp:
    def __init__(self, status=200, body=b'{"stored": 1, "duplicates": 0}'):
        self.status, self._b = status, body

    def read(self, n=-1):
        return self._b if n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b"{}"):
    import io
    return urllib.error.HTTPError("https://x.lambda-url.us-east-1.on.aws/", code, "err", {}, io.BytesIO(body))


class FakeOpener:
    def __init__(self, *outcomes):
        self.outcomes, self.requests = list(outcomes), []

    def open(self, req, timeout=None):
        assert timeout and timeout > 0
        self.requests.append(req)
        o = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(o, BaseException):
            raise o
        return o


@pytest.fixture
def nb(capsys):
    """A namespace equivalent to a notebook kernel after the prelude + shared credential cell."""
    marks, verdicts = [], {}
    ns: dict = {}
    exec(compile(EVID_SRC, "evidence.py", "exec"), ns)

    def mark(name, **f):
        rec = {"marker": name, "utc": "2026-11-27T00:00:00Z", "t": 1.0, "pid": 1, "pgid": 1, "sid": 1, **f}
        marks.append(ns["redact"](json.dumps(rec, default=str)))
        return rec

    def verdict(step, status, reason, **ev):
        verdicts[step] = status
        marks.append(ns["redact"](json.dumps({"verdict": step, "status": status, "reason": reason, **ev}, default=str)))
        if status != "PASS":
            raise Stop(f"{step} {status}: {reason}")

    ns.update(mark=mark, verdict=verdict, ATTEMPT="20261127T000000Z-test0001", NOTEBOOK="nb", EXPERIMENT="no-sdk")
    exec(compile(CRED_SRC.replace("__DIAG__", "https://abc.lambda-url.us-east-1.on.aws/"), "diag_credentials.py", "exec"), ns)
    ns["_marks"], ns["_verdicts"] = marks, verdicts
    ns["_secrets"] = FakeSecrets({"ENIGMA_DIAG_KEY": DIAG, "ENIGMA_RUNTIME_TOKEN": RT})
    ns["_secret_client"] = lambda: ns["_secrets"]
    yield ns
    out = capsys.readouterr()
    everything = out.out + out.err + "\n".join(marks)
    for secret in (DIAG, RT, AWS_ID, "abcd1234_" + "A" * 10):
        assert secret not in everything, "a credential (or fragment / key id) leaked into output or evidence"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)


# ------------------------------------------------------------------------------------------------------------------------------------ classification
@pytest.mark.parametrize("name,value,code", [
    ("ENIGMA_DIAG_KEY", DIAG, "SECRET_OK"),
    ("ENIGMA_DIAG_KEY", DIAG + "\n", "SECRET_OK"),                                   # trailing newline from copy/paste tolerated
    ("ENIGMA_RUNTIME_TOKEN", RT, "SECRET_OK"),
    ("ENIGMA_DIAG_KEY", RT, "SECRET_WRONG_TYPE"),                                    # the 2026-11-26 incident
    ("ENIGMA_RUNTIME_TOKEN", DIAG, "SECRET_WRONG_TYPE"),
    ("ENIGMA_DIAG_KEY", AWS_ID, "SECRET_WRONG_TYPE"),
    ("ENIGMA_DIAG_KEY", None, "SECRET_MISSING"),
    ("ENIGMA_DIAG_KEY", "", "SECRET_EMPTY"),
    ("ENIGMA_DIAG_KEY", "   \n", "SECRET_EMPTY"),
    ("ENIGMA_DIAG_KEY", "edy_abcd1234_" + "A" * 43, "SECRET_FORMAT_INVALID"),       # invalid prefix
    ("ENIGMA_DIAG_KEY", DIAG[:-1], "SECRET_FORMAT_INVALID"),                         # invalid length
    ("ENIGMA_DIAG_KEY", DIAG[:-1] + "!", "SECRET_FORMAT_INVALID"),                   # invalid character
    ("ENIGMA_DIAG_KEY", "edx_ABCD1234_" + "A" * 43, "SECRET_FORMAT_INVALID"),       # upper-case key id
    ("ENIGMA_DIAG_KEY", DIAG[:20] + " " + DIAG[21:], "SECRET_FORMAT_INVALID"),       # embedded whitespace
    ("ENIGMA_DIAG_KEY", f'"{DIAG}"', "SECRET_FORMAT_INVALID"),                       # quoted
    ("ENIGMA_DIAG_KEY", f"ENIGMA_DIAG_KEY={DIAG}", "SECRET_FORMAT_INVALID"),        # copied assignment
])
def test_classification(nb, name, value, code):
    got, facts = nb["classify_secret"](name, value)
    assert got == code
    assert DIAG not in json.dumps(facts) and RT not in json.dumps(facts) and "abcd1234" not in json.dumps(facts)


def test_wrong_type_message_is_explicit_and_names_only_the_category(nb):
    nb["_secrets"].values["ENIGMA_DIAG_KEY"] = RT
    with pytest.raises(Stop):
        nb["check_credential"]("ENIGMA_DIAG_KEY", "diag_key_preflight")
    text = "\n".join(nb["_marks"])
    assert "SECRET_WRONG_TYPE" in text and '"supplied_category": "enigma_runtime_token"' in text and '"actual_length": 60' in text
    assert nb["credential_message"]("SECRET_WRONG_TYPE", "ENIGMA_DIAG_KEY") == (
        "Wrong credential type. ENIGMA_DIAG_KEY requires the diagnostic key from the local ENIGMA_DIAG_KEY.txt file, not the Enigma runtime token.")


@pytest.mark.parametrize("exc,code", [
    (RuntimeError("No user secrets exist for kernel id 9 and label ENIGMA_DIAG_KEY"), "SECRET_MISSING"),
    (PermissionError("403 Forbidden"), "SECRET_ACCESS_DENIED"),
    (RuntimeError("secret not attached to this notebook"), "SECRET_ACCESS_DENIED"),
    (ConnectionError("boom"), "SECRET_RETRIEVAL_FAILED"),
    (ImportError("no kaggle_secrets"), "SECRET_RETRIEVAL_FAILED"),
])
def test_retrieval_exceptions_are_classified_without_recording_the_message(nb, exc, code):
    nb["_secrets"].exc = exc
    v, res = nb["get_credential"]("ENIGMA_DIAG_KEY")
    assert v is None and res["code"] == code and res["error_type"] == type(exc).__name__
    assert str(exc) not in json.dumps(res)


# -------------------------------------------------------------------------------------------------------------------------------- cache / refresh
def test_failed_lookup_never_poisons_the_cache_and_refresh_recovers(nb):
    nb["_secrets"].values["ENIGMA_DIAG_KEY"] = RT                                      # wrong secret first
    assert nb["get_credential"]("ENIGMA_DIAG_KEY")[0] is None
    assert "ENIGMA_DIAG_KEY" not in nb["_CRED_CACHE"]
    nb["_secrets"].values["ENIGMA_DIAG_KEY"] = DIAG                                    # user fixes the sidebar secret
    v, res = nb["get_credential"]("ENIGMA_DIAG_KEY")                                   # no kernel restart, no explicit refresh needed
    assert v == DIAG and res["code"] == "SECRET_OK"
    calls = nb["_secrets"].calls
    assert nb["get_credential"]("ENIGMA_DIAG_KEY")[1]["cached"] is True and nb["_secrets"].calls == calls   # validated value is cached
    nb["refresh_credentials"]("ENIGMA_DIAG_KEY")
    nb["get_credential"]("ENIGMA_DIAG_KEY")
    assert nb["_secrets"].calls == calls + 1


def test_refresh_keeps_attempt_sequence_and_verdicts(nb):
    nb["_OPENER"] = FakeOpener(Resp())
    nb["rmark"]("a"); nb["rmark"]("b")
    nb["_verdicts"]["idle_10min"] = "PASS"
    nb["refresh_credentials"]()
    assert nb["_DIAG"]["seq"] == 2 and nb["ATTEMPT"] == "20261127T000000Z-test0001" and nb["_verdicts"]["idle_10min"] == "PASS"
    nb["rmark"]("c")
    assert json.loads(nb["_OPENER"].requests[-1].data)["events"][0]["seq"] == 3


def test_auth_rejection_invalidates_the_cached_key_without_retrying(nb):
    nb["_OPENER"] = FakeOpener(http_error(401, b'{"error": "unauthorized"}'))
    res = nb["rmark"]("x")
    assert not res and res["code"] == "DIAGNOSTIC_AUTH_REJECTED" and res["attempts"] == 1
    assert "ENIGMA_DIAG_KEY" not in nb["_CRED_CACHE"]


def test_preflight_passes_for_the_right_secret_and_is_rerunnable(nb):
    nb["_secrets"].values["ENIGMA_DIAG_KEY"] = ""
    with pytest.raises(Stop):
        nb["check_credential"]("ENIGMA_DIAG_KEY", "diag_key_preflight")
    assert nb["_verdicts"]["diag_key_preflight"] == "INCONCLUSIVE"
    nb["_secrets"].values["ENIGMA_DIAG_KEY"] = DIAG
    nb["check_credential"]("ENIGMA_DIAG_KEY", "diag_key_preflight")
    assert nb["_verdicts"]["diag_key_preflight"] == "PASS"


def test_runtime_token_and_diag_key_failures_are_reported_separately(nb):
    nb["_secrets"].values["ENIGMA_RUNTIME_TOKEN"] = DIAG
    nb["check_credential"]("ENIGMA_DIAG_KEY", "diag_key_preflight")
    with pytest.raises(Stop, match="runtime_token_preflight"):
        nb["check_credential"]("ENIGMA_RUNTIME_TOKEN", "runtime_token_preflight")
    assert nb["_verdicts"] == {"diag_key_preflight": "PASS", "runtime_token_preflight": "INCONCLUSIVE"}


# -------------------------------------------------------------------------------------------------------------------------------------- transport
def test_valid_ack_and_request_shape(nb):
    nb["_OPENER"] = FakeOpener(Resp())
    res = nb["rmark"]("aws_first_event", stage="aws", note="n", not_whitelisted="dropped")
    assert res and res["code"] == "DIAGNOSTIC_PERSISTENCE_VERIFIED" and res["stored"] == 1
    req = nb["_OPENER"].requests[0]
    assert req.get_method() == "POST" and req.get_header("Content-type") == "application/json" and req.get_header("Authorization") == f"Bearer {DIAG}"
    ev = json.loads(req.data)["events"][0]
    assert {"attempt", "nb", "seq", "marker", "utc"} <= set(ev) and "not_whitelisted" not in ev


def test_duplicate_ack_counts_as_durable_and_resubmission_reuses_seq(nb):
    nb["_OPENER"] = FakeOpener(Resp(), Resp(body=b'{"stored": 0, "duplicates": 1}'))
    nb["rmark"]("e")
    dup = nb["resubmit_last"]()
    assert dup and dup["duplicates"] == 1
    a, b = (json.loads(r.data)["events"][0] for r in nb["_OPENER"].requests)
    assert a == b


@pytest.mark.parametrize("body,code", [
    (b"not json", "DIAGNOSTIC_INVALID_RESPONSE"),
    (b"{}", "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED"),
    (b'{"stored": "1", "duplicates": 0}', "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED"),
    (b'{"stored": true, "duplicates": 0}', "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED"),
    (b'{"stored": 0, "duplicates": 0}', "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED"),
    (b"[1]", "DIAGNOSTIC_PERSISTENCE_NOT_ACKNOWLEDGED"),
    (b"{" + b" " * 5000 + b"}", "DIAGNOSTIC_INVALID_RESPONSE"),
])
def test_200_without_a_valid_acknowledgement_is_not_success(nb, body, code):
    nb["_OPENER"] = FakeOpener(Resp(body=body))
    res = nb["rmark"]("x")
    assert not res and res["code"] == code


@pytest.mark.parametrize("status,body,code,attempts", [
    (401, b'{"error": "unauthorized"}', "DIAGNOSTIC_AUTH_REJECTED", 1),
    (401, b'{"error": "key_expired_or_revoked"}', "DIAGNOSTIC_KEY_EXPIRED_OR_REVOKED", 1),
    (403, b'{"Message": "Forbidden"}', "DIAGNOSTIC_HTTP_ERROR", 1),
    (429, b'{"error": "quota_exhausted"}', "DIAGNOSTIC_QUOTA_EXCEEDED", 1),
    (429, b'{"Reason": "ReservedFunctionConcurrentInvocationLimitExceeded"}', "DIAGNOSTIC_HTTP_ERROR", 3),
    (400, b'{"error": "bad_field"}', "DIAGNOSTIC_HTTP_ERROR", 1),
    (500, b"", "DIAGNOSTIC_HTTP_ERROR", 3),
    (502, b"", "DIAGNOSTIC_HTTP_ERROR", 3),
    (503, b'{"error": "unavailable"}', "DIAGNOSTIC_HTTP_ERROR", 3),
    (504, b"", "DIAGNOSTIC_HTTP_ERROR", 3),
])
def test_http_errors_are_classified_and_only_transient_ones_retried(nb, status, body, code, attempts):
    nb["_OPENER"] = FakeOpener(http_error(status, body))
    res = nb["rmark"]("x")
    assert not res and res["code"] == code and res["http"] == status and res["attempts"] == attempts
    seqs = {json.loads(r.data)["events"][0]["seq"] for r in nb["_OPENER"].requests}
    assert seqs == {1}                                                                  # retries reuse the same event (same seq)


@pytest.mark.parametrize("exc,code,phase", [
    (urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), "DIAGNOSTIC_DNS_ERROR", "dns"),
    (urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed")), "DIAGNOSTIC_TLS_ERROR", "tls"),
    (urllib.error.URLError(TimeoutError("timed out")), "DIAGNOSTIC_NETWORK_TIMEOUT", "connect"),
    (TimeoutError("The read operation timed out"), "DIAGNOSTIC_NETWORK_TIMEOUT", "read"),
    (urllib.error.URLError(ConnectionRefusedError(111, "refused")), "DIAGNOSTIC_CONNECTION_ERROR", "connect"),
])
def test_network_failures_are_classified(nb, exc, code, phase):
    nb["_OPENER"] = FakeOpener(exc)
    res = nb["rmark"]("x")
    assert not res and res["code"] == code and res["phase"] == phase
    assert res["attempts"] == (3 if code in ("DIAGNOSTIC_NETWORK_TIMEOUT", "DIAGNOSTIC_CONNECTION_ERROR") else 1)
    assert "timed out" not in json.dumps(dict(res)) and "refused" not in json.dumps(dict(res))


def test_uncertain_read_timeout_then_retry_is_acknowledged_as_duplicate(nb):
    nb["_OPENER"] = FakeOpener(TimeoutError(), Resp(body=b'{"stored": 0, "duplicates": 1}'))
    res = nb["rmark"]("x")
    assert res and res["attempts"] == 2 and res["duplicates"] == 1
    a, b = (json.loads(r.data) for r in nb["_OPENER"].requests)
    assert a == b


def test_missing_secret_sends_nothing_and_is_not_an_ack(nb):
    nb["_secrets"].values.pop("ENIGMA_DIAG_KEY")
    nb["_OPENER"] = FakeOpener(Resp())
    res = nb["rmark"]("x")
    assert not res and res["code"] == "DIAGNOSTIC_CREDENTIAL_UNAVAILABLE" and res["credential_code"] == "SECRET_MISSING"
    assert nb["_OPENER"].requests == [] and nb["_DIAG"]["seq"] == 0


def test_remote_disabled_mode_reads_no_secret_and_sends_nothing(nb):
    nb["REMOTE_ENABLED"] = False
    nb["_OPENER"] = FakeOpener(Resp())
    assert nb["rmark"]("x")["code"] == "REMOTE_DISABLED" and nb["rcheck"]("s", "x")["code"] == "REMOTE_DISABLED"
    assert nb["_secrets"].calls == 0 and nb["_OPENER"].requests == []


def test_rcheck_stops_on_a_failed_required_marker(nb):
    nb["_OPENER"] = FakeOpener(http_error(503))
    with pytest.raises(Stop, match="railway_marker"):
        nb["rcheck"]("railway_marker", "before_railway")


def test_only_the_configured_https_lambda_host_is_allowed(nb):
    ok = nb["_diag_url_ok"]
    assert ok("https://abc.lambda-url.us-east-1.on.aws/")
    for bad in ("http://abc.lambda-url.us-east-1.on.aws/", "https://evil.example/", "https://abc.lambda-url.us-east-1.on.aws.evil.example/",
                "https://u:p@abc.lambda-url.us-east-1.on.aws/"):
        assert not ok(bad), bad


def test_redirects_are_never_followed_so_the_authorization_header_never_leaves(nb, monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    seen = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.headers.get("Authorization")); self.send_response(200); self.end_headers(); self.wfile.write(b'{"stored":1,"duplicates":0}')

        def log_message(self, *a):
            pass

    tgt = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(307); self.send_header("Location", f"http://127.0.0.1:{tgt.server_port}/steal"); self.end_headers()

        def log_message(self, *a):
            pass

    red = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    for s in (tgt, red):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        nb["_OPENER"] = nb["_build_opener"]()
        res = nb["post_events"]([{"x": 1}], DIAG, url=f"http://127.0.0.1:{red.server_port}/", timeout=5)
    finally:
        tgt.shutdown(); red.shutdown()
    assert not res and res["code"] == "DIAGNOSTIC_HTTP_ERROR" and res["detail"] == "redirect_refused" and res["http"] == 307
    assert seen == []


def test_opener_keeps_certificate_verification(nb):
    h = [x for x in nb["_build_opener"]().handlers if type(x).__name__ == "HTTPSHandler"][0]
    ctx = h._context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


# -------------------------------------------------------------------------------------------------------------------------------------- redaction
def test_redaction_of_marks_and_evidence_archive(nb, tmp_path):
    assert nb["redact"](f"x {DIAG} y {RT} z {AWS_ID} Bearer {DIAG}") .count("<REDACTED>") >= 3
    d = tmp_path / "attempt"
    (d / "enigma-src").mkdir(parents=True)
    (d / "markers.jsonl").write_text(json.dumps({"oops": RT, "k": DIAG}) + "\n")
    (d / "pip-install.log").write_text(f"Authorization: Bearer {DIAG}\n")
    (d / "enigma-src" / "x.py").write_text("public source")
    (d / "stokastisk.whl").write_bytes(b"PK")
    z = tmp_path / "e.zip"
    files, hits = nb["write_redacted_archive"](d, z)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names).decode()
    assert sorted(names) == ["markers.jsonl", "pip-install.log"] and files == 2 and hits >= 3
    assert DIAG not in blob and RT not in blob and "abcd1234" not in blob


def test_cloudwatch_log_line_never_contains_the_key():
    import importlib.util
    spec = importlib.util.spec_from_file_location("h", ROOT / "infra" / "aws" / "diag_ingest" / "handler.py")
    h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
    src = (ROOT / "infra" / "aws" / "diag_ingest" / "handler.py").read_text()
    printed = [l for l in src.splitlines() if "print(" in l]
    assert printed and not any("secret" in l or "header" in l or "authorization" in l.lower() for l in printed)
