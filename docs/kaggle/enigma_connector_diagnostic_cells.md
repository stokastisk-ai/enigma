# Staged connector diagnostics for Kaggle — replacement cells

These cells **replace** Step 3, Step 4 (optional), Steps 6A/6B, Step 7 and Step 8 of `enigma-stokastisk-qwen25-05b-kaggle.ipynb`.
Everything else (install, environment check, local vLLM inference, local measured acceptance) stays as it is.

**Why:** the previous Step 6B ran the connector's three background threads *inside the notebook kernel*. When that kernel died the session logs died with it.
Here the connector runs as a **separate supervised process**: its stdout, stderr, an fsynced event log and a `faulthandler` dump go to files, and its exit code or
terminating signal is recorded by the notebook instead of being lost. Only one subsystem is enabled per stage, so a failure names the component that was running.

Connector revision for these cells: **`4212ab7`** (`connector/` at this revision is what you must fetch; the API needs no change).

## Stage sequence

Run **one stage at a time, in this order**. Stop at the first `FAIL`: do not run later stages, and paste the printed output.

| # | Stage | What is enabled | Network to Enigma | Needs the key | PASS when |
|---|-------|-----------------|-------------------|---------------|-----------|
| E | `nvml` (120 s) | NVML sampling every 1 s from a background thread | none | no | exit 0, ≥ 84 samples, 0 sample errors |
| F | `vllm` (60 s) | local `/metrics` scrape + parse every 2 s | none | no | exit 0, ≥ 21 scrapes, every scrape parsed |
| B | `register` | one registration, **no background thread** | yes | yes | exit 0, `mock` false, 0 threads started |
| C | `heartbeat` (60 s) | registration + heartbeat | yes | yes | ≥ 5 heartbeats acknowledged, none failed |
| D | `jobs` (60 s) | registration + job claiming | yes | yes | ≥ 10 claims acknowledged (no job queued) |
| G | `upload-once` (15 s) | local sampling, then exactly **one** upload | yes | yes | exactly one batch acknowledged |
| H | `telemetry` (120 s) | heartbeat + sampling + continuous upload, **no job claiming** | yes | yes | ≥ 15 batches, ≥ 10 heartbeats acknowledged |
| I | `full` (600 s) | everything | yes | yes | as H plus claiming; submit a Playground prompt while it runs |

Every stage also checks that **no OS out-of-memory kill happened** (cgroup `oom_kill` counter) and, when you pass `server=server`, that the **vLLM server process stayed up**.
The live status line prints, every few seconds: child state, the last event the connector finished, whether the vLLM server is up, free host memory, cgroup memory and `oom_kill`.

If the **kernel itself dies again while the connector is a separate process**, the cause is not an in-kernel connector thread. The last printed status line, the files in
`/kaggle/working/enigma-diag/*/` and the vLLM server log (cell P at the end) are then the evidence.

## Cell R1 — replaces Step 4 (optional: lower GPU memory for the first diagnostic run)

`StokastiskConfig.gpu_memory_utilization` defaults to **0.88** (about 13.5 GiB of a 15 GiB T4; your GPU 0 showed 13 961 MiB). It accepts 0.1–0.98.
Keep Qwen2.5-0.5B-Instruct, TP=1, PP=1, context 2048. Use 0.60 for the first run; restore the default afterwards.

```python
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
TP = 1
GPU_MEM_UTIL = 0.60            # SDK default 0.88; valid range 0.1-0.98
from stokastisk import StokastiskConfig, serving
from stokastisk.serving import ServerProcess
server = ServerProcess(MODEL, StokastiskConfig(tensor_parallel_size=TP, max_model_len=2048, gpu_memory_utilization=GPU_MEM_UTIL),
                       host="127.0.0.1", port=8000, served_model_name=MODEL)
try:
    server.start_and_wait(timeout=900)
    assert server.health()
except BaseException:
    server.stop()
    raise
print("Healthy:", server.health(), "| pid:", server.pid, "| log:", server.log_path)
```

## Cell R2 — replaces Step 3 (fetch the new connector revision)

Restart the kernel first if the previous revision was already imported.

```python
from pathlib import Path
import subprocess, sys, importlib
REV = "4212ab7"
repo = Path("/kaggle/working/enigma-acceptance-source")
if not repo.exists():
    subprocess.run(["git", "clone", "https://github.com/stokastisk-ai/enigma.git", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "fetch", "origin"], check=True)
subprocess.run(["git", "-C", str(repo), "checkout", "--detach", REV], check=True)
connector_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
assert connector_sha.startswith(REV)
assert (repo / "connector/enigma_connector/stage_runner.py").is_file(), "wrong revision: stage_runner.py is missing"
for m in [m for m in sys.modules if m.startswith("enigma_connector")]:
    del sys.modules[m]                                   # never keep a previously imported older revision
sys.path.insert(0, str(repo / "connector"))
for mod, dist in [("prometheus_client", "prometheus_client"), ("pynvml", "nvidia-ml-py")]:
    try:
        importlib.import_module(mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", dist], check=True)
import enigma_connector
print("Connector revision:", connector_sha, "| version:", enigma_connector.__version__)
```

