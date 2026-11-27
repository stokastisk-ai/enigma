"""Generate the three Kaggle notebooks (build 5 only) in docs/kaggle/diagnostics/ (run: python3.13 scripts/build_kaggle_diagnostic_notebooks.py).

Why separate notebooks: on 2026-11-26 a "CPU-only" HTTPS test ran inside a GPU session whose Stokastisk vLLM server was already running, so it isolated nothing.
Each notebook here is meant for its OWN fresh Kaggle session and exercises one layer. Every step ends with an explicit PASS / FAIL / INCONCLUSIVE verdict;
anything but PASS raises, so "Run all" stops. Verdicts are only accepted from the current kernel session (attempt id), never from stale outputs.

These notebooks are generated; tests/test_kaggle_diagnostic_notebooks.py checks that the committed files equal this generator's output."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "kaggle" / "diagnostics"

ENIGMA_REV = "8648d91632b5bbf5ae174403a01578c04a5eff20"           # reviewed CONNECTOR-SOURCE commit (has serverctl); pinned separately from the notebook commit
API_URL = "https://enigma-api-production-c8cffe9d.up.railway.app"
DIAG_URL = "https://ygjtcfhrgkhyybl4gotvzmfa440zzlkh.lambda-url.us-east-1.on.aws/"     # independent AWS diagnostic ingestion (Lambda Function URL)
AWS_RUNTIME_URL = "https://ifmsy4cngt3kd474oqayy6b5py0ufiht.lambda-url.us-east-1.on.aws"   # optional AWS runtime API (same FastAPI app on Lambda); ISOLATED staging data until the cutover
AWS_RUNTIME_PROD_URL = ""           # the AWS PRODUCTION runtime endpoint: not deployed until the DynamoDB + Cognito cutover (empty = aws-production is BLOCKED)
# Public, non-secret data-plane fingerprints (enigma_api.settings.Settings.data_plane) of each deployment's configured store, fixed here at generation time:
DATA_PLANES = {"aws-staging": "9a507821bc94305b",          # dynamodb:enigma-rt-staging (isolated staging table)
               "aws-production": "5bdb3004af425d7c",       # dynamodb:enigma-prod (the future authoritative production table)
               "railway-comparison": "432c8dfc55388292"}   # postgres: the Neon production database Railway uses today (derived without exposing the DSN)
WHEEL = "https://huggingface.co/waqasm86/stokastisk/resolve/main/wheels/stokastisk-0.1.0-5-cp313-cp313-linux_x86_64.whl"
WHEEL_SHA256 = "fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e"
WHEEL_SIZE = 38057515
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
# The ONLY replacement installing the immutable build-5 wheel makes on Kaggle (2026-11-25/26 logs): its METADATA pins pydantic-settings==2.16.2 exactly.
KNOWN_REPLACEMENTS = {"pydantic-settings": ["2.16.3", "2.16.2"]}
NO_INPUTS_NOTEBOOKS = ("01_enigma_kaggle_platform_aws_diagnostics",)

NOTEBOOKS = {
    "01_enigma_kaggle_platform_aws_diagnostics": "cpu",
    "02_stokastisk_build5_kaggle_t4_validation": "gpu",
    "03_enigma_stokastisk_build5_aws_integration": "gpu",
}

DURABILITY = ("**Evidence durability.** Every step prints flushed `MARK` and `VERDICT` lines and appends them to an fsynced `markers.jsonl` in a new, "
              "never-reused attempt directory. After a complete platform termination the VM's files may be gone (on 2026-11-26 a new session had to "
              "re-clone everything), but cell output printed before the failure was kept in the saved notebook. **Missing files do not prove that no crash "
              "occurred.** After a fatal session, copy the last `MARK` lines (UTC time, `t` = seconds since the prelude) into the incident report.")

PRELUDE = r'''# P0. Prelude: environment guard, attempt id, markers and verdicts. Standard library only: no network, no GPU library is imported.
import os, sys, json, time, uuid, platform, shutil, subprocess, re, pathlib
NOTEBOOK = "__NOTEBOOK__"
EXPECT = "__EXPECT__"                                   # "cpu" = Accelerator None; "gpu" = GPU T4 x2
assert os.path.isdir("/kaggle/working"), "Kaggle only: refusing to run anywhere else (never on a developer laptop)"


class DiagnosticStop(Exception):
    """Raised by every non-PASS verdict so that 'Run all' stops here. Later cells must never run after a failure."""


HAS_GPU_DEVICE = os.path.exists("/dev/nvidia0")
if EXPECT == "cpu" and HAS_GPU_DEVICE:
    raise DiagnosticStop("INCONCLUSIVE: wrong environment. A GPU is attached; set Accelerator = None and start a NEW session")
if EXPECT == "gpu" and not HAS_GPU_DEVICE:
    raise DiagnosticStop("INCONCLUSIVE: wrong environment. No GPU; set Accelerator = GPU T4 x2 and start a NEW session")
assert sys.version_info[:2] == (3, 13), f"expected Python 3.13 (build-5 wheel is cp313), got {platform.python_version()}"
REQUIRE_NO_INPUTS = __NO_INPUTS__                       # notebook 01 must run with NO attached dataset, competition or utility script
_inputs = sorted(os.listdir("/kaggle/input")) if os.path.isdir("/kaggle/input") else []
if REQUIRE_NO_INPUTS and _inputs:
    raise DiagnosticStop(f"INCONCLUSIVE: wrong environment. Inputs are attached {_inputs}; remove every input and start a NEW session")
# ---- configuration: edit ONLY these lines, before running anything; they are fixed for the whole attempt ----
__MODE_LINE__
# ------------------------------------------------------------------------------------------------------------
assert VALIDATION_MODE in ("standalone", "aws-assisted", "aws-required"), VALIDATION_MODE
assert EXPERIMENT_ID in ("A", "B", "C", "D", "-"), EXPERIMENT_ID
assert RUNTIME_TRANSPORT in ("none", "railway", "aws"), RUNTIME_TRANSPORT
EXPERIMENT = ("build5" if EXPECT == "gpu" else "no-sdk") + ("" if VALIDATION_MODE == "aws-required" else "-" + VALIDATION_MODE) \
    + ("" if EXPERIMENT_ID == "-" else "-exp" + EXPERIMENT_ID) + ("" if RUNTIME_TRANSPORT == "none" else "-via-" + RUNTIME_TRANSPORT)   # in every MARK

ATTEMPT = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
ATTEMPT_DIR = f"/kaggle/working/enigma-diag-attempts/{NOTEBOOK}/{ATTEMPT}"
os.makedirs(ATTEMPT_DIR, exist_ok=False)               # a new directory per attempt: earlier evidence is never overwritten
T0 = time.monotonic()
RESULTS = {}                                           # verdicts of THIS kernel session only (stale outputs can never satisfy a check)

__EVIDENCE__


def _read(path):
    try:
        with open(path) as fh:
            return fh.read(65536)
    except OSError:
        return None


def host():
    """Host memory, cgroup v2 memory (current, max, events), shared memory, disk, uptime and load. No environment variables are read."""
    h = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "Shmem"):
            h[k.lower() + "_mib"] = round(int(v.split()[0]) / 1024, 1)
    mib = lambda s: None if s is None or not s.strip().isdigit() else round(int(s) / 2**20, 1)   # noqa: E731 ("max" = unlimited -> None)
    h["cgroup_current_mib"], h["cgroup_max_mib"] = mib(_read("/sys/fs/cgroup/memory.current")), mib(_read("/sys/fs/cgroup/memory.max"))
    h["cgroup_events"] = {k: int(v) for k, _, v in (l.partition(" ") for l in (_read("/sys/fs/cgroup/memory.events") or "").splitlines()) if v.strip().isdigit()}
    for d in ("/kaggle/working", "/tmp", "/dev/shm"):
        try:
            u = shutil.disk_usage(d)
            h["disk_" + d.strip("/").replace("/", "_")] = {"used_gib": round(u.used / 2**30, 2), "free_gib": round(u.free / 2**30, 2)}
        except OSError:
            pass
    up = _read("/proc/uptime")
    h["vm_uptime_s"] = float(up.split()[0]) if up else None
    h["loadavg"] = os.getloadavg()
    return h


def ident():
    return {"pid": os.getpid(), "ppid": os.getppid(), "pgid": os.getpgid(0), "sid": os.getsid(0)}


def mark(name, **fields):
    rec = {"attempt": ATTEMPT, "nb": NOTEBOOK, "experiment": EXPERIMENT, "mode": VALIDATION_MODE, "transport": RUNTIME_TRANSPORT, "marker": name, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "t": round(time.monotonic() - T0, 2), **ident(), **host(), **fields}
    line = redact(json.dumps(rec, default=str))
    with open(f"{ATTEMPT_DIR}/markers.jsonl", "a") as fh:
        fh.write(line + "\n"); fh.flush(); os.fsync(fh.fileno())
    print("MARK", line, flush=True)
    return rec


def verdict(step, status, reason, **evidence):
    assert status in ("PASS", "FAIL", "INCONCLUSIVE"), status
    RESULTS[step] = {"status": status, "reason": reason, "attempt": ATTEMPT, **evidence}
    mark("verdict", step=step, status=status, reason=reason, **evidence)
    print(f"VERDICT {step}: {status} | {reason}", flush=True)
    if status != "PASS":
        raise DiagnosticStop(f"{step} {status}: {reason}")


def require(*steps):
    """Every prerequisite must have PASSED in this very session (same attempt id)."""
    bad = [s for s in steps if RESULTS.get(s, {}).get("status") != "PASS" or RESULTS[s].get("attempt") != ATTEMPT]
    if bad:
        raise DiagnosticStop(f"INCONCLUSIVE: prerequisite(s) did not PASS in this session: {bad}. Start a new session and run from the top.")


__OBSERVE__


def export_evidence():
    """Zip this attempt's directory (only it), redacting every text file. Safe to run after a failure."""
    path = f"/kaggle/working/enigma-diag-attempts/{NOTEBOOK}-{ATTEMPT}.zip"
    files, hits = write_redacted_archive(ATTEMPT_DIR, path)
    print("evidence archive:", path, os.path.getsize(path), "bytes,", files, "files, redactions:", hits, flush=True)
    return path


