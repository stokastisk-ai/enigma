"""Generate the three Kaggle notebooks (build 5 only) in docs/kaggle/diagnostics/ (run: python3.13 scripts/build_kaggle_diagnostic_notebooks.py).

Why six notebooks: on 2026-11-26 a "CPU-only" HTTPS test ran inside a GPU session whose Stokastisk vLLM server was already running, so it isolated nothing.
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
EXPERIMENT = "build5" if EXPECT == "gpu" else "no-sdk"   # build 5 is the ONLY supported Stokastisk artifact; recorded in every MARK

ATTEMPT = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
ATTEMPT_DIR = f"/kaggle/working/enigma-diag-attempts/{NOTEBOOK}/{ATTEMPT}"
os.makedirs(ATTEMPT_DIR, exist_ok=False)               # a new directory per attempt: earlier evidence is never overwritten
T0 = time.monotonic()
RESULTS = {}                                           # verdicts of THIS kernel session only (stale outputs can never satisfy a check)


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
    rec = {"attempt": ATTEMPT, "nb": NOTEBOOK, "experiment": EXPERIMENT, "marker": name, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "t": round(time.monotonic() - T0, 2), **ident(), **host(), **fields}
    line = json.dumps(rec, default=str)
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


def observe(step, seconds, every=15.0, sample=None):
    """Survival observation: one MARK every `every` s. PASS only if the full duration elapsed with no cgroup OOM kill and no failed sample check."""
    start, oom0, n, problems = time.monotonic(), host()["cgroup_events"].get("oom_kill"), 0, []
    mark(step + "_begin", seconds=seconds, every=every)
    while time.monotonic() - start < seconds:
        time.sleep(min(every, max(0.0, seconds - (time.monotonic() - start))))
        n += 1
        extra = sample() if sample else {}
        if extra.get("problem"):
            problems.append(extra["problem"])
        mark(step + "_alive", i=n, **extra)
    el, oom1 = time.monotonic() - start, host()["cgroup_events"].get("oom_kill")
    delta = None if oom0 is None or oom1 is None else oom1 - oom0
    ok = el >= seconds - 1.0 and delta in (None, 0) and not problems
    verdict(step, "PASS" if ok else "FAIL", f"observed {el:.0f}/{seconds}s, oom_kill delta {delta}, problems {problems[:3]}", observed_s=round(el, 1),
            oom_kill_delta=delta, samples=n)


def export_evidence():
    """Zip this attempt's directory (only it). Safe to run after a failure."""
    base = f"/kaggle/working/enigma-diag-attempts/{NOTEBOOK}-{ATTEMPT}"
    path = shutil.make_archive(base, "zip", ATTEMPT_DIR)
    print("evidence archive:", path, os.path.getsize(path), "bytes", flush=True)
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
chk = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
import importlib.metadata as md
_dist = md.distribution("stokastisk")
_build = next((l.split(":", 1)[1].strip() for l in (_dist.read_text("WHEEL") or "").splitlines() if l.startswith("Build:")), None)
mark("installed_identity", wheel_build=_build, version=_dist.version, changed=changed, removed=removed, pip_check_rc=chk.returncode)
base_same = all(before.get(b) == after.get(b) for b in BASE)
ok = r.returncode == 0 and base_same and changed == replaced and not removed and _build == "5" and _dist.version == "0.1.0"
verdict("install", "PASS" if ok else "FAIL", f"rc={r.returncode}, base unchanged={base_same}, changed={changed}, installed build={_build}",
        installed_build=_build, changed=changed)
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