## Cell S0 — replaces Steps 6A and 6B (defines the stage helper; starts nothing)

The key is read from the Kaggle Secret **only inside the helper, at the moment a stage needs it**, and goes to the child through its environment. It is never printed,
never put on a command line, and is scrubbed from the notebook process once the child has been spawned.

```python
from kaggle_secrets import UserSecretsClient
from enigma_connector.stage_runner import run_stage

API_URL = "https://enigma-api-production-c8cffe9d.up.railway.app"
token_getter = lambda: UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN").strip()
COMMON = dict(vllm_url=server.base_url, model=MODEL, tp=TP, server=server, workdir="/kaggle/working/enigma-diag")
results = {}

def stage(name, **kw):
    """Run ONE isolated stage. Stops the notebook at the first failure so the evidence is not overwritten by later stages."""
    r = run_stage(name, api_url=API_URL, token_getter=token_getter, **COMMON, **kw)
    results[name] = r
    assert r["passed"], f"stage {name!r} FAILED: read the output above and do not run the next stage"
    return r
print("stage helper ready; nothing has been started")
```

## Cells S-E … S-I — one cell per stage, run in order, stop at the first FAIL

```python
stage("nvml")            # E. offline: repeated NVML sampling from a background thread. No network, no key.
```

```python
stage("vllm")            # F. offline: repeated local /metrics scraping and parsing. No network, no key.
```

```python
stage("register")        # B. one registration, no background thread. Expect: mock False, 0 threads started.
```

```python
stage("heartbeat")       # C. registration + heartbeat only.
```

```python
stage("jobs")            # D. registration + job claiming only. Make sure no Playground job is queued for this runtime.
```

```python
stage("upload-once")     # G. sample locally for 15 s, then exactly ONE telemetry upload.
```

```python
stage("telemetry")       # H. continuous sampling + upload + heartbeat, still NO job claiming.
```

## Cell S7 — replaces Step 7 (Playground, full production connector)

Start this cell, then in Enigma open **Playground**, select this runtime and send a prompt while the stage is running (10 minutes). A 15-second status poll keeps the output short.

```python
r = stage("full", duration=600, poll_s=15)
print("jobs completed by the connector:", (r["summary"] or {}).get("stats", {}).get("jobs_done"),
      "| failed:", (r["summary"] or {}).get("stats", {}).get("jobs_failed"))
print("Confirm in Enigma that the generated text appeared in the Playground and persisted after a reload.")
```

## Cell S8 — replaces Step 8 (evidence, then always clean up)

The connector is a child process that has already exited; nothing here stops an in-kernel connector. Only the vLLM server is stopped.
Everything saved here is secret-free (the logs are redacted when written).

```python
import json, platform, shutil, subprocess, importlib.metadata as md
from pathlib import Path
out = Path("/kaggle/working/enigma-stokastisk-evidence.json")
evidence = {
    "host": "kaggle", "python": platform.python_version(), "sdk": stokastisk.__version__, "vllm": md.version("vllm"),
    "torch": md.version("torch"), "triton": md.version("triton"), "connector_sha": connector_sha,
    "model": MODEL, "tp": TP, "pp": 1, "max_model_len": 2048, "gpu_memory_utilization": globals().get("GPU_MEM_UTIL", "default (0.88)"),
    "stages": {k: {"passed": v["passed"], "checks": v["checks"], "exit": v["exit"], "summary": v["summary"], "dir": v["dir"], "oom_kill_delta": v["oom_kill_delta"]}
               for k, v in results.items()},
}
try:
    evidence["server_exit_code"] = server.stop()
    evidence["server_shutdown_report"] = server.shutdown_report
finally:
    out.write_text(json.dumps(evidence, indent=2, default=str))
    print(out.read_text())
    shutil.make_archive("/kaggle/working/enigma-diag-logs", "zip", "/kaggle/working/enigma-diag")
    subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv"], check=False)
    print("Saved:", out, "and /kaggle/working/enigma-diag-logs.zip")
```

## Cell P — only after a crash: read what the dead session left behind

```python
import glob, subprocess
for d in sorted(glob.glob("/kaggle/working/enigma-diag/*/")):
    print("=" * 70, d)
    for name in ("connector.diag.jsonl", "connector.faulthandler.log", "connector.stderr.log"):
        subprocess.run(f"echo '--- {name}'; tail -n 12 '{d}{name}' 2>/dev/null", shell=True)
subprocess.run("echo '--- vLLM server log'; tail -n 60 /tmp/stokastisk-server-*/server.log 2>/dev/null", shell=True)
subprocess.run("echo '--- cgroup memory events'; cat /sys/fs/cgroup/memory.events 2>/dev/null", shell=True)
```