_os = dict(l.split("=", 1) for l in (_read("/etc/os-release") or "").splitlines() if "=" in l)
_kaggle = {k: os.environ[k] for k in ("KAGGLE_KERNEL_RUN_TYPE", "KAGGLE_DOCKER_IMAGE") if k in os.environ}   # allow-list only; never the raw environment
mark("prelude", python=platform.python_version(), expect=EXPECT, gpu_device=HAS_GPU_DEVICE, os=_os.get("PRETTY_NAME", "").strip('"'), kaggle=_kaggle, inputs=_inputs)
print("ATTEMPT", ATTEMPT, "| markers:", ATTEMPT_DIR + "/markers.jsonl", flush=True)
'''

GPU_HELPERS = r'''# GPU observation helpers: nvidia-smi in a SEPARATE process (this kernel never imports torch, pynvml or CUDA), plus /proc process-group states.
def gpu_query():
    q = "index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw"
    try:
        r = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
        return {"nvidia_smi_rc": r.returncode, "gpus": [dict(zip(q.split(","), (x.strip() for x in l.split(",")))) for l in r.stdout.strip().splitlines()]}
    except Exception as e:
        return {"nvidia_smi_error": type(e).__name__}


def group_procs(pgid):
    """Every process in process group `pgid`: pid, state, rss (MiB), command name. Read from /proc only."""
    page, out = os.sysconf("SC_PAGE_SIZE"), []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        st = _read(f"/proc/{p}/stat")
        if not st:
            continue
        rest = st.rsplit(")", 1)[1].split()
        if int(rest[2]) == pgid:
            out.append({"pid": int(p), "state": rest[0], "rss_mib": round(int(rest[21]) * page / 2**20, 1), "comm": st[st.find("(") + 1:st.rfind(")")][:24]})
    return out


def inventory():
    import importlib.metadata as md
    norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()                     # noqa: E731
    return {norm(d.metadata["Name"]): d.version for d in md.distributions() if d.metadata["Name"]}
'''

INSTALL = r'''# Immutable Stokastisk build 5 ONLY. The wheel is downloaded and its exact SIZE and SHA-256 are verified BEFORE pip sees it (the version string 0.1.0
# does not distinguish build tags). Dry-run plan first; the one known replacement must be explicitly acknowledged; then install from the verified file,
# a before/after inventory diff, pip check, and the INSTALLED identity (WHEEL build tag) read without importing anything.
WHEEL_URL = "__WHEEL__"
WHEEL_SHA256 = "__SHA__"
WHEEL_SIZE = __SIZE__
KNOWN_REPLACEMENTS = __KNOWN__
ACKNOWLEDGE_PYDANTIC_SETTINGS_DOWNGRADE = False      # set True deliberately: build 5 pins pydantic-settings==2.16.2; Kaggle ships 2.16.3 (2.16.3 is a security release)
import hashlib, urllib.request
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()                                     # noqa: E731
BASE = ("torch", "triton", "transformers", "pydantic", "pydantic-core", "fastapi", "starlette", "numpy")
wheel_path = f"{ATTEMPT_DIR}/stokastisk-0.1.0-5-cp313-cp313-linux_x86_64.whl"
mark("wheel_download_begin", url=WHEEL_URL)
h, n = hashlib.sha256(), 0
with urllib.request.urlopen(WHEEL_URL, timeout=120) as r, open(wheel_path, "wb") as out:
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        h.update(chunk); n += len(chunk); out.write(chunk)
got = h.hexdigest()
mark("wheel_downloaded", size=n, sha256=got)
if n != WHEEL_SIZE or got != WHEEL_SHA256:
    verdict("wheel_verified", "FAIL", f"NOT the published build 5: size {n} sha256 {got}")
verdict("wheel_verified", "PASS", f"published build 5: {n} bytes, sha256 {got[:16]}...", wheel_sha256=got)


def dependents(name):
    import importlib.metadata as md
    out = []
    for d in md.distributions():
        for req in d.requires or []:
            if norm(re.split(r"[ ;<>=!~\[(]", req.strip(), maxsplit=1)[0]) == name:
                out.append(f"{d.metadata['Name']}=={d.version}: {req}")
    return out


def pip_check():
    r = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
    return r.returncode, sorted({l.strip() for l in r.stdout.splitlines() if l.strip() and "no broken requirements" not in l.lower()})


pre_rc, pre_conflicts = pip_check()                    # Kaggle's OWN pre-existing conflicts are recorded, not blamed on build 5
mark("pip_check_before", rc=pre_rc, conflicts=pre_conflicts[:50])
before = inventory()
json.dump(before, open(f"{ATTEMPT_DIR}/packages-before.json", "w"), indent=1, sort_keys=True)
report = f"{ATTEMPT_DIR}/pip-dry-run-report.json"
r = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--report", report, "--index-url", "https://pypi.org/simple", wheel_path],
                   capture_output=True, text=True)
if r.returncode != 0:
    verdict("install_plan", "FAIL", f"pip dry-run failed rc={r.returncode}", stderr_tail=r.stderr[-1500:])
plan = {norm(i["metadata"]["name"]): i["metadata"]["version"] for i in json.load(open(report))["install"]}
replaced = {k: [before[k], v] for k, v in plan.items() if k in before and before[k] != v}
mark("install_plan", would_install=len(plan), replaced=replaced, dependents={k: dependents(k) for k in replaced})
print("DEPENDENCY_VERSION_PRESERVATION:", "VERIFIED" if not replaced else f"CONFLICT {replaced}", flush=True)
unexpected = {k: v for k, v in replaced.items() if KNOWN_REPLACEMENTS.get(k) != v}
if unexpected:
    verdict("install_plan", "INCONCLUSIVE", f"build 5 would replace unexpected preinstalled packages {unexpected}: not installing", replaced=replaced)
if replaced and not ACKNOWLEDGE_PYDANTIC_SETTINGS_DOWNGRADE:
    verdict("install_plan", "INCONCLUSIVE", f"CONFLICT {replaced}: set ACKNOWLEDGE_PYDANTIC_SETTINGS_DOWNGRADE = True to accept it deliberately", replaced=replaced)
verdict("install_plan", "PASS", f"{len(plan)} to install; replacements {replaced or 'none'}", replaced=replaced)

mark("install_begin")
r = subprocess.run([sys.executable, "-m", "pip", "install", "--index-url", "https://pypi.org/simple", wheel_path], capture_output=True, text=True)
open(f"{ATTEMPT_DIR}/pip-install.log", "w").write(r.stdout[-200000:] + "\n--- stderr ---\n" + r.stderr[-50000:])
after = inventory()
changed = {k: [before[k], after[k]] for k in before if k in after and before[k] != after[k]}
removed = sorted(k for k in before if k not in after)
post_rc, post_conflicts = pip_check()
new_conflicts = sorted(set(post_conflicts) - set(pre_conflicts))
json.dump({"before": pre_conflicts, "after": post_conflicts, "introduced": new_conflicts}, open(f"{ATTEMPT_DIR}/pip-check.json", "w"), indent=1)
import importlib.metadata as md
_dist = md.distribution("stokastisk")
_build = next((l.split(":", 1)[1].strip() for l in (_dist.read_text("WHEEL") or "").splitlines() if l.startswith("Build:")), None)
mark("installed_identity", wheel_build=_build, version=_dist.version, changed=changed, removed=removed, pip_check_rc_before=pre_rc,
     pip_check_rc_after=post_rc, introduced_conflicts=new_conflicts)
base_same = all(before.get(b) == after.get(b) for b in BASE)
ok = (r.returncode == 0 and base_same and changed == replaced and not removed and _build == "5" and _dist.version == "0.1.0"
      and not new_conflicts)                           # PASS never hides a dependency incompatibility that the install introduced
verdict("install", "PASS" if ok else "FAIL", f"rc={r.returncode}, base unchanged={base_same}, changed={changed}, installed build={_build}, "
        f"new pip-check conflicts={new_conflicts or 'none'} (pre-existing: {len(pre_conflicts)})", installed_build=_build, changed=changed,
        introduced_conflicts=new_conflicts)