REMOTE = r'''# Independent AWS evidence (Lambda Function URL -> DynamoDB + CloudWatch), NOT Railway and NOT the Enigma database. Optional Kaggle Secret
# ENIGMA_DIAG_KEY (a narrow, revocable diagnostic key; never an AWS credential). rmark() = mark() locally + one bounded event to AWS; it returns True only
# when AWS acknowledged the durable write. Nothing secret, no environment variable and no request body is ever sent.
import urllib.request
DIAG_URL = "__DIAG__"
_DIAG = {"key": None, "seq": 0}
_REMOTE_FIELDS = {"stage", "phase", "http", "latency_s", "target", "exc", "exit_code", "signal", "last_ok", "verdict", "note", "gpu_count",
                  "gpu_mem_used_mib", "gpu_util_pct", "wheel_build", "wheel_sha256", "artifact"}


def _diag_key():
    if _DIAG["key"] is None:
        try:
            from kaggle_secrets import UserSecretsClient
            k = UserSecretsClient().get_secret("ENIGMA_DIAG_KEY").strip()
            _DIAG["key"] = k if re.fullmatch(r"edx_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", k) else ""
        except Exception:
            _DIAG["key"] = ""
    return _DIAG["key"]


def rmark(name, **fields):
    rec = mark(name, **fields)
    key = _diag_key()
    if not key:
        mark("remote_skipped", reason="no ENIGMA_DIAG_KEY Kaggle Secret")
        return False
    _DIAG["seq"] += 1
    ev = {"attempt": ATTEMPT, "nb": NOTEBOOK, "seq": _DIAG["seq"], "marker": name, "utc": rec["utc"], "t": rec["t"], "experiment": EXPERIMENT,
          "pid": rec["pid"], "pgid": rec["pgid"], "sid": rec["sid"], "mem_available_mib": rec.get("memavailable_mib"),
          "cgroup_current_mib": rec.get("cgroup_current_mib"), "cgroup_max_mib": rec.get("cgroup_max_mib"),
          "oom_kill": rec.get("cgroup_events", {}).get("oom_kill"), "vm_uptime_s": rec.get("vm_uptime_s")}
    ev.update({k: v for k, v in fields.items() if k in _REMOTE_FIELDS and v is not None})
    ok = False
    try:
        req = urllib.request.Request(DIAG_URL, data=json.dumps({"events": [ev]}).encode(), method="POST",
                                     headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read(4096))
            ok = r.status == 200 and body.get("stored", 0) + body.get("duplicates", 0) == 1
    except Exception as e:
        mark("remote_failed", seq=_DIAG["seq"], error=type(e).__name__)
    print(f"REMOTE {'ACK' if ok else 'NOT-ACKNOWLEDGED'} {name} seq={_DIAG['seq']}", flush=True)
    return ok
'''

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

