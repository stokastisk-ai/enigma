# Enigma × Kaggle — minimal independent isolation cells (2026-11-26)

Why these exist: on 2026-11-26 a fresh Kaggle session died at 14:11:41 UTC **while a pure-stdlib HTTPS health loop was sleeping**
(no connector, no subprocess, no secret, no NVML call). Two earlier fatal sessions (2026-11-25 18:00 and 18:38 UTC) stopped ~10–11 s
after a successful registration. Each cell below exercises ONE thing so the next fatal session can be attributed.

Rules
* Every cell is independent: it needs only cell **P** (and **R** where stated). Run P first in every new session.
* Every step writes a marker to `/kaggle/working/enigma-iso/markers.jsonl` (flushed + fsynced) **and** prints it with `flush=True`.
  Cell outputs printed before a fatal error were preserved in the saved notebook; VM files were not.
* No cell prints the runtime key, its length or any part of it. No cell fabricates GPU data; C/D/E run only on a real T4 session.
* Session plan: **CPU session** (Accelerator: None): P, R, Z0, A, B, F, G. **GPU session** (2×T4): P, R, Z1, C, then start the server, D, E.

## P. Prelude — guard and marker helper (stdlib only, no network)

```python
import os, json, time, platform, threading
assert os.path.isdir("/kaggle/working"), "Kaggle only"
ISO = "/kaggle/working/enigma-iso"
os.makedirs(ISO, exist_ok=True)
_T0 = time.monotonic()

def mark(name, **fields):
    try:
        up = float(open("/proc/uptime").read().split()[0])
    except OSError:
        up = None
    mem = {}
    try:
        for line in open("/proc/meminfo"):
            if line.startswith(("MemAvailable:", "MemTotal:")):
                mem[line.split(":")[0]] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    oom = None
    try:
        for line in open("/sys/fs/cgroup/memory.events"):
            if line.startswith("oom_kill "):
                oom = int(line.split()[1])
    except OSError:
        pass
    rec = {"marker": name, "utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), "since_prelude_s": round(time.monotonic() - _T0, 1),
           "vm_uptime_s": up, "pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0), "ppid": os.getppid(),
           "threads": threading.active_count(), "mem_mib": mem, "oom_kill": oom, **fields}
    line = json.dumps(rec, default=str)
    with open(f"{ISO}/markers.jsonl", "a") as fh:
        fh.write(line + "\n"); fh.flush(); os.fsync(fh.fileno())
    print("MARK", line, flush=True)

mark("prelude", python=platform.python_version())
```

## R. Fetch the connector at the pinned revision (either session; needed by C, D, E, F; no Enigma request)

```python
import subprocess, sys
from pathlib import Path
REV = "PINNED_REVISION"
repo = Path("/kaggle/working/enigma-acceptance-source")
if not repo.exists():
    subprocess.run(["git", "clone", "-q", "https://github.com/stokastisk-ai/enigma.git", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin", REV], check=True)
subprocess.run(["git", "-C", str(repo), "checkout", "-q", REV], check=True)
sys.path.insert(0, str(repo / "connector"))
import enigma_connector
connector_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
mark("connector_ready", sha=connector_sha, path=enigma_connector.__file__)
```

## Z0. Control — idle CPU session survival (10 min, nothing running)

If this session dies, the cause is not Enigma, the connector, vLLM or the GPU.

```python
mark("z0_begin")
for i in range(40):
    time.sleep(15)
    mark("z0_alive", i=i)
mark("z0_end")
```

## Z1. Control — vLLM server idle survival (GPU session, 10 min, no Enigma traffic)

Start the Stokastisk server exactly as the main notebook does (TP=1, gpu_memory_utilization 0.60), then run:

```python
mark("z1_begin", server_pid=getattr(server, "pid", None))
for i in range(40):
    time.sleep(15)
    mark("z1_alive", i=i, server_running=bool(getattr(server, "running", None)), healthy=bool(server.health()))
mark("z1_end")
```

## A. CPU-only unauthenticated HTTPS health requests

```python
import urllib.request
URL = "https://enigma-api-production-c8cffe9d.up.railway.app/api/v1/health"
mark("a_begin")
for i in range(6):
    t = time.monotonic()
    try:
        req = urllib.request.Request(URL, headers={"Accept": "application/json", "User-Agent": "Enigma-Kaggle-Diagnostic/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            mark("a_request", i=i + 1, http=r.status, bytes=len(r.read(65536)), s=round(time.monotonic() - t, 3))
    except Exception as e:
        mark("a_request_error", i=i + 1, error=type(e).__name__)
    if i < 5:
        mark("a_sleep_begin", i=i + 1)
        time.sleep(10)
mark("a_end")
```

## B. Kaggle Secret retrieval only (no Enigma request)

`UserSecretsClient` itself talks to Kaggle's own secret service; nothing is sent to Enigma. The value is checked and discarded, never printed.