'''

SERVER = r'''# Start the Stokastisk server with the previously successful settings (TP=1, PP=1, 2048 tokens, gpu_memory_utilization 0.60, Triton attention, fp16, eager).
require("install")
for _k in ("no_proxy", "NO_PROXY"):                 # build 5: loopback HTTP honours http_proxy unless no_proxy lists it; set BEFORE the first request
    _have = [x for x in os.environ.get(_k, "").split(",") if x.strip()]
    os.environ[_k] = ",".join(_have + [h for h in ("127.0.0.1", "localhost", "::1") if h not in _have])
MODEL = "__MODEL__"
from stokastisk import StokastiskConfig, serving
from stokastisk.serving import ServerProcess
cfg = StokastiskConfig(tensor_parallel_size=1, pipeline_parallel_size=1, max_model_len=2048, gpu_memory_utilization=0.60)
assert (cfg.attention_backend, cfg.dtype, cfg.enforce_eager) == ("TRITON_ATTN", "half", True), "unexpected server defaults"
assert globals().get("server") is None, "a server already exists in this kernel: start a NEW session"
server = ServerProcess(MODEL, cfg, host="127.0.0.1", port=8000, served_model_name=MODEL, log_path=f"{ATTEMPT_DIR}/vllm-server.log")
mark("server_start_begin")
try:
    server.start_and_wait(timeout=900)
except BaseException as e:
    mark("server_start_failed", error=type(e).__name__, log_tail=server.log_tail(20))
    verdict("server_start", "FAIL", f"server did not become healthy: {type(e).__name__}")
procs = group_procs(server.pid)
mark("server_started", server_pid=server.pid, group=procs, **gpu_query())
verdict("server_start", "PASS" if server.health() else "FAIL", f"pid {server.pid}, {len(procs)} process(es) in its group", server_pid=server.pid)
'''

LOCAL_INFERENCE = r'''# The previously verified local inference test: streamed reply and a rising native vLLM generated-token counter. No Enigma traffic.
require("server_start")
before_m = serving.fetch_metrics(server.base_url)
chunks = list(serving.stream_chat(server.base_url, MODEL, [{"role": "user", "content": "Count from one to ten in words."}], max_tokens=64))
time.sleep(2.5)
after_m = serving.fetch_metrics(server.base_url)
key = next((k for k in after_m if k.startswith("vllm:generation_tokens")), None)
delta = None if key is None else after_m.get(key, 0) - before_m.get(key, 0)
mark("local_inference", chunks=len(chunks), text_chars=len("".join(chunks)), counter=key, generated_delta=delta, **gpu_query())
verdict("local_inference", "PASS" if len(chunks) > 1 and "".join(chunks).strip() and (delta or 0) > 0 else "FAIL",
        f"{len(chunks)} chunks, generated-token counter +{delta}", chunks=len(chunks), generated_delta=delta)
'''

CELLS = ROOT / "scripts" / "kaggle_cells"
REMOTE = (CELLS / "diag_credentials.py").read_text()        # shared credential + transport cell (tests exec this exact text)
EVIDENCE = (CELLS / "evidence.py").read_text()               # inlined into the prelude
OBSERVE = (CELLS / "observe.py").read_text()                 # inlined into the prelude
CAPABILITY = (CELLS / "capability.py").read_text()           # notebook 03 capability probe (tests exec this exact text)
BASELINE = json.loads((CELLS / "baseline_manifest.json").read_text())    # derived by scripts/build_baseline_manifest.py from the executed evidence


SAFE_STOP = r'''# Build-5-safe shutdown (no connector needed): build-5 ServerProcess.stop() is only safe while the server's leader process is unreaped. If the leader
# already exited, stop() is NOT called (it could SIGKILL a reused process group); the exit is reported instead.
p = getattr(server, "_proc", None)
if p is not None and p.poll() is None:
    rc = server.stop()
    mark("server_stopped", rc=rc, report=server.shutdown_report, **gpu_query())
    verdict("server_shutdown", "PASS" if server.shutdown_report.get("group_gone") else "FAIL", f"rc={rc} {server.shutdown_report}")
else:
    mark("server_leader_already_exited", rc=None if p is None else p.returncode, log_tail=server.log_tail(20))
    verdict("server_shutdown", "FAIL", "the server process had already exited before shutdown; build-5 stop() deliberately not called")
'''

CONNECTOR_FETCH = r'''# Fetch the exact reviewed Enigma connector revision (GitHub only; no Enigma request). Verifies the checked-out SHA.
REV = "__REV__"
repo = f"{ATTEMPT_DIR}/enigma-src"
mark("connector_fetch_begin", rev=REV)
for cmd in (["git", "init", "-q", repo], ["git", "-C", repo, "fetch", "-q", "--depth", "1", "https://github.com/stokastisk-ai/enigma.git", REV],
            ["git", "-C", repo, "checkout", "-q", "FETCH_HEAD"]):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        verdict("connector_fetch", "FAIL", f"{cmd[3] if len(cmd) > 3 else cmd[1]} failed rc={r.returncode}", stderr_tail=r.stderr[-500:])
head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
sys.path.insert(0, f"{repo}/connector")
import enigma_connector
from enigma_connector.supervise import ConnectorProcess            # stdlib-only module; run_stage is imported only where a stage runs
ok = head == REV and enigma_connector.__file__.startswith(repo)
verdict("connector_fetch", "PASS" if ok else "FAIL", f"HEAD={head}", head=head, module=enigma_connector.__file__)
'''

TOKEN_GETTER = r'''# Runtime credential (ENIGMA_RUNTIME_TOKEN): read through the shared helper only when a cloud stage needs it; type- and format-checked;
# never printed, stored, or put in os.environ. A diagnostic-key problem can never be reported as a runtime-token problem (separate secrets, codes, steps).
assert API_URL == ENDPOINTS[RUNTIME_TRANSPORT], "transport changed mid-attempt"     # selected by the capability gate; never switched
os.environ.pop("ENIGMA_RUNTIME_TOKEN", None)


def token_getter():
    t, res = get_credential("ENIGMA_RUNTIME_TOKEN")
    if t is None:
        raise ValueError(f"ENIGMA_RUNTIME_TOKEN {res['code']}: {res['message']}")
    return t
'''

EXPORT = r'''# Evidence export (run it even after a FAIL / INCONCLUSIVE stop). Review the archive before sharing it; it contains no credential.
mark("export_begin", results={k: v["status"] for k, v in RESULTS.items()})
archive = export_evidence()
print(json.dumps({k: {"status": v["status"], "reason": v["reason"]} for k, v in RESULTS.items()}, indent=1), flush=True)
'''


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": text.strip("\n").splitlines(keepends=True)}


def sub(text: str, **kw: str) -> str:
    for k, v in kw.items():
        text = text.replace(f"__{k}__", v)
    return text


MODE_LINES = {
    "01_enigma_kaggle_platform_aws_diagnostics": (
        'VALIDATION_MODE = "aws-required"\n'
        'EXPERIMENT_ID = "A"            # ONE per fresh session: "A" AWS-only (default; never contacts Railway) | "B" Railway direct | '
        '"C" AWS runtime endpoint | "D" Railway via a short-lived subprocess\n'
        'RUNTIME_TRANSPORT = "none"'),
    "02_stokastisk_build5_kaggle_t4_validation": (
        'VALIDATION_MODE = "standalone"   # "standalone" = Mode A (no AWS traffic, no secret needed) | "aws-assisted" = Mode B (ENIGMA_DIAG_KEY required)\n'
        'EXPERIMENT_ID = "-"\nRUNTIME_TRANSPORT = "none"'),
    "03_enigma_stokastisk_build5_aws_integration": (
        'VALIDATION_MODE = "aws-required"\nEXPERIMENT_ID = "-"\n'
        'INTEGRATION_MODE = "aws-staging"     # "aws-staging" = AWS runtime endpoint on its ISOLATED staging table (default; staging acceptance only) | '
        '"aws-production" = AWS runtime endpoint on production data (BLOCKED until the DynamoDB + Cognito cutover) | "railway-comparison" = Railway direct\n'
        'ALLOW_RAILWAY_COMPARISON = False     # railway-comparison contacts Railway from Kaggle (associated with every fatal Kaggle session so far): '
        'set True only deliberately\n'
        'RUNTIME_TRANSPORT = {"aws-staging": "aws", "aws-production": "aws", "railway-comparison": "railway"}[INTEGRATION_MODE]   # derived; fixed for '
        'the attempt, never switched, no fallback'),
}


def prelude(name: str) -> dict:
    return code(sub(PRELUDE, NOTEBOOK=name, EXPECT=NOTEBOOKS[name], NO_INPUTS=str(name in NO_INPUTS_NOTEBOOKS), MODE_LINE=MODE_LINES[name],
                    EVIDENCE=EVIDENCE.strip("\n"), OBSERVE=OBSERVE.strip("\n")))


def header(title: str, body: str, name: str) -> dict:
    acc = "**Accelerator: None**" if NOTEBOOKS[name] == "cpu" else "**Accelerator: GPU T4 x2**"
    return md(f"# {title}\n\n{acc} · fresh Kaggle session · Python 3.13 · generated by `scripts/build_kaggle_diagnostic_notebooks.py`\n\n{body}\n\n{DURABILITY}\n\n"
              "Run the cells top to bottom (or *Run all*). Every step ends with `VERDICT <step>: PASS | FAIL | INCONCLUSIVE`; anything but PASS raises and stops "
              "the run. Then run the final **evidence export** cell manually.")


# ------------------------------------------------------------------------------------------------------------------------------------- the three notebooks
SECRETS_MD = ("**Kaggle Secrets (Add-ons → Secrets; tick each one for this notebook).** `ENIGMA_DIAG_KEY` = the 56-character diagnostic key "
              "(`edx_…`, from the private local file `.enigma-local/ENIGMA_DIAG_KEY.txt`); it authenticates only the independent AWS diagnostic "
              "Lambda. `ENIGMA_RUNTIME_TOKEN` = the 60-character Enigma runtime key (`enig_rt_…`); it authenticates only Enigma runtime "
              "traffic. They are never interchangeable and neither is an AWS credential (no AWS access key is ever needed in Kaggle). If a "
              "credential preflight cell reports a problem, fix the secret in the sidebar and **re-run only that cell** (and the cell after "
              "it): the attempt id, event sequence and earlier verdicts are kept. A new Kaggle session always starts from the top.")
DIAG_PROBE = r'''require("diag_key_preflight")
first = rmark("aws_first_event", stage="aws", phase="probe", note="first independent AWS event")
if not first:
    verdict("aws_endpoint", "INCONCLUSIVE", f"{first['code']}: {DIAG_GUIDANCE.get(first['code'], '')}", remote=dict(first))