TOKEN_GETTER = r'''# Runtime credential: read from Kaggle Secrets only when a stage needs it; format-checked; never printed, stored, or put in os.environ.
API_URL = "__API__"
os.environ.pop("ENIGMA_RUNTIME_TOKEN", None)


def token_getter():
    from kaggle_secrets import UserSecretsClient
    t = UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN").strip()
    if not re.fullmatch(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", t):
        raise ValueError("unexpected runtime credential format; check the Kaggle Secret")
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


def prelude(name: str) -> dict:
    return code(sub(PRELUDE, NOTEBOOK=name, EXPECT=NOTEBOOKS[name], NO_INPUTS=str(name in NO_INPUTS_NOTEBOOKS)))


def header(title: str, body: str, name: str) -> dict:
    acc = "**Accelerator: None**" if NOTEBOOKS[name] == "cpu" else "**Accelerator: GPU T4 x2**"
    return md(f"# {title}\n\n{acc} · fresh Kaggle session · Python 3.13 · generated by `scripts/build_kaggle_diagnostic_notebooks.py`\n\n{body}\n\n{DURABILITY}\n\n"
              "Run the cells top to bottom (or *Run all*). Every step ends with `VERDICT <step>: PASS | FAIL | INCONCLUSIVE`; anything but PASS raises and stops "
              "the run. Then run the final **evidence export** cell manually.")


# ------------------------------------------------------------------------------------------------------------------------------------- the three notebooks
def nb_01() -> list[dict]:
    n = "01_enigma_kaggle_platform_aws_diagnostics"
    controls = sub(r"""require("idle_10min")
import urllib.request, urllib.error
def get(name, url):
    mark("request_begin", target=name)
    t = time.monotonic(); row = {"target": name}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Enigma-Kaggle-Diagnostic/4.0", "Accept": "*/*", "Connection": "close"})
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
verdict("controls_https", "PASS" if ok else "INCONCLUSIVE", f"{[(r['target'], r.get('http'), r.get('latency_s')) for r in rows]}")""")
    aws = sub(r"""require("controls_survival_120s")
first = rmark("aws_first_event", stage="aws", phase="after", note="first independent AWS event")
if not first:
    verdict("aws_endpoint", "INCONCLUSIVE", "AWS did not acknowledge (attach the ENIGMA_DIAG_KEY Kaggle Secret, Internet ON)")
verdict("aws_endpoint", "PASS", "AWS acknowledged a durable diagnostic write")""")
    railway = sub(r"""require("aws_survival_120s")
import urllib.request, urllib.error
if not rmark("before_railway", stage="railway", phase="before", target="enigma_health"):
    verdict("railway_marker", "INCONCLUSIVE", "AWS did not acknowledge the before-marker: not sending the Railway request")
t = time.monotonic(); row = {}
try:
    req = urllib.request.Request("__API__/api/v1/health", headers={"User-Agent": "Enigma-Kaggle-Diagnostic/4.0", "Accept": "application/json", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=60) as r:
        row["http"] = r.status; r.read(4096)
except urllib.error.HTTPError as e:
    row["http"] = e.code
except Exception as e:
    row["exc"] = type(e).__name__
row["latency_s"] = round(time.monotonic() - t, 3)
rmark("after_railway", stage="railway", phase="after", target="enigma_health", **row)
verdict("railway_health", "PASS" if row.get("http") == 200 else "FAIL", f"{row}")""", API=API_URL)
    return [
        header("01. Kaggle platform + independent AWS networking diagnostics", "A troubleshooting control only: **no Stokastisk, no CUDA, no LLM, no Enigma "
               "connector, no runtime token, no Git clone.** It separates four things that earlier notebooks ran in the same second: (1) idle platform "
               "survival, (2) ordinary public HTTPS, (3) the independent AWS diagnostic endpoint, (4) the Railway FastAPI health endpoint, with a "
               "survival window after each. Attach the Kaggle Secret `ENIGMA_DIAG_KEY` (a narrow diagnostic key, never an AWS credential) and NO inputs.\n\n"
               "**Reading the result:** the AWS table keeps the last acknowledged marker even if the VM disappears. Death during `controls_survival` = "
               "ordinary HTTPS is enough; during `aws_survival` = any third-party endpoint; only during `railway_survival` = correlated with the Railway "
               "request specifically. Correlation is not a cause: only Kaggle's platform logs can explain a status-code-44 termination.", n),
        prelude(n),
        code(sub(REMOTE, DIAG=DIAG_URL)),
        md("## 01a. Idle survival: 10 minutes, no network at all"),
        code('observe("idle_10min", 600, every=15)'),
        md("## 01b. Ordinary public HTTPS controls, then 2 minutes of survival"),
        code(controls),
        code('require("controls_https")\nobserve("controls_survival_120s", 120, every=15)'),
        md("## 01c. Independent AWS diagnostic endpoint (tested BEFORE relying on it), then 2 minutes of survival with a marker every 30 s"),
        code(aws),
        code('require("aws_endpoint")\nobserve("aws_survival_120s", 120, every=30, sample=lambda: {"problem": None if rmark("aws_alive", stage="aws_survival") else "aws not acknowledged"})'),
        md("## 01d. Railway FastAPI health (AWS marker persisted BEFORE the request), then 2 minutes of survival with a marker every 15 s"),
        code(railway),
        code('require("railway_health")\nobserve("railway_survival_120s", 120, every=15, sample=lambda: {"problem": None if rmark("railway_alive", stage="railway_survival") else "aws not acknowledged"})\nrmark("final", verdict="PASS", note="all four comparisons survived")'),
        md("## Evidence export (local; the AWS record is the durable copy)"),
        code(EXPORT),
    ]


def nb_02() -> list[dict]:
    n = "02_stokastisk_build5_kaggle_t4_validation"
    env = r"""import importlib.metadata as md
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
        header("02. Stokastisk build 5: standalone Kaggle 2×T4 validation", f"Immutable `stokastisk-0.1.0-5` only (SHA-256 verified before install). {MODEL}, "
               "FP16, TRITON_ATTN, TP=1, PP=1, 2048 tokens, gpu_memory_utilization 0.60. **No Enigma connection, no runtime token.** The optional "
               "`ENIGMA_DIAG_KEY` secret only sends markers to the independent AWS diagnostic endpoint.", n),
        prelude(n),
        code(GPU_HELPERS),
        code(sub(REMOTE, DIAG=DIAG_URL)),
        md("## 02a. Environment (Python 3.13, 2×T4, Kaggle's torch / triton / transformers)"),
        code(env),
        md("## 02b. Install the immutable build-5 wheel (size + SHA-256 verified first; the pydantic-settings conflict must be acknowledged)"),
        code('require("environment")\nrmark("install_begin", stage="install")\n' + sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, SIZE=str(WHEEL_SIZE), KNOWN=json.dumps(KNOWN_REPLACEMENTS))
             + '\nrmark("install_end", stage="install", wheel_build=_build, wheel_sha256=WHEEL_SHA256)'),
        md("## 02c. Start the server (loopback `no_proxy` set first)"),
        code(sub(SERVER, MODEL=MODEL) + '\nrmark("server_started", stage="server")'),
        md("## 02d. Real local inference and native vLLM metrics"),
        code(LOCAL_INFERENCE + '\nrmark("local_inference_done", stage="inference")'),
        md("## 02e. Passive server observation: 10 minutes"),
        code(passive + '\nrmark("server_observed_10min", stage="observe", verdict="PASS")'),
        md("## 02f. Build-5-safe shutdown"),
        code(SAFE_STOP + 'rmark("final", verdict=RESULTS["server_shutdown"]["status"])'),
        md("## Evidence export"),
        code(EXPORT),
    ]


def nb_03() -> list[dict]:
    n = "03_enigma_stokastisk_build5_aws_integration"
    gate = r"""# Gate: run only after notebooks 01 and 02 PASSED in their own sessions, and after the AWS-backed Enigma production is live. Edit deliberately.
BASELINES_PASSED = False
assert BASELINES_PASSED, "run notebooks 01 and 02 first"
verdict("baselines_confirmed", "PASS", "operator confirmed 01 and 02 PASS")"""
    helper = r"""from enigma_connector.stage_runner import run_stage, MarkerLog
from enigma_connector import serverctl
INTERVAL_ARGS = ("--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0")      # explicit finite intervals, as in every verified run
ORDER = ("nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full")


def stage(name, **kw):
    # One SUPERVISED connector subprocess per stage (never in this kernel). Prerequisites must have PASSED in THIS session.
    require("connector_fetch", "local_inference", *[f"stage_{p}" for p in ORDER[:ORDER.index(name)]])
    assert server.health(), "local vLLM unhealthy"
    cloud = name not in ("nvml", "vllm")
    rmark(f"before_stage_{name}", stage=name, phase="before")
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
    rmark(f"after_stage_{name}", stage=name, phase="after", exit_code=res["exit"]["returncode"], signal=res["exit"].get("killed_by_signal"), verdict=status)
    verdict(f"stage_{name}", status, f"rc={res['exit']['returncode']} failed={failed}", run_id=(res.get("summary") or {}).get("run_id"), stats=stats)
    return res"""
    cloud_gate = r"""# Cloud gate: confirm in Enigma (AWS-backed production) that the runtime is REAL (Mock off) and that no Playground job is queued. Edit deliberately.
ALLOW_CLOUD_CONNECTION = False
assert ALLOW_CLOUD_CONNECTION, "cloud gate closed"
verdict("cloud_gate", "PASS", "operator opened the cloud gate")"""
    shutdown = r"""# Shutdown with the build-5-safe orchestration from the reviewed connector (never patches the installed wheel).
rep = serverctl.safe_stop(server)
mark("server_safe_stop", report=rep, **gpu_query())
rmark("final", verdict="PASS" if rep.get("action") == "package_stop" else "FAIL", note=str(rep.get("action")))
verdict("server_shutdown", "PASS" if rep.get("action") == "package_stop" and rep.get("report", {}).get("group_gone") else "FAIL", f"{rep}")"""
    return [
        header("03. Enigma + Stokastisk build 5 integration (AWS-backed Enigma)", "**Prepared, not GPU-verified.** Immutable build 5, the reviewed connector "
               "in its own supervised subprocess, explicit intervals, independent AWS markers before and after every cloud stage. Stages stop at the "
               "first failure: offline NVML, offline vLLM metrics, registration, heartbeat, idle job polling, single telemetry upload, continuous "
               "telemetry, full connector with one real Playground job (required), then build-5-safe shutdown. Kaggle Secrets: "
               "`ENIGMA_RUNTIME_TOKEN` (runtime key) and `ENIGMA_DIAG_KEY` (diagnostic key). Never an AWS credential.", n),
        prelude(n),
        code(GPU_HELPERS),
        code(sub(REMOTE, DIAG=DIAG_URL)),
        code(gate),
        md("## 03a. Immutable build 5, server, local inference"),
        code('require("baselines_confirmed")\n' + sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, SIZE=str(WHEEL_SIZE), KNOWN=json.dumps(KNOWN_REPLACEMENTS))),
        code(sub(SERVER, MODEL=MODEL)),
        code(LOCAL_INFERENCE),
        md("## 03b. Reviewed connector, credentials, stage helper"),
        code(sub(CONNECTOR_FETCH, REV=ENIGMA_REV)),
        code(sub(TOKEN_GETTER, API=API_URL)),
        code(helper),
        md("## 03c. Offline stages"),
        code('_ = stage("nvml", duration=120)'),
        code('_ = stage("vllm")'),
        md("## 03d. Cloud stages"),
        code(cloud_gate),
        code('require("cloud_gate")\n_ = stage("register")'),
        code('_ = stage("heartbeat")'),
        code('_ = stage("jobs")'),
        code('_ = stage("upload-once")'),
        code('_ = stage("telemetry")'),
        md("## 03e. Full connector with one real Playground inference (600 s)\nStart the cell, then open "
           "https://enigma-web-production-8986d34a.up.railway.app/app/runtimes, select the real runtime, open Playground and submit one prompt. "
           "PASS requires at least one completed job and no failed job; confirm the reply persisted in the UI."),
        code('_ = stage("full", duration=600, poll_s=15)'),
        md("## 03f. Build-5-safe shutdown"),
        code(shutdown),
        md("## Evidence export"),
        code(EXPORT),
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