```python
import re
mark("b_before_secret_retrieval")
from kaggle_secrets import UserSecretsClient
_ok = bool(re.fullmatch(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN").strip()))
mark("b_after_secret_retrieval", format_ok=_ok)
del _ok
```

## C. Registration-only child WITHOUT the vLLM server (GPU session, before the server starts)

Real NVML inventory only (nothing invented). No server is listening, so the reported identity simply lacks the server's vLLM version.
Make sure no Playground job is queued for the runtime. One registration; the run it opens is aborted server-side after heartbeat expiry.

```python
import re
from kaggle_secrets import UserSecretsClient
from enigma_connector.stage_runner import run_stage, MarkerLog
def token_getter():
    t = UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN").strip()
    if not re.fullmatch(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", t):
        raise ValueError("unexpected runtime credential format")
    return t
mark("c_begin")
rc = run_stage("register", api_url="https://enigma-api-production-c8cffe9d.up.railway.app", token_getter=token_getter,
               vllm_url="http://127.0.0.1:8000", model="Qwen/Qwen2.5-0.5B-Instruct", tp=1, workdir=f"{ISO}/c",
               extra_args=("--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0"), markers=MarkerLog(f"{ISO}/markers.jsonl", echo=print))
mark("c_end", passed=rc["passed"], returncode=rc["exit"]["returncode"], signals_sent=rc["exit"].get("signals_sent"))
```

## D. Real registration with the Stokastisk server running, no background threads (GPU session)

Uses `token_getter` from cell C (re-define it if C was skipped).

```python
from enigma_connector.stage_runner import run_stage, MarkerLog
mark("d_begin", server_running=bool(server.running))
rd = run_stage("register", api_url="https://enigma-api-production-c8cffe9d.up.railway.app", token_getter=token_getter,
               vllm_url=server.base_url, model=MODEL, tp=1, server=server, workdir=f"{ISO}/d",
               extra_args=("--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0"), markers=MarkerLog(f"{ISO}/markers.jsonl", echo=print))
mark("d_end", passed=rd["passed"], returncode=rd["exit"]["returncode"], signals_sent=rd["exit"].get("signals_sent"))
```

## E. Heartbeat-only (GPU session, 60 s)

```python
from enigma_connector.stage_runner import run_stage, MarkerLog
mark("e_begin")
re_ = run_stage("heartbeat", duration=60, api_url="https://enigma-api-production-c8cffe9d.up.railway.app", token_getter=token_getter,
                vllm_url=server.base_url, model=MODEL, tp=1, server=server, workdir=f"{ISO}/e",
                extra_args=("--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0"), markers=MarkerLog(f"{ISO}/markers.jsonl", echo=print))
mark("e_end", passed=re_["passed"], returncode=re_["exit"]["returncode"])
```

## F. Supervised subprocess cleanup without any network (CPU session; needs R)

```python
import subprocess, sys, signal
from enigma_connector.supervise import ConnectorProcess
me = (os.getpid(), os.getpgid(0), os.getsid(0))
sentinel = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.5)"], start_new_session=True)
mark("f_begin", sentinel=sentinel.pid)
p1 = ConnectorProcess([], dict(os.environ), f"{ISO}/f1", command=[sys.executable, "-c", "print('exit by itself')"])
mark("f_before_spawn_self_exit"); mark("f_after_spawn_self_exit", child=p1.start())
p1.wait(10); mark("f_before_cleanup_self_exit"); r1 = p1.stop(grace_s=2); mark("f_after_cleanup_self_exit", rc=r1["returncode"], sent=r1["signals_sent"])
p2 = ConnectorProcess([], dict(os.environ), f"{ISO}/f2", command=[sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"])
mark("f_before_spawn_needs_term"); mark("f_after_spawn_needs_term", child=p2.start())
time.sleep(1); mark("f_before_cleanup_needs_term"); r2 = p2.stop(grace_s=5, kill_after_s=3); mark("f_after_cleanup_needs_term", rc=r2["returncode"], sent=r2["signals_sent"])
ok = r1["signals_sent"] == [] and r2["signals_sent"][:1] == ["SIGTERM"] and sentinel.poll() is None and me == (os.getpid(), os.getpgid(0), os.getsid(0))
sentinel.kill(); sentinel.wait(5)
mark("f_end", passed=ok)
```

## G. Checkpoint creation and archive export only (no connector, no network)

```python
import shutil
mark("g_before_checkpoint")
data = {"kind": "isolation-checkpoint", "python": platform.python_version(), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
tmp = f"{ISO}/checkpoint.tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh); fh.flush(); os.fsync(fh.fileno())
os.replace(tmp, f"{ISO}/checkpoint.json")
mark("g_after_checkpoint")
mark("g_before_archive")
path = shutil.make_archive("/kaggle/working/enigma-iso-logs", "zip", ISO)
mark("g_after_archive", archive=path, bytes=os.path.getsize(path))
```