dup = resubmit_last()                   # the SAME event (same attempt id and seq) again: a durable record must answer as an already-stored duplicate
durable = bool(dup) and dup.get("duplicates") == 1 and dup.get("stored") == 0
verdict("aws_endpoint", "PASS" if durable else "INCONCLUSIVE",
        f"first {first['code']} (stored={first.get('stored')}, duplicates={first.get('duplicates')}); identical resubmission {dup.get('code')} "
        f"(duplicates={dup.get('duplicates')})", first=dict(first), resubmit=dict(dup))'''


def nb_01() -> list[dict]:
    n = "01_enigma_kaggle_platform_aws_diagnostics"
    controls = r"""require("idle_10min")
import urllib.request, urllib.error
def get(name, url):
    mark("request_begin", target=name)
    t = time.monotonic(); row = {"target": name}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Enigma-Kaggle-Diagnostic/6.0", "Accept": "*/*", "Connection": "close"})
        with urllib.request.urlopen(req, timeout=60) as r:
            row["http"] = r.status; r.read(4096)
    except urllib.error.HTTPError as e:
        row["http"] = e.code
    except Exception as e:
        row["exc"] = type(e).__name__
    row["latency_s"] = round(time.monotonic() - t, 3)
    mark("request_end", **row)
    return row
rows = [get("pypi", "https://pypi.org/simple/pip/"), get("gstatic", "https://www.gstatic.com/generate_204"), get("huggingface", "https://huggingface.co/api/models/Qwen/Qwen2.5-0.5B-Instruct")]
ok = all(r.get("http") in (200, 204) for r in rows)
verdict("controls_https", "PASS" if ok else "INCONCLUSIVE", f"{[(r['target'], r.get('http'), r.get('latency_s')) for r in rows]}")"""
    aws_window = r"""require("aws_endpoint")
# Experiment A: 10 minutes of AWS-only survival, one durable marker per minute (10 requests): it runs well past the VM uptime (~1390 s) at which
# the 2026-11-26 23:31 session ended, so a lifetime-only explanation is tested without Railway. Other experiments: 2 minutes, 4 markers.
if EXPERIMENT_ID == "A":
    observe("aws_survival", 600, every=15, remote_at=tuple(range(60, 601, 60)), durable_begin=True)
else:
    observe("aws_survival", 120, every=15, remote_at=(30, 60, 90, 120), durable_begin=True)"""
    target = sub(r"""# The ONE target request of this experiment (A: none). Exactly one bounded request; never repeated automatically after an unexplained fatal session.
require("aws_survival")
TARGETS = {"B": ("railway_health", "__API__/api/v1/health"), "C": ("aws_runtime_health", "__AWSRT__/api/v1/health"),
           "D": ("railway_health_subprocess", "__API__/api/v1/health")}
CHILD = r'''
import json, sys, time, urllib.request, urllib.error
t = time.monotonic(); row = {}
try:
    req = urllib.request.Request(sys.argv[1], headers={"User-Agent": "Enigma-Kaggle-Diagnostic/6.0", "Accept": "application/json", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=60) as r:
        row["http"] = r.status; r.read(4096)
except urllib.error.HTTPError as e:
    row["http"] = e.code
except Exception as e:
    row["exc"] = type(e).__name__
row["latency_s"] = round(time.monotonic() - t, 3)
print(json.dumps(row))
'''
if EXPERIMENT_ID == "A":
    mark("target_not_applicable", note="experiment A never contacts Railway or the AWS runtime endpoint")
else:
    name, url = TARGETS[EXPERIMENT_ID]
    assert "PENDING" not in url, "experiment C needs the deployed AWS runtime endpoint (AWS_RUNTIME_URL)"
    rcheck("target_marker", "before_target", stage=name, phase="before", target=name)             # no target request without a durable marker
    t = time.monotonic(); row = {"target": name}
    if EXPERIMENT_ID == "D":
        # Same destination and request semantics, but from a short-lived child Python process (bounded lifetime; only this child is ever signalled).
        child = subprocess.Popen([sys.executable, "-I", "-c", CHILD, url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        mark("child_started", child_pid=child.pid)
        try:
            out, _ = child.communicate(timeout=90)
            row.update(json.loads(out.strip().splitlines()[-1]) if out.strip() else {"exc": "NoOutput"})
        except subprocess.TimeoutExpired:
            child.kill(); child.communicate(); row["exc"] = "ChildTimeout"
        except ValueError:
            row["exc"] = "BadChildOutput"
        row.update(child_pid=child.pid, child_exit=child.returncode)
    else:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Enigma-Kaggle-Diagnostic/6.0", "Accept": "application/json", "Connection": "close"})
            with urllib.request.urlopen(req, timeout=60) as r:
                row["http"] = r.status; r.read(4096)
        except urllib.error.HTTPError as e:
            row["http"] = e.code
        except Exception as e:
            row["exc"] = type(e).__name__
        row["latency_s"] = row.get("latency_s") or round(time.monotonic() - t, 3)
    row["wall_s"] = round(time.monotonic() - t, 3)
    mark("target_end", **row)
    after = rmark("after_target", stage=name, phase="after", target=name, http=row.get("http"), latency_s=row.get("latency_s"), exc=row.get("exc"),
                  child_pid=row.get("child_pid"), child_exit=row.get("child_exit"))
    # Independent outcomes: the HTTP request itself, and the durable after-marker. Survival is judged by the next cell.
    verdict("target_request", "PASS" if row.get("http") == 200 else "FAIL", f"{row}", **{k: v for k, v in row.items() if k != "target"})
    verdict("target_after_marker", "PASS" if after else "INCONCLUSIVE", f"after-marker {after['code']}")""", API=API_URL, AWSRT=AWS_RUNTIME_URL)
    post = r"""# Post-target observation, in two SEPARATE phases so a termination can be attributed:
#   1) 60 s PASSIVE: no network at all; a local, printed tick every 2 s (the saved notebook output brackets a termination to ~2 s).
#   2) 60 s ACTIVE: durable AWS markers at +5, +15, +30 and +60 s of that phase, each bracketed by local submit / result markers.
if EXPERIMENT_ID != "A":
    require("target_request", "target_after_marker")
    observe("post_target_passive_60s", 60, every=2, dense_first_s=60, dense_every=2)
    observe("post_target_active_60s", 60, every=5, remote_at=(5, 15, 30, 60))
else:
    mark("post_target_not_applicable")"""
    final = r"""# Overall verdict. Outcomes are reported separately: HTTP success, durable markers, survival windows, overall.
REQUIRED = ["idle_10min", "controls_https", "controls_survival_120s", "diag_key_preflight", "aws_endpoint", "aws_survival"]
if EXPERIMENT_ID != "A":
    REQUIRED += ["target_request", "target_after_marker", "post_target_passive_60s", "post_target_active_60s"]
summary = {s: RESULTS.get(s, {}).get("status", "NOT_RUN") for s in REQUIRED}
mark("outcomes", experiment_id=EXPERIMENT_ID, **summary)
require(*REQUIRED)
rcheck("overall", "final", verdict="PASS", note=f"experiment {EXPERIMENT_ID}: all {len(REQUIRED)} steps PASSED")
verdict("overall", "PASS", f"experiment {EXPERIMENT_ID}: all {len(REQUIRED)} steps PASSED in this session (status code 44 root cause: NOT_ESTABLISHED)")"""
    return [
        header("01. Kaggle platform + independent AWS networking diagnostics (experiments A-D)", "A troubleshooting control only: **no Stokastisk, no "
               "CUDA, no LLM, no Enigma connector, no runtime token, no Git clone, no boto3, no AWS credential, no Docker/Kubernetes.** Choose ONE "
               "experiment per FRESH session in the first code cell (`EXPERIMENT_ID`):\n\n"
               "* **A (default): AWS-only.** 10-min offline idle, public HTTPS, credential preflight, AWS ack + duplicate test, 10 min AWS survival (past the uptime at which earlier sessions died). "
               "Never contacts Railway.\n"
               "* **B: Railway direct.** As A (2-min AWS window), then ONE durable before-marker, ONE Railway `/api/v1/health`, a durable after-marker, "
               "60 s PASSIVE observation (no network, a printed tick every 2 s), then 60 s with 4 durable markers.\n"
               "* **C: AWS runtime endpoint.** As B, but the one request goes to the optional AWS runtime API health endpoint instead of Railway.\n"
               "* **D: Railway via a short-lived subprocess.** As B, but the same request runs in a bounded child Python process (pid + exit code "
               "recorded).\n\n"
               "Do not repeat B or D in the same session after an unexplained termination. Attach NO inputs; only the Kaggle Secret `ENIGMA_DIAG_KEY`."
               f"\n\n{SECRETS_MD}\n\n"
               "**Reading the result:** AWS (table `enigma-diag`, partition `ATT#<attempt>`) keeps every acknowledged marker even if the VM disappears; "
               "the saved notebook output keeps the printed local ticks. Remote logging cannot capture the final instant of a VM termination. "
               "Correlation is not a cause: only Kaggle's platform logs can explain a status-code-44 termination.", n),
        prelude(n),
        code(sub(REMOTE, DIAG=DIAG_URL)),
        md("## 01a. Idle survival: 10 minutes, PASSIVE, no network at all (no secret is read yet)"),
        code('observe("idle_10min", 600, every=15)'),
        md("## 01b. Ordinary public HTTPS controls, then 2 minutes of PASSIVE survival"),
        code(controls),
        code('require("controls_https")\nobserve("controls_survival_120s", 120, every=15)'),
        md("## 01c. Diagnostic credential preflight (re-runnable)\nReads `ENIGMA_DIAG_KEY` (a request to Kaggle's own secret service, nothing else) "
           "and checks its type and format locally. On a problem: fix the secret in the sidebar, then re-run **this cell only**; no new 10-minute idle "
           "observation is needed in the same healthy session."),
        code('require("controls_survival_120s")\nrefresh_credentials("ENIGMA_DIAG_KEY")\ncheck_credential("ENIGMA_DIAG_KEY", "diag_key_preflight")'),
        md("## 01d. Independent AWS diagnostic endpoint: first event, then an identical resubmission that must come back as a durable duplicate"),
        code(DIAG_PROBE),
        md("## 01e. AWS survival window (durable observation-start marker first; bounded markers)"),
        code(aws_window),
        md("## 01f. Experiments B / C / D only: the ONE target request (durable markers before and after)"),
        code(target),
        md("## 01g. Experiments B / C / D only: passive, then active post-target observation"),
        code(post),
        md("## 01h. Outcomes and overall verdict"),
        code(final),
        md("## Evidence export (local, redacted; the AWS record is the durable copy). Run it even after a stop."),
        code(EXPORT),
    ]


def nb_02() -> list[dict]:
    n = "02_stokastisk_build5_kaggle_t4_validation"
    mode = r"""# 02-0. Experimental mode (set VALIDATION_MODE in the first code cell, before running anything).
#   Mode A "standalone":   strict standalone GPU validation. No AWS diagnostic traffic, no Kaggle Secret read, local evidence only.
#   Mode B "aws-assisted": ENIGMA_DIAG_KEY is validated and a durable AWS acknowledgement is verified HERE, before any expensive GPU installation or
#                          model start; critical install / server / inference / shutdown markers are persisted to AWS. NOT cloud-independent.
# Neither mode sends anything to Railway or Enigma, and neither uses an Enigma runtime token.
REMOTE_ENABLED = VALIDATION_MODE == "aws-assisted"
mark("mode_selected", remote_enabled=REMOTE_ENABLED)
if REMOTE_ENABLED:
    refresh_credentials("ENIGMA_DIAG_KEY")
    check_credential("ENIGMA_DIAG_KEY", "diag_key_preflight")
    first = rmark("aws_first_event", stage="mode", note="aws-assisted GPU validation")
    if not first:
        verdict("remote_ready", "INCONCLUSIVE", f"{first['code']}: {DIAG_GUIDANCE.get(first['code'], '')} (stopping before any GPU work)")
    verdict("remote_ready", "PASS", f"Mode B aws-assisted: durable AWS acknowledgement seq={first.get('seq')}")
else:
    verdict("remote_ready", "PASS", "Mode A standalone: no AWS diagnostic traffic and no secret read; local evidence only")"""
    env = r"""require("remote_ready")
import importlib.metadata as md
g = gpu_query()
names = [x.get("name") for x in g.get("gpus", [])]
vers = {p: (md.version(p) if p in {d.metadata["Name"].lower() for d in md.distributions()} else None) for p in ("torch", "triton", "transformers")}
mark("environment", python=platform.python_version(), gpus=names, versions=vers, **g)
ok = names == ["Tesla T4", "Tesla T4"] and vers == {"torch": "2.11.0+cu130", "triton": "3.6.0", "transformers": "5.20.2"}
verdict("environment", "PASS" if ok else "INCONCLUSIVE", f"GPUs {names}, {vers} (required: 2x Tesla T4, torch 2.11.0+cu130, triton 3.6.0, transformers 5.20.2)")"""
    passive = r"""require("local_inference")
def passive_sample():
    procs = group_procs(server.pid)
    states = sorted({p["state"] for p in procs})
    problem = None if server.running and procs and "Z" not in states else f"server_running={server.running} states={states}"
    return {"server_running": server.running, "group_size": len(procs), "states": states, "rss_mib": round(sum(p["rss_mib"] for p in procs), 1),
            "problem": problem, **(gpu_query() if int(time.monotonic()) % 60 < 15 else {})}
observe("server_passive_10min", 600, every=15, sample=passive_sample)
mark("server_log_tail", log_tail=server.log_tail(20))"""
    return [
        header("02. Stokastisk build 5: Kaggle 2×T4 validation (Mode A standalone / Mode B AWS-assisted)", f"Immutable `stokastisk-0.1.0-5` only "
               f"(size + SHA-256 verified before install). {MODEL}, FP16, TRITON_ATTN, TP=1, PP=1, 2048 tokens, gpu_memory_utilization 0.60. **No "
               "Enigma / Railway connection and no runtime token in either mode.** Choose the mode in the first code cell: `standalone` (default; no "
               "secret needed) or `aws-assisted` (needs the Kaggle Secret `ENIGMA_DIAG_KEY`; stops before any GPU work if AWS does not acknowledge)."
               f"\n\n{SECRETS_MD}", n),
        prelude(n),
        code(GPU_HELPERS),
        code(sub(REMOTE, DIAG=DIAG_URL)),
        md("## 02-0. Mode and (Mode B only) diagnostic credential + durable AWS acknowledgement. Re-runnable after fixing the secret."),
        code(mode),
        md("## 02a. Environment (Python 3.13, 2×T4, Kaggle's torch / triton / transformers)"),
        code(env),
        md("## 02b. Install the immutable build-5 wheel (size + SHA-256 verified first; pydantic-settings conflict must be acknowledged; no new pip-check conflict)"),
        code('require("environment")\nrcheck("remote_install_begin", "install_begin", stage="install", phase="before")\n'
             + sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, SIZE=str(WHEEL_SIZE), KNOWN=json.dumps(KNOWN_REPLACEMENTS))
             + '\nrmark("install_end", stage="install", wheel_build=_build, wheel_sha256=WHEEL_SHA256)'),
        md("## 02c. Start the server (loopback `no_proxy` set first)"),
        code('require("install")\nrcheck("remote_server_begin", "server_start_begin_remote", stage="server", phase="before")\n'
             + sub(SERVER, MODEL=MODEL) + '\nrmark("server_started", stage="server")'),
        md("## 02d. Real local inference and native vLLM metrics"),
        code(LOCAL_INFERENCE + '\nrmark("local_inference_done", stage="inference")'),
        md("## 02e. Passive server observation: 10 minutes (no remote marker during the window)"),
        code(passive + '\nrmark("server_observed_10min", stage="observe", verdict="PASS")'),
        md("## 02f. Build-5-safe shutdown"),
        code(SAFE_STOP + 'rmark("final", verdict=RESULTS["server_shutdown"]["status"])'),
        md("## Evidence export (local, redacted)"),
        code(EXPORT),
    ]


NB03_ENV = r"""# 03-0. GPU environment guard (local only: nvidia-smi in a separate process, /proc, a loopback socket; no network, no CUDA in this kernel).
# The same inventory approach as the verified notebook 02, plus SM75, free GPU memory, host memory and "no inference server already running".
EXPERIMENT = f"build5-{INTEGRATION_MODE}"              # every later MARK names the integration mode
assert INTEGRATION_MODE in ("aws-staging", "aws-production", "railway-comparison"), INTEGRATION_MODE
import importlib.metadata as md, socket
g = gpu_query()
names = [x.get("name") for x in g.get("gpus", [])]
try:
    _r = subprocess.run(["nvidia-smi", "--query-gpu=compute_cap,memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
    caps = [tuple(x.strip() for x in l.split(",")) for l in _r.stdout.strip().splitlines()]
except Exception as e:
    caps = [("error:" + type(e).__name__, "0")]
vers = {p: (md.version(p) if p in {d.metadata["Name"].lower() for d in md.distributions()} else None) for p in ("torch", "triton", "transformers")}
EXPECTED_VERSIONS = {"torch": "2.11.0+cu130", "triton": "3.6.0", "transformers": "5.20.2"}
_h = host()
with socket.socket() as _s:
    _s.settimeout(1.0)
    port_busy = _s.connect_ex(("127.0.0.1", 8000)) == 0
servers = []
for _p in os.listdir("/proc"):
    if _p.isdigit() and int(_p) != os.getpid():
        _c = (_read(f"/proc/{_p}/cmdline") or "").replace("\x00", " ")
        if "vllm" in _c and ("serve" in _c or "entrypoints" in _c):
            servers.append(int(_p))
checks = {"python_3_13": sys.version_info[:2] == (3, 13), "two_tesla_t4": names == ["Tesla T4", "Tesla T4"],
          "sm75": [c[0] for c in caps] == ["7.5", "7.5"], "gpu_free_mib_ge_14000": all(c[1].isdigit() and int(c[1]) >= 14000 for c in caps) and len(caps) == 2,
          "host_available_mib_ge_24000": (_h.get("memavailable_mib") or 0) >= 24000, "versions_as_validated": vers == EXPECTED_VERSIONS,
          "no_local_server": not port_busy and not servers}
mark("environment", integration_mode=INTEGRATION_MODE, python=platform.python_version(), gpus=names, compute_caps=caps, versions=vers, port_8000_busy=port_busy,
     vllm_pids=servers, checks=checks, **g)
bad = [k for k, v in checks.items() if not v]
verdict("environment", "PASS" if not bad else "INCONCLUSIVE",
        f"{names}, SM {[c[0] for c in caps]}, free MiB {[c[1] for c in caps]}, {vers}" + (f"; NOT as validated: {bad} (start a NEW GPU T4 x2 session; "
        "a version difference means the historical baseline does not cover this image)" if bad else "; matches the validated notebook 02 environment"),
        checks=checks)"""

NB03_BASELINE = r"""# 03-2. HISTORICAL baseline evidence (not an operator checkbox): a small manifest derived by scripts/build_baseline_manifest.py from the owner's
# executed notebook 01 / 02 runs and their diagnostic archives (hashes of the source files are inside). Its digest is re-verified here. This proves what
# PASSED in those earlier sessions only; this session still verifies its own environment, the wheel bytes, the install and real local inference.
require("environment")
import hashlib
BASELINE = json.loads(__BASELINE_JSON__)
_core = {k: v for k, v in BASELINE.items() if k not in ("generated_utc", "manifest_sha256")}
_digest = hashlib.sha256(json.dumps(_core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
acc, b2 = BASELINE["acceptance"], BASELINE["nb02"]
checks = {"manifest_digest": _digest == BASELINE["manifest_sha256"] == "__BASELINE_SHA__",
          "nb01_expA_overall": acc["NOTEBOOK_01_AWS_ONLY_BASELINE"] == "PASS" and BASELINE["nb01"]["railway_requests"] == 0,
          "nb02_local_inference": acc["BUILD5_LOCAL_GPU_INFERENCE"] == "PASS", "nb02_600s_stability": acc["BUILD5_SERVER_600S_STABILITY"] == "PASS",
          "nb02_safe_shutdown": acc["BUILD5_SAFE_SHUTDOWN"] == "PASS", "no_conflict_introduced": acc["BUILD5_DEPENDENCY_CONFLICTS_INTRODUCED"] == 0,
          "wheel_identity": (b2["wheel"]["sha256"], b2["wheel"]["size"], b2["wheel"]["wheel_build_tag"]) == ("__SHA__", __SIZE__, "5"),
          "model_config": (b2["model_config"]["model"], b2["model_config"]["tensor_parallel_size"], b2["model_config"]["pipeline_parallel_size"],
                           b2["model_config"]["dtype"], b2["model_config"]["attention"], b2["model_config"]["max_seq_len"]) == ("__MODEL__", "1", "1", "float16", "TRITON_ATTN", "2048"),
          "environment_matches_baseline": b2["versions"] == vers and b2["gpus"] == names}
mark("baseline_manifest", digest=_digest, attempts=[BASELINE["nb01"]["attempt"], b2["attempt"]], acceptance=acc, checks=checks)
bad = [k for k, v in checks.items() if not v]
verdict("baseline_manifest", "PASS" if not bad else "INCONCLUSIVE",
        f"HISTORICAL: nb01 {BASELINE['nb01']['attempt']} expA PASS; nb02 {b2['attempt']} inference/600 s/shutdown PASS; wheel {b2['wheel']['sha256'][:16]}..."
        + (f"; FAILED checks {bad}" if bad else "; current environment equals the validated one"), checks=checks)"""

NB03_CAPABILITY = r"""# 03-3. Capability gate: EVIDENCE from the SELECTED runtime endpoint only (the other endpoints are never contacted). One public, unauthenticated
# health probe (no credential), bracketed by REQUIRED durable AWS markers. aws-production is BLOCKED (no request at all) until the production AWS runtime
# endpoint exists; railway-comparison needs ALLOW_RAILWAY_COMPARISON = True. HTTP 200 alone is never readiness: the schema, the deployment label, the
# store, the auth provider and the data-plane fingerprint fixed at generation time must all match the selected mode.
require("baseline_manifest", "aws_endpoint", "runtime_token_preflight")
MODES = {"aws-staging": {"url": "__AWSRT__", "expect": {"deployment": "aws-staging-isolated", "store": "dynamodb", "auth": "cognito", "data_plane": "__DP_STAGING__"}},
         "aws-production": {"url": "__AWSPROD__", "expect": {"deployment": "production", "store": "dynamodb", "auth": "cognito", "data_plane": "__DP_PROD__"}},
         "railway-comparison": {"url": "__API__", "expect": {"deployment": "production", "store": "postgres", "auth": "neon", "data_plane": "__DP_RAILWAY__"}}}
SCOPE = {"aws-staging": "STAGING", "aws-production": "PRODUCTION", "railway-comparison": "RAILWAY-COMPARISON"}[INTEGRATION_MODE]
API_URL = MODES[INTEGRATION_MODE]["url"]                # fixed for the attempt; the connector is given only this URL and never falls back
if INTEGRATION_MODE == "aws-production" and not API_URL:
    verdict("runtime_capabilities", "INCONCLUSIVE", "BLOCKED: the AWS PRODUCTION runtime endpoint does not exist yet (production still runs on Railway + Neon; "
            "the DynamoDB + Cognito cutover is pending). No request was sent and no GPU work runs. Use INTEGRATION_MODE = \"aws-staging\" for the isolated "
            "staging integration; do not bypass this gate.", blocked=True)
if INTEGRATION_MODE == "railway-comparison" and not ALLOW_RAILWAY_COMPARISON:
    verdict("runtime_capabilities", "INCONCLUSIVE", "BLOCKED: railway-comparison contacts Railway from Kaggle; set ALLOW_RAILWAY_COMPARISON = True deliberately",
            blocked=True)
ALLOWED_HOST = urllib.parse.urlsplit(API_URL).hostname
rcheck("capability_marker", "before_capability_probe", stage="capability", phase="before", target=RUNTIME_TRANSPORT, note=INTEGRATION_MODE)
probe = probe_health(API_URL, ALLOWED_HOST, wall_timeout=20.0)
body = probe["body"] if isinstance(probe["body"], dict) else {}
sch = check_schema(probe["body"], 2) if probe["http"]["result"] == "PASS" else {"result": "FAIL", "why": "no HTTP 200"}
dep = check_deployment(body, MODES[INTEGRATION_MODE]["expect"]) if sch["result"] == "PASS" else {"result": "FAIL", "why": "schema not valid"}
after = rmark("after_capability_probe", stage="capability", phase="after", target=RUNTIME_TRANSPORT, http=probe["http"].get("status"),
              latency_s=probe["latency_s"], note=f"{INTEGRATION_MODE} http={probe['http']['result']} schema={sch['result']} deployment={dep['result']}")
CAP = {"CAPABILITY_HTTP_RESULT": probe["http"], "CAPABILITY_SCHEMA_RESULT": sch, "CAPABILITY_DEPLOYMENT_RESULT": dep,
       "CAPABILITY_DURABLE_EVIDENCE_RESULT": {"result": "PASS" if after else "FAIL", "before": "acknowledged", "after": after.get("code")}}
for k, v in CAP.items():
    print(f"{k}: {v['result']} | {json.dumps({x: y for x, y in v.items() if x != 'result'})}", flush=True)
ok = all(v["result"] == "PASS" for v in CAP.values())
verdict("runtime_capabilities", "PASS" if ok else "INCONCLUSIVE",
        f"{SCOPE} via {RUNTIME_TRANSPORT}: " + ", ".join(f"{k.split('_')[1]}={v['result']}" for k, v in CAP.items())
        + (f" | deployment={body.get('deployment')!r} store={body.get('store')!r} auth={body.get('auth')!r}" if body else "")
        + ("" if ok else " | NOT the expected data plane: no GPU or cloud stage runs"), capability={k: v["result"] for k, v in CAP.items()}, scope=SCOPE,
        deployment=body.get("deployment"), store=body.get("store"), auth=body.get("auth"), data_plane=body.get("data_plane"))"""

NB03_IDENTITY = r"""# 03-4. Runtime credential: ACTUAL authentication against the selected endpoint, before any GPU work. One read-only, side-effect-free
# GET /api/v1/runtimes/me (no registration, no job operation). Requires a REAL (non-mock) runtime. In aws-staging the secret ENIGMA_RUNTIME_TOKEN must
# hold the STAGING runtime token (scripts/aws_staging_runtime.py provision); a production token is not known to the staging table and fails here (401).
require("runtime_capabilities")
rcheck("identity_marker", "before_runtime_identity", stage="identity", phase="before", target=RUNTIME_TRANSPORT)
_tok, _res = get_credential("ENIGMA_RUNTIME_TOKEN")
if _tok is None:
    verdict("runtime_identity", "INCONCLUSIVE", f"ENIGMA_RUNTIME_TOKEN {_res['code']}: {_res['message']}")
_req = urllib.request.Request(API_URL.rstrip("/") + "/api/v1/runtimes/me", headers={"Authorization": f"Bearer {_tok}", "Accept": "application/json",
                                                                                     "User-Agent": "Enigma-Kaggle-Capability/7.0"})
del _tok
who, st, t = {}, None, time.monotonic()
try:
    with _cap_opener().open(_req, timeout=20) as r:
        st, who = r.status, json.loads(r.read(4096))
except urllib.error.HTTPError as e:
    st = e.code
except Exception as e:
    st = "exc:" + type(e).__name__
del _req
who = who if isinstance(who, dict) else {}
after = rmark("after_runtime_identity", stage="identity", phase="after", http=st if isinstance(st, int) else None, latency_s=round(time.monotonic() - t, 3),
              note=f"is_mock={who.get('is_mock')}")
ok = st == 200 and who.get("is_mock") is False and bool(who.get("runtime_id")) and bool(after)
hint = {401: "the token is not accepted by THIS endpoint (wrong token for the mode, revoked, or expired)",
        404: "the endpoint does not serve /runtimes/me yet (API contract too old)"}.get(st, "")
verdict("runtime_identity", "PASS" if ok else "INCONCLUSIVE",
        f"http {st}; real runtime={who.get('is_mock') is False}; durable after-marker={bool(after)}" + (f" | {hint}" if hint and not ok else ""),
        runtime_id=who.get("runtime_id"), is_mock=who.get("is_mock"))"""

NB03_SURVIVAL = r"""# 03-5. Post-capability survival gate. Every fatal Kaggle session so far ended seconds after a Railway response; nothing is installed until this
# session demonstrably survives its endpoint contact. Durable start marker -> 90 s PASSIVE window (local, fsynced, printed ticks every 2 s for 30 s,
# then every 5 s; no network) -> durable completion marker. If the whole VM is terminated, only the markers already stored in AWS remain.
require("runtime_identity")
observe("post_capability_survival", 90, every=5, dense_first_s=30, dense_every=2, durable_begin=True)
rcheck("post_capability_survival_marker", "post_capability_survival_complete", stage="survival", phase="after", verdict="PASS")"""

NB03_HELPER = r"""from enigma_connector.stage_runner import run_stage, MarkerLog
from enigma_connector import serverctl
INTERVAL_ARGS = ("--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0")      # explicit finite intervals, as in every verified run
ORDER = ("nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full")


def stage(name, **kw):
    # One SUPERVISED connector subprocess per stage (never in this kernel). Prerequisites (result AND durable evidence) must have PASSED in THIS session.
    prev = ORDER[:ORDER.index(name)]
    require("connector_fetch", "local_inference", *[f"stage_{p}" for p in prev], *[f"stage_{p}_evidence" for p in prev])
    if name not in ("nvml", "vllm"):
        require("cloud_gate")
    assert server.health(), "local vLLM unhealthy"
    cloud = name not in ("nvml", "vllm")
    # The before-marker MUST be durable (a failed one stops here). Offline stages send nothing DURING their measured interval.
    rcheck(f"stage_{name}_marker", f"before_stage_{name}", stage=name, phase="before")
    res = run_stage(name, api_url=API_URL if cloud else "", token_getter=token_getter if cloud else None, vllm_url=server.base_url, model=MODEL, tp=1,
                    server=server, workdir=f"{ATTEMPT_DIR}/stages", extra_args=INTERVAL_ARGS, markers=MarkerLog(f"{ATTEMPT_DIR}/markers.jsonl", echo=print), **kw)
    stats = (res.get("summary") or {}).get("stats") or {}
    extra_ok, why = True, ""
    if name == "jobs":
        extra_ok, why = stats.get("jobs_done") == 0 and stats.get("jobs_failed") == 0, "job stage must stay idle"
    if name == "full":
        extra_ok, why = (stats.get("jobs_done") or 0) >= 1 and stats.get("jobs_failed") == 0, "at least one real Playground job must complete"
    failed = [c[0] for c in res["checks"] if not c[1]] + ([why] if not extra_ok else [])
    status = "PASS" if res["passed"] and extra_ok else "FAIL"
    # The stage RESULT is recorded first and independently: a failed stage stays failed even if its marker is stored ...
    after = rmark(f"after_stage_{name}", stage=name, phase="after", exit_code=res["exit"]["returncode"], signal=res["exit"].get("killed_by_signal"), verdict=status)
    verdict(f"stage_{name}", status, f"rc={res['exit']['returncode']} failed={failed}", run_id=(res.get("summary") or {}).get("run_id"), stats=stats)
    # ... and a passed stage whose required durable evidence is missing can never satisfy the acceptance gate.
    verdict(f"stage_{name}_evidence", "PASS" if after else "INCONCLUSIVE", f"after-stage AWS marker {after.get('code')}")
    return res"""

NB03_CLOUD_GATE = r"""# 03-7. Cloud gate (evidence, not a checkbox): the selected endpoint's capabilities and the REAL runtime identity were verified before any GPU work,
# the session survived, and both offline stages passed with durable evidence. A queued Playground job would be claimed by the idle-polling stage and
# fail it (it must stay idle): submit NOTHING before stage "full".
require("runtime_capabilities", "runtime_identity", "post_capability_survival", "stage_nvml", "stage_nvml_evidence", "stage_vllm", "stage_vllm_evidence")
assert API_URL == MODES[INTEGRATION_MODE]["url"], "transport changed mid-attempt"
rcheck("cloud_gate_marker", "cloud_gate_open", stage="cloud_gate", phase="before", target=RUNTIME_TRANSPORT, note=SCOPE)
verdict("cloud_gate", "PASS", f"{SCOPE} cloud stages enabled against {urllib.parse.urlsplit(API_URL).hostname} only")"""

NB03_SHUTDOWN = r"""# 03-9. Shutdown with the build-5-safe orchestration from the reviewed connector (never patches the installed wheel).
rep = serverctl.safe_stop(server)
mark("server_safe_stop", report=rep, **gpu_query())
ok = rep.get("action") == "package_stop" and rep.get("report", {}).get("group_gone")
rmark("shutdown_done", stage="shutdown", phase="after", verdict="PASS" if ok else "FAIL", note=str(rep.get("action")))
verdict("server_shutdown", "PASS" if ok else "FAIL", f"{rep}")"""

NB03_OVERALL = r"""# 03-10. OVERALL acceptance. Run it last, also after a stop (it reads this session's verdicts only). PASS needs EVERY step below, including safe
# shutdown and a durable final AWS verdict. The scope is stated explicitly: STAGING acceptance is never production acceptance.
REQUIRED = ["environment", "diag_key_preflight", "runtime_token_preflight", "aws_endpoint", "baseline_manifest", "runtime_capabilities", "runtime_identity",
            "post_capability_survival", "wheel_verified", "install_plan", "install", "server_start", "local_inference", "connector_fetch", "cloud_gate",
            *[f"stage_{s}{e}" for s in ("nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full") for e in ("", "_evidence")],
            "server_shutdown"]
got = {s: RESULTS.get(s, {}).get("status", "NOT_RUN") if RESULTS.get(s, {}).get("attempt") in (ATTEMPT, None) else "STALE" for s in REQUIRED}
if all(v == "PASS" for v in got.values()):
    outcome = "PASS"
elif RESULTS.get("runtime_capabilities", {}).get("blocked"):
    outcome = "BLOCKED"
elif any(v == "FAIL" for v in got.values()):
    outcome = "FAIL"
else:
    outcome = "INCONCLUSIVE"
fin = rmark("overall", stage="overall", verdict=outcome, note=f"{globals().get('SCOPE', INTEGRATION_MODE)}; missing={[k for k, v in got.items() if v != 'PASS'][:6]}")
if outcome == "PASS" and not fin:
    outcome = "INCONCLUSIVE"                          # the final remote verdict is required evidence
scope = globals().get("SCOPE", INTEGRATION_MODE.upper())
label = {"PASS": f"{scope} INTEGRATION ACCEPTED" + (" (isolated staging data; NOT production acceptance)" if scope == "STAGING" else "")}.get(outcome, outcome)
RESULTS["overall"] = {"status": outcome, "reason": label, "attempt": ATTEMPT, "steps": got}
mark("verdict", step="overall", status=outcome, reason=label, steps=got, final_remote=fin.get("code"))
print(json.dumps(got, indent=1), flush=True)
print(f"VERDICT overall: {outcome} | {label}", flush=True)
if outcome != "PASS":
    raise DiagnosticStop(f"overall {outcome}")"""

NB03_EXPORT = r"""# Compact evidence export (run it even after a FAIL / INCONCLUSIVE / BLOCKED stop). ONE redacted archive directly in /kaggle/working (not inside the
# attempt tree, so it is never nested in another archive): markers, verdicts, pip reports, server and stage logs. The public 38 MB wheel bytes and the
# public connector clone are excluded (the wheel's size + SHA-256 and the connector's full SHA are in the markers). Download THIS file only; do not
# zip /kaggle/working/enigma-diag-attempts.
mark("export_begin", results={k: v["status"] for k, v in RESULTS.items()}, wheel_sha256="__SHA__", connector_rev="__REV__")
archive = f"/kaggle/working/{NOTEBOOK}-{ATTEMPT}-evidence.zip"
files, hits = write_redacted_archive(ATTEMPT_DIR, archive)
print("evidence archive:", archive, os.path.getsize(archive), "bytes,", files, "files, redactions:", hits, flush=True)
print(json.dumps({k: {"status": v["status"], "reason": v["reason"]} for k, v in RESULTS.items()}, indent=1), flush=True)"""


def nb_03() -> list[dict]:
    n = "03_enigma_stokastisk_build5_aws_integration"
    base_json = json.dumps(BASELINE, sort_keys=True, separators=(",", ":"))
    baseline = sub(NB03_BASELINE, BASELINE_JSON=repr(base_json), BASELINE_SHA=BASELINE["manifest_sha256"], SHA=WHEEL_SHA256, SIZE=str(WHEEL_SIZE), MODEL=MODEL)
    capability = sub(NB03_CAPABILITY, AWSRT=AWS_RUNTIME_URL, AWSPROD=AWS_RUNTIME_PROD_URL, API=API_URL, DP_STAGING=DATA_PLANES["aws-staging"],
                     DP_PROD=DATA_PLANES["aws-production"], DP_RAILWAY=DATA_PLANES["railway-comparison"])
    full_md = ("## 03-8e. Full connector with one real Playground inference (600 s)\nStart this cell, wait for `stage_full` to begin, then submit ONE prompt:\n\n"
               "* **aws-staging** (isolated staging data): on your computer run `AWS_PROFILE=enigma-deployer python3.13 scripts/aws_staging_runtime.py submit` "
               "in the Enigma repository. It submits a Playground job to the staging runtime, waits for this GPU to complete it and prints the reply "
               "(`STAGING_PLAYGROUND_JOB_PASS`). The Railway web app does NOT show staging data.\n"
               "* **aws-production** / **railway-comparison**: open https://enigma-web-production-8986d34a.up.railway.app/app/runtimes, select the real "
               "runtime, open Playground and submit one prompt; confirm the streamed reply persisted in the UI.\n\n"
               "PASS requires at least one completed job and no failed job.")
    return [
        header("03. Enigma + Stokastisk build 5: AWS runtime integration", "**Prepared; NOT yet executed end to end (no GPU or Playground acceptance "
               "exists for this notebook).** `INTEGRATION_MODE` (first code cell, fixed for the attempt): `aws-staging` (default) = the AWS runtime "
               "endpoint (the same FastAPI app on Lambda) on its ISOLATED staging table: STAGING acceptance only; `aws-production` = the AWS runtime "
               "endpoint on production data: BLOCKED until the DynamoDB + Cognito production cutover (stops before any request or GPU work); "
               "`railway-comparison` = Railway direct, only with `ALLOW_RAILWAY_COMPARISON = True`. In the AWS modes this notebook never contacts "
               "Railway. Order: GPU environment guard; both credential preflights; a durable AWS diagnostic acknowledgement; the historical baseline "
               "manifest (notebooks 01 + 02, digest-verified); the capability gate (HTTP, schema, deployment, durable evidence: four separate results); "
               "real runtime authentication (read-only); a 90 s post-capability survival gate; then immutable build 5, real local Qwen inference, the "
               "reviewed connector in its own supervised subprocess with explicit intervals, offline NVML and vLLM stages, registration, heartbeat, idle "
               "job polling, single and continuous telemetry, the full connector with one real Playground job, build-5-safe shutdown and an OVERALL "
               f"verdict (PASS / FAIL / INCONCLUSIVE / BLOCKED, with its scope).\n\n{SECRETS_MD} **aws-staging:** `ENIGMA_RUNTIME_TOKEN` must hold the "
               "STAGING runtime token (the content of `.enigma-local/ENIGMA_STAGING_RUNTIME_TOKEN.txt`, created by `scripts/aws_staging_runtime.py "
               "provision`); the production token is rejected by the staging endpoint before any GPU work.", n),
        prelude(n),
        code(GPU_HELPERS),
        code(sub(REMOTE, DIAG=DIAG_URL)),
        code(CAPABILITY),
        md("## 03-0. GPU environment guard (local only)"),
        code(NB03_ENV),
        md("## 03-1. Credential preflight and a durable AWS acknowledgement (each credential cell re-runnable on its own)"),
        code('require("environment")\nrefresh_credentials("ENIGMA_DIAG_KEY")\ncheck_credential("ENIGMA_DIAG_KEY", "diag_key_preflight")'),
        code('refresh_credentials("ENIGMA_RUNTIME_TOKEN")\ncheck_credential("ENIGMA_RUNTIME_TOKEN", "runtime_token_preflight")   # format/type only; '
             'the live check is 03-4'),
        code(DIAG_PROBE),
        md("## 03-2. Historical baseline evidence (manifest from the executed notebooks 01 and 02)"),
        code(baseline),
        md("## 03-3. Capability gate (selected endpoint only) · 03-4. real runtime authentication · 03-5. post-capability survival"),
        code(capability),
        code(NB03_IDENTITY),
        code(NB03_SURVIVAL),
        md("## 03-6. Immutable build 5 (exactly the notebook 02 workflow), server, real local inference"),
        code('require("post_capability_survival")\nrcheck("remote_install_begin", "install_begin", stage="install", phase="before")\n'
             + sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, SIZE=str(WHEEL_SIZE), KNOWN=json.dumps(KNOWN_REPLACEMENTS))
             + '\nrcheck("remote_install_end", "install_end", stage="install", phase="after", wheel_build=_build, wheel_sha256=WHEEL_SHA256)'),
        code('require("install")\nrcheck("remote_server_begin", "server_start_begin_remote", stage="server", phase="before")\n' + sub(SERVER, MODEL=MODEL)
             + '\nrcheck("remote_server_started", "server_started_remote", stage="server", phase="after")'),
        code(LOCAL_INFERENCE + '\nrcheck("remote_inference_done", "local_inference_done", stage="inference", phase="after")'),
        md("## 03-7a. Reviewed connector, runtime credential getter, stage helper"),
        code(sub(CONNECTOR_FETCH, REV=ENIGMA_REV)),
        code(TOKEN_GETTER.replace('assert API_URL == ENDPOINTS[RUNTIME_TRANSPORT], "transport changed mid-attempt"     # selected by the capability gate; '
                                  'never switched', 'assert API_URL == MODES[INTEGRATION_MODE]["url"], "transport changed mid-attempt"     # selected by the '
                                  'capability gate; never switched')),
        code(NB03_HELPER),
        md("## 03-7b. Offline stages (no Enigma request during the measured intervals)"),
        code('_ = stage("nvml", duration=120)'),
        code('_ = stage("vllm")'),
        md("## 03-8. Cloud stages (selected endpoint only)"),
        code(NB03_CLOUD_GATE),
        code('_ = stage("register")'),
        code('_ = stage("heartbeat")'),
        code('_ = stage("jobs")'),
        code('_ = stage("upload-once")'),
        code('_ = stage("telemetry")'),
        md(full_md),
        code('_ = stage("full", duration=600, poll_s=15)'),
        md("## 03-9. Build-5-safe shutdown"),
        code(NB03_SHUTDOWN),
        md("## 03-10. Overall acceptance (run it last, also after a stop)"),
        code(NB03_OVERALL),
        md("## Evidence export (one compact, redacted archive)"),
        code(sub(NB03_EXPORT, SHA=WHEEL_SHA256, REV=ENIGMA_REV)),
    ]


BUILDERS = {"01_enigma_kaggle_platform_aws_diagnostics": nb_01, "02_stokastisk_build5_kaggle_t4_validation": nb_02,
            "03_enigma_stokastisk_build5_aws_integration": nb_03}


def render(name: str) -> str:
    nb = {"cells": BUILDERS[name](), "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                                   "language_info": {"name": "python", "version": "3.13"}}, "nbformat": 4, "nbformat_minor": 5}
    for i, c in enumerate(nb["cells"]):
        c["id"] = f"{name[:20]}-{i:02d}"
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in NOTEBOOKS:
        (OUT / f"{name}.ipynb").write_text(render(name))
        print("wrote", OUT / f"{name}.ipynb")
    return 0


if __name__ == "__main__":
    sys.exit(main())
