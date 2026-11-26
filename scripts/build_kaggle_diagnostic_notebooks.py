"""Generate the six independent Kaggle diagnostic notebooks in docs/kaggle/diagnostics/ (run: python3.13 scripts/build_kaggle_diagnostic_notebooks.py).

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

ENIGMA_REV = "c5e5e8dedcaa2582f2a55a5918ab3ad6f03b3a49"           # reviewed connector revision (branch diag/kaggle-isolation-2026-11-26)
API_URL = "https://enigma-api-production-c8cffe9d.up.railway.app"
WHEEL = "https://huggingface.co/waqasm86/stokastisk/resolve/main/wheels/stokastisk-0.1.0-5-cp313-cp313-linux_x86_64.whl"
WHEEL_SHA256 = "fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
# The only replacement the build-5 install made on Kaggle (2026-11-25 and 2026-11-26): the wheel pins pydantic-settings==2.16.2 exactly.
KNOWN_REPLACEMENTS = {"pydantic-settings": ["2.16.3", "2.16.2"]}

NOTEBOOKS = {
    "enigma_kaggle_cpu_platform_baseline": "cpu",
    "enigma_kaggle_cpu_connector_isolation": "cpu",
    "stokastisk_kaggle_gpu_platform_baseline": "gpu",
    "stokastisk_kaggle_vllm_stability": "gpu",
    "enigma_kaggle_registration_without_vllm": "gpu",
    "enigma_stokastisk_qwen25_05b_final_integration": "gpu",
}

DURABILITY = ("**Evidence durability.** Every step prints flushed `MARK` and `VERDICT` lines and appends them to an fsynced `markers.jsonl` in a new, "
              "never-reused attempt directory. After a complete platform termination the VM's files may be gone (on 2026-11-26 a new session had to "
              "re-clone everything), but cell output printed before the failure was kept in the saved notebook. **Missing files do not prove that no crash "
              "occurred.** After a fatal session, copy the last `MARK` lines (UTC time, `t` = seconds since the prelude) into the incident report.")

PRELUDE = r'''# P0. Prelude: environment guard, attempt id, markers and verdicts. Standard library only: no network, no GPU library is imported.
import os, sys, json, time, uuid, platform, shutil, subprocess, re
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
    rec = {"attempt": ATTEMPT, "nb": NOTEBOOK, "marker": name, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
mark("prelude", python=platform.python_version(), expect=EXPECT, gpu_device=HAS_GPU_DEVICE, os=_os.get("PRETTY_NAME", "").strip('"'), kaggle=_kaggle)
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

INSTALL = r'''# Controlled build-5 installation. First a dry-run plan; only the known, reported replacement is accepted; then the real install; then a diff and pip check.
WHEEL = "__WHEEL__"
SHA256 = "__SHA__"
KNOWN_REPLACEMENTS = __KNOWN__          # the wheel pins pydantic-settings==2.16.2; Kaggle's image ships 2.16.3 (CONFLICT with "preserve every version")
SPEC = f"stokastisk @ {WHEEL}#sha256={SHA256}"
BASE = ("torch", "triton", "transformers", "pydantic", "pydantic-core", "fastapi", "starlette", "numpy")
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()                                     # noqa: E731


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
mark("install_dry_run_begin", packages_before=len(before))
r = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--report", report, "--index-url", "https://pypi.org/simple", SPEC],
                   capture_output=True, text=True)
if r.returncode != 0:
    verdict("install_plan", "FAIL", f"pip dry-run failed rc={r.returncode}", stderr_tail=r.stderr[-1500:])
plan = {norm(i["metadata"]["name"]): i["metadata"]["version"] for i in json.load(open(report))["install"]}
replaced = {k: [before[k], v] for k, v in plan.items() if k in before and before[k] != v}
added = sorted(k for k in plan if k not in before)
mark("install_plan", would_install=len(plan), added=added, replaced=replaced, dependents={k: dependents(k) for k in replaced})
print("DEPENDENCY_VERSION_PRESERVATION:", "VERIFIED" if not replaced else f"CONFLICT {replaced}", flush=True)
unexpected = {k: v for k, v in replaced.items() if KNOWN_REPLACEMENTS.get(k) != v}
if unexpected:
    verdict("install_plan", "INCONCLUSIVE", f"pip would replace unexpected preinstalled packages {unexpected}: not installing", replaced=replaced)
verdict("install_plan", "PASS", f"{len(plan)} to install, replacements limited to the known set {replaced}", replaced=replaced, added=added)

mark("install_begin")
r = subprocess.run([sys.executable, "-m", "pip", "install", "--index-url", "https://pypi.org/simple", SPEC], capture_output=True, text=True)
open(f"{ATTEMPT_DIR}/pip-install.log", "w").write(r.stdout[-200000:] + "\n--- stderr ---\n" + r.stderr[-50000:])
after = inventory()
json.dump(after, open(f"{ATTEMPT_DIR}/packages-after.json", "w"), indent=1, sort_keys=True)
changed = {k: [before[k], after[k]] for k in before if k in after and before[k] != after[k]}
removed = sorted(k for k in before if k not in after)
new = sorted(k for k in after if k not in before)
chk = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
mark("install_end", rc=r.returncode, changed=changed, removed=removed, new=new, pip_check_rc=chk.returncode, pip_check=chk.stdout.strip().splitlines()[:40])
base_same = all(before.get(b) == after.get(b) for b in BASE)
ok = r.returncode == 0 and base_same and changed == replaced and not removed and "stokastisk" in after
verdict("install", "PASS" if ok else "FAIL", f"rc={r.returncode}, base unchanged={base_same}, changed={changed}, removed={removed}",
        changed=changed, new_count=len(new), pip_check_rc=chk.returncode)
'''

SERVER = r'''# Start the Stokastisk server with the previously successful settings (TP=1, PP=1, 2048 tokens, gpu_memory_utilization 0.60, Triton attention, fp16, eager).
require("install")
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
    return code(sub(PRELUDE, NOTEBOOK=name, EXPECT=NOTEBOOKS[name]))


def header(title: str, body: str, name: str) -> dict:
    acc = "**Accelerator: None**" if NOTEBOOKS[name] == "cpu" else "**Accelerator: GPU T4 x2**"
    return md(f"# {title}\n\n{acc} · fresh Kaggle session · Python 3.13 · generated by `scripts/build_kaggle_diagnostic_notebooks.py`\n\n{body}\n\n{DURABILITY}\n\n"
              "Run the cells top to bottom (or *Run all*). Every step ends with `VERDICT <step>: PASS | FAIL | INCONCLUSIVE`; anything but PASS raises and stops "
              "the run. Then run the final **evidence export** cell manually.")


# ------------------------------------------------------------------------------------------------------------------------------------- the six notebooks
def nb_a() -> list[dict]:
    n = "enigma_kaggle_cpu_platform_baseline"
    return [
        header("A. Kaggle CPU-only platform baseline", "No Stokastisk, no CUDA, PyTorch, NVML or vLLM import, no Enigma connector, no runtime token, no Git clone. "
               "Question: does a plain CPU session survive, and does ordinary HTTPS work, with NONE of our software present?", n),
        prelude(n),
        md("## A1. Static inventory (no network)"),
        code('import importlib.metadata as md\nmark("A1_inventory", cpus=os.cpu_count(), packages=sum(1 for _ in md.distributions()), kernel=platform.release())\n'
             'verdict("A1_inventory", "PASS", "recorded")'),
        md("## A2. Idle survival control: 10 minutes, nothing running\nIf the session dies here, the cause is Kaggle-side: none of Enigma, Stokastisk, vLLM or CUDA is present. "
           "**Do not continue to the network tests in that case.**"),
        code('require("A1_inventory")\nobserve("A2_idle_10min", 600, every=15)'),
        md("## A3. Unauthenticated HTTPS: public controls and the Enigma health endpoint\n6 rounds, 10 s apart, 20 s timeout, at most 64 KiB read per response. "
           "A failing Enigma request while the controls succeed is an Enigma-side network FAIL; failing controls too = INCONCLUSIVE (Kaggle internet). "
           "A session that dies here leaves no verdict: its last MARK says which request was last completed."),
        code(sub(r'''require("A2_idle_10min")
import urllib.request, urllib.error
TARGETS = {"control_pypi": "https://pypi.org/simple/pip/", "control_gstatic": "https://www.gstatic.com/generate_204",
           "enigma_health": "__API__/api/v1/health"}
rows = []
for rnd in range(6):
    for name, url in TARGETS.items():
        t = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Enigma-Kaggle-Diagnostic/2.0", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                n_bytes = len(resp.read(65536))
                rows.append({"round": rnd + 1, "target": name, "http": resp.status, "bytes": n_bytes, "s": round(time.monotonic() - t, 3)})
        except urllib.error.HTTPError as e:
            rows.append({"round": rnd + 1, "target": name, "http": e.code, "s": round(time.monotonic() - t, 3)})
        except Exception as e:
            rows.append({"round": rnd + 1, "target": name, "error": type(e).__name__, "s": round(time.monotonic() - t, 3)})
        mark("A3_request", **rows[-1])
    if rnd < 5:
        mark("A3_sleep_begin", round=rnd + 1)
        time.sleep(10)
ok = lambda r: r.get("http") in (200, 204)                                    # noqa: E731
controls = [r for r in rows if r["target"].startswith("control")]
enigma = [r for r in rows if r["target"] == "enigma_health"]
if not all(map(ok, controls)):
    verdict("A3_https", "INCONCLUSIVE", f"public controls failed {sum(not ok(r) for r in controls)}/{len(controls)}: check that Internet is ON")
verdict("A3_https", "PASS" if all(map(ok, enigma)) else "FAIL", f"enigma {sum(map(ok, enigma))}/{len(enigma)} ok, controls all ok",
        enigma_ok=sum(map(ok, enigma)), enigma_total=len(enigma))''', API=API_URL)),
        md("## A4. Post-network idle survival: 5 minutes\nThe earlier fatal sessions ended seconds after network activity; this checks the same window with no other software."),
        code('require("A3_https")\nobserve("A4_post_network_idle_5min", 300, every=15)'),
        md("## A5. Optional, after A1–A4 PASS: dependency plan on the CPU image (dry run, installs nothing)\n"
           "Answers whether the build-5 install would change the same packages on a CPU image. `pip install --dry-run` resolves only; nothing is installed."),
        code(sub(r'''require("A4_post_network_idle_5min")
report = f"{ATTEMPT_DIR}/pip-dry-run-report.json"
spec = "stokastisk @ __WHEEL__#sha256=__SHA__"
r = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--report", report, "--index-url", "https://pypi.org/simple", spec],
                   capture_output=True, text=True)
if r.returncode != 0:
    mark("A5_dry_run", rc=r.returncode, stderr_tail=r.stderr[-1500:])
    verdict("A5_dry_run", "INCONCLUSIVE", "the CPU image cannot resolve the build-5 requirements (e.g. torch==2.11.0+cu130 not installed/available): see stderr_tail")
import importlib.metadata as md
have = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version for d in md.distributions() if d.metadata["Name"]}
plan = {re.sub(r"[-_.]+", "-", i["metadata"]["name"]).lower(): i["metadata"]["version"] for i in json.load(open(report))["install"]}
replaced = {k: [have[k], v] for k, v in plan.items() if k in have and have[k] != v}
mark("A5_dry_run", would_install=len(plan), replaced=replaced, added=sorted(k for k in plan if k not in have))
verdict("A5_dry_run", "PASS", f"{len(plan)} would be installed; would replace {replaced}", replaced=replaced)''', WHEEL=WHEEL, SHA=WHEEL_SHA256)),
        md("## Evidence export"),
        code(EXPORT),
    ]


def nb_b() -> list[dict]:
    n = "enigma_kaggle_cpu_connector_isolation"
    return [
        header("B. CPU-only: Kaggle Secret and supervised-subprocess isolation", "No Stokastisk, no vLLM, no GPU. Fetches the exact reviewed Enigma connector "
               "revision and tests, one at a time: secret retrieval (no Enigma request), a child that exits by itself, a child that needs graceful shutdown, "
               "sentinel survival, parent PID/PGID/SID preservation, checkpoint writes and ZIP export. No runtime is registered.", n),
        prelude(n),
        md("## B0. Idle survival control: 5 minutes"),
        code('observe("B0_idle_5min", 300, every=15)'),
        md("## B1. Fetch the reviewed connector"),
        code('require("B0_idle_5min")\n' + sub(CONNECTOR_FETCH, REV=ENIGMA_REV)),
        md("## B2. Kaggle Secret retrieval only\n`UserSecretsClient` contacts Kaggle's own secret service; nothing is sent to Enigma. Only presence and format are recorded."),
        code(r'''require("connector_fetch")
mark("B2_before_secret_retrieval")
try:
    from kaggle_secrets import UserSecretsClient
    _v = UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN")
except Exception as e:
    mark("B2_after_secret_retrieval", ok=False, error=type(e).__name__)
    verdict("B2_secret", "INCONCLUSIVE", f"secret not readable ({type(e).__name__}): attach ENIGMA_RUNTIME_TOKEN under Add-ons > Secrets")
_fmt = bool(re.fullmatch(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", (_v or "").strip()))
del _v
mark("B2_after_secret_retrieval", ok=True, format_ok=_fmt)
verdict("B2_secret", "PASS" if _fmt else "FAIL", "present and well-formed" if _fmt else "present but malformed")'''),
        md("## B3. Supervised child that exits by itself, B4. child that needs graceful SIGTERM\nA sentinel in its own session must survive; this kernel's "
           "PID/PGID/SID must not change; the self-exiting child must receive **no** signal."),
        code(r'''require("B2_secret")
me = ident()
sentinel = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.5)"], start_new_session=True)
child_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}             # minimal environment: no Kaggle or Enigma variables are passed
p1 = ConnectorProcess([], child_env, f"{ATTEMPT_DIR}/b3", command=[sys.executable, "-c", "print('exit by itself')"])
mark("B3_before_spawn"); pid1 = p1.start(); mark("B3_after_spawn", child=pid1)
rc1 = p1.wait(30); mark("B3_child_exit", rc=rc1)
mark("B3_before_cleanup"); r1 = p1.stop(grace_s=2); mark("B3_after_cleanup", rc=r1["returncode"], sent=r1["signals_sent"], refused=r1["signals_refused"])
ok1 = r1["returncode"] == 0 and r1["signals_sent"] == [] and sentinel.poll() is None and ident() == me
verdict("B3_self_exit", "PASS" if ok1 else "FAIL", f"rc={r1['returncode']} signals={r1['signals_sent']} sentinel_alive={sentinel.poll() is None}")

script = "import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\nwhile True: time.sleep(0.1)"
p2 = ConnectorProcess([], child_env, f"{ATTEMPT_DIR}/b4", command=[sys.executable, "-c", script])
mark("B4_before_spawn"); pid2 = p2.start(); mark("B4_after_spawn", child=pid2)
time.sleep(1)
mark("B4_before_cleanup"); r2 = p2.stop(grace_s=10, kill_after_s=5); mark("B4_after_cleanup", rc=r2["returncode"], sent=r2["signals_sent"], escalated=r2["escalated_to_sigkill"])
ok2 = r2["returncode"] == 0 and r2["signals_sent"] == ["SIGTERM"] and not r2["escalated_to_sigkill"] and sentinel.poll() is None and ident() == me
sentinel.kill(); sentinel.wait(5)
verdict("B4_graceful", "PASS" if ok2 else "FAIL", f"rc={r2['returncode']} signals={r2['signals_sent']} escalated={r2['escalated_to_sigkill']}")'''),
        md("## B5. Checkpoint write and B6. ZIP archive"),
        code(r'''require("B4_graceful")
mark("B5_before_checkpoint")
data = {"attempt": ATTEMPT, "results": {k: v["status"] for k, v in RESULTS.items()}, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
tmp, final = f"{ATTEMPT_DIR}/checkpoint.tmp", f"{ATTEMPT_DIR}/checkpoint.json"
with open(tmp, "w") as fh:
    json.dump(data, fh); fh.flush(); os.fsync(fh.fileno())
os.replace(tmp, final)
mark("B5_after_checkpoint")
verdict("B5_checkpoint", "PASS" if json.load(open(final)) == data else "FAIL", "atomic write and read-back")
import zipfile
mark("B6_before_archive")
path = export_evidence()
with zipfile.ZipFile(path) as z:
    good = z.testzip() is None and "markers.jsonl" in z.namelist() and "checkpoint.json" in z.namelist()
mark("B6_after_archive", archive=path, bytes=os.path.getsize(path))
verdict("B6_archive", "PASS" if good else "FAIL", f"{path}")'''),
        md("## Evidence export"),
        code(EXPORT),
    ]


def nb_c() -> list[dict]:
    n = "stokastisk_kaggle_gpu_platform_baseline"
    return [
        header("C. GPU platform baseline: before and after the build-5 install", "No Enigma traffic, no model, no server. GPU inventory via `nvidia-smi` in a "
               "child process only. Question: does a 2×T4 session survive idle, and does it still survive after the build-5 installation?", n),
        prelude(n),
        code(GPU_HELPERS),
        md("## C1. GPU inventory and preinstalled package inventory"),
        code(r'''g = gpu_query()
pkgs = inventory()
json.dump(pkgs, open(f"{ATTEMPT_DIR}/packages-preinstalled.json", "w"), indent=1, sort_keys=True)
mark("C1_inventory", packages=len(pkgs), torch=pkgs.get("torch"), triton=pkgs.get("triton"), transformers=pkgs.get("transformers"),
     pydantic_settings=pkgs.get("pydantic-settings"), **g)
names = [x.get("name") for x in g.get("gpus", [])]
verdict("C1_inventory", "PASS" if names == ["Tesla T4", "Tesla T4"] else "INCONCLUSIVE", f"GPUs {names}")'''),
        md("## C2. Idle survival before installing anything: 10 minutes"),
        code('require("C1_inventory")\nobserve("C2_gpu_idle_10min", 600, every=15, sample=lambda: gpu_query() if int(time.monotonic()) % 60 < 15 else {})'),
        md("## C3. Controlled build-5 installation\nDry-run plan first; only the reported `pydantic-settings` 2.16.3 → 2.16.2 replacement is accepted. Any other change stops before installing."),
        code('require("C2_gpu_idle_10min")\n' + sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, KNOWN=json.dumps(KNOWN_REPLACEMENTS))),
        md("## C4. Post-install survival: 10 minutes, no model, no server"),
        code('require("install")\nobserve("C4_post_install_idle_10min", 600, every=15, sample=lambda: gpu_query() if int(time.monotonic()) % 60 < 15 else {})'),
        md("## Evidence export"),
        code(EXPORT),
    ]


def nb_d() -> list[dict]:
    n = "stokastisk_kaggle_vllm_stability"
    passive = r'''require("local_inference")
def passive_sample():
    procs = group_procs(server.pid)
    states = sorted({p["state"] for p in procs})
    problem = None if server.running and procs and "Z" not in states else f"server_running={server.running} states={states}"
    return {"server_running": server.running, "group_size": len(procs), "states": states, "rss_mib": round(sum(p["rss_mib"] for p in procs), 1),
            "problem": problem, **(gpu_query() if int(time.monotonic()) % 60 < 15 else {})}
observe("D3_passive_idle_10min", 600, every=15, sample=passive_sample)       # no HTTP to the server at all during this window
mark("D3_server_log_tail", log_tail=server.log_tail(20))'''
    active = r'''require("D3_passive_idle_10min")
def active_sample():
    ok = server.health(timeout=5)
    m = serving.fetch_metrics(server.base_url) if int(time.monotonic()) % 30 < 5 else {}
    return {"health": ok, "metric_keys": len(m), "server_running": server.running, "problem": None if ok and server.running else "health failed"}
observe("D4_health_polling_5min", 300, every=5, sample=active_sample)
mark("D4_server_log_tail", log_tail=server.log_tail(20))'''
    stop = r'''# Optional, after the evidence export: graceful shutdown report (SIGINT to the server's own group only).
rc = server.stop()
mark("D5_server_stopped", rc=rc, report=server.shutdown_report, **gpu_query())
verdict("D5_stop", "PASS" if server.shutdown_report.get("group_gone") else "FAIL", f"rc={rc} {server.shutdown_report}")'''
    return [
        header("D. Stokastisk standalone vLLM server stability", f"Build-5 wheel, vLLM 0.25.1, {MODEL}, TP=1, PP=1, 2048 tokens, gpu_memory_utilization 0.60, "
               "Triton attention, fp16, eager. **No Enigma connector, no credential, no Railway request.** Passive observation (no HTTP to the server) and "
               "health polling are separate steps. Do not raise gpu_memory_utilization to chase a crash.", n),
        prelude(n),
        code(GPU_HELPERS),
        md("## D0. Install build 5 (same controlled procedure as notebook C)"),
        code(sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, KNOWN=json.dumps(KNOWN_REPLACEMENTS))),
        md("## D1. Start the server"),
        code(sub(SERVER, MODEL=MODEL)),
        md("## D2. Verified local inference"),
        code(LOCAL_INFERENCE),
        md("## D3. Passive observation: 10 minutes\nServer process group states, GPU memory/utilization (`nvidia-smi` each minute), RAM, cgroup, /dev/shm, disk. No requests."),
        code(passive),
        md("## D4. Local health polling: 5 minutes"),
        code(active),
        md("## Evidence export"),
        code(EXPORT),
        md("## D5. Optional graceful stop"),
        code(stop),
    ]


def nb_e() -> list[dict]:
    n = "enigma_kaggle_registration_without_vllm"
    unsupported = r'''# E3. Registration WITHOUT a running model: NOT SENT, reported as unsupported.
# The registration contract (RegisterRequest) requires served_model (min length 1) and opens a new run; with no server the connector would send
# served_model="unknown" (overwriting the real runtime record), claim capabilities.vllm_metrics=true because a sampler object exists, and make the
# runtime look online in the Playground with nothing to serve. That cannot be represented honestly, so no request is made.
require("E2_nvml_offline")
mark("E3_registration_not_sent", reason="contract has no hardware-only registration")
verdict("E3_registration_without_vllm", "INCONCLUSIVE", "UNSUPPORTED: hardware-only registration cannot be represented honestly; no request was sent")'''
    nvml = r'''require("E1_gpu_idle_5min")
from enigma_connector.stage_runner import run_stage, MarkerLog
# pynvml (nvidia-ml-py) is needed by the connector's NVML sampler; installing it adds a package and changes no existing version.
import importlib.util
if importlib.util.find_spec("pynvml") is None:
    before_pkgs = inventory()
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--index-url", "https://pypi.org/simple", "nvidia-ml-py"], check=True)
    changed = {k: [v, inventory().get(k)] for k, v in before_pkgs.items() if inventory().get(k) != v}
    mark("E2_pynvml_installed", changed=changed)
    if changed:
        verdict("E2_nvml_offline", "INCONCLUSIVE", f"installing nvidia-ml-py changed existing packages {changed}")
res = run_stage("nvml", duration=120, workdir=f"{ATTEMPT_DIR}/stages", extra_args=("--gpu-interval", "1.0"),
                markers=MarkerLog(f"{ATTEMPT_DIR}/markers.jsonl", echo=print))
mark("E2_result", passed=res["passed"], rc=res["exit"]["returncode"], signals=res["exit"].get("signals_sent"), failed=[c[0] for c in res["checks"] if not c[1]])
verdict("E2_nvml_offline", "PASS" if res["passed"] else "FAIL", f"rc={res['exit']['returncode']} failed checks {[c[0] for c in res['checks'] if not c[1]]}")'''
    return [
        header("E. GPU session without vLLM: real NVML child, then (unsupported) registration", "No Stokastisk server is started. The connector's NVML "
               "stage runs as a supervised child against the real T4s (no network, no credential). Registration without a served model is **not** "
               "sent (see E3). The session is then observed after the child has exited.", n),
        prelude(n),
        code(GPU_HELPERS),
        md("## E1. Idle survival: 5 minutes"),
        code('observe("E1_gpu_idle_5min", 300, every=15)'),
        md("## E2. Fetch the reviewed connector; offline NVML child for 120 s (no network)"),
        code(sub(CONNECTOR_FETCH, REV=ENIGMA_REV)),
        code(nvml),
        md("## E3. Registration without vLLM: unsupported (no request)"),
        code(unsupported),
        md("## E4. Survival after the child exited: 5 minutes\nRun this cell even though E3 stops *Run all*: it requires only E2."),
        code('require("E2_nvml_offline")\nobserve("E4_after_child_exit_5min", 300, every=15)'),
        md("## Evidence export"),
        code(EXPORT),
    ]


def nb_f() -> list[dict]:
    n = "enigma_stokastisk_qwen25_05b_final_integration"
    gate = r'''# Gate: run this notebook only after notebooks A, B, C and D PASSED (each in its own session). Edit deliberately.
BASELINES_PASSED = False        # set True only after reading the A-D verdicts
assert BASELINES_PASSED, "Run notebooks A-D first; this notebook is not a diagnostic and is not GPU-verified until it passes on Kaggle"
verdict("baselines_confirmed", "PASS", "operator confirmed A-D PASS")'''
    helper = r'''from enigma_connector.stage_runner import run_stage, MarkerLog
INTERVAL_ARGS = ("--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0")      # explicit, as in every verified run
ORDER = ("nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full")


def stage(name, **kw):
    """One supervised connector stage. Prerequisites must have PASSED in THIS session; anything but PASS stops the notebook."""
    require("connector_fetch", "local_inference", *[f"stage_{p}" for p in ORDER[:ORDER.index(name)]])
    assert server.health(), "local vLLM unhealthy"
    cloud = name not in ("nvml", "vllm")
    res = run_stage(name, api_url=API_URL if cloud else "", token_getter=token_getter if cloud else None, vllm_url=server.base_url, model=MODEL, tp=1,
                    server=server, workdir=f"{ATTEMPT_DIR}/stages", extra_args=INTERVAL_ARGS, markers=MarkerLog(f"{ATTEMPT_DIR}/markers.jsonl", echo=print), **kw)
    stats = (res.get("summary") or {}).get("stats") or {}
    extra_ok, why = True, ""
    if name == "jobs":
        extra_ok, why = stats.get("jobs_done") == 0 and stats.get("jobs_failed") == 0, "job stage must stay idle"
    if name == "full":
        extra_ok, why = (stats.get("jobs_done") or 0) >= 1 and stats.get("jobs_failed") == 0, "at least one real Playground job must complete"
    failed = [c[0] for c in res["checks"] if not c[1]] + ([why] if not extra_ok else [])
    verdict(f"stage_{name}", "PASS" if res["passed"] and extra_ok else "FAIL", f"rc={res['exit']['returncode']} failed={failed}",
            run_id=(res.get("summary") or {}).get("run_id"), stats=stats)
    return res'''
    cloud_gate = r'''# Cloud gate: confirm in Enigma that runtime p2-kaggle-stokastisk-real is REAL (Mock off) and that no Playground job is queued. Edit deliberately.
ALLOW_CLOUD_CONNECTION = False
assert ALLOW_CLOUD_CONNECTION, "cloud gate closed"
verdict("cloud_gate", "PASS", "operator opened the cloud gate")'''
    return [
        header("F. Full integration: Stokastisk build 5 + Enigma connector (Qwen2.5-0.5B)", "**Prepared, not GPU-verified.** Run only after A–D pass. Stages "
               "run strictly in order and stop at the first failure: offline NVML, offline vLLM metrics, registration, heartbeat-only, idle job polling, "
               "single telemetry upload, continuous telemetry, and the full connector with one real Playground inference.", n),
        prelude(n),
        code(GPU_HELPERS),
        code(gate),
        md("## F0. Install build 5, start the server, verify local inference"),
        code('require("baselines_confirmed")\n' + sub(INSTALL, WHEEL=WHEEL, SHA=WHEEL_SHA256, KNOWN=json.dumps(KNOWN_REPLACEMENTS))),
        code(sub(SERVER, MODEL=MODEL)),
        code(LOCAL_INFERENCE),
        md("## F1. Reviewed connector and stage helper"),
        code(sub(CONNECTOR_FETCH, REV=ENIGMA_REV)),
        code(sub(TOKEN_GETTER, API=API_URL)),
        code(helper),
        md("## F2. Offline stages"),
        code('_ = stage("nvml", duration=120)'),
        code('_ = stage("vllm")'),
        md("## F3. Cloud stages"),
        code(cloud_gate),
        code('require("cloud_gate")\n_ = stage("register")'),
        code('_ = stage("heartbeat")'),
        code('_ = stage("jobs")'),
        code('_ = stage("upload-once")'),
        code('_ = stage("telemetry")'),
        md("## F4. Full connector with one real Playground inference (600 s)\nStart the cell, then open "
           "https://enigma-web-production-8986d34a.up.railway.app/app/runtimes, select the real runtime, open Playground and submit one prompt. "
           "PASS requires at least one completed job and no failed job; confirm the reply persisted in the UI."),
        code('_ = stage("full", duration=600, poll_s=15)'),
        md("## Evidence export"),
        code(EXPORT),
    ]


BUILDERS = {"enigma_kaggle_cpu_platform_baseline": nb_a, "enigma_kaggle_cpu_connector_isolation": nb_b, "stokastisk_kaggle_gpu_platform_baseline": nb_c,
            "stokastisk_kaggle_vllm_stability": nb_d, "enigma_kaggle_registration_without_vllm": nb_e,
            "enigma_stokastisk_qwen25_05b_final_integration": nb_f}


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
