"""Derive the small, redacted, provenance-linked baseline acceptance manifest (scripts/kaggle_cells/baseline_manifest.json) from the owner's EXECUTED
Kaggle notebooks and diagnostic archives. Read-only: nothing in the archives is executed, installed or extracted to disk (members are read in memory),
and the evidence files are never modified. Every value comes from the evidence; nothing is assumed.

    python3.13 -I scripts/build_baseline_manifest.py /path/to/Project-Stokastisk/notebooks [--check]

--check re-derives the manifest and fails if the committed one differs (except the generation timestamp)."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "kaggle_cells" / "baseline_manifest.json"
FILES = {"nb01_notebook": "01-enigma-kaggle-platform-aws-diagnostics.ipynb",
         "nb01_archive": "01-enigma-kaggle-platform-aws-diagnostics-enigma-diag-attempts.zip",
         "nb02_notebook": "02-stokastisk-build5-kaggle-t4-validation.ipynb",
         "nb02_archive": "02-stokastisk-build5-kaggle-t4-validation-enigma-diag-attempts.zip",
         "nb03_previous_notebook": "03-enigma-stokastisk-build5-aws-integration.ipynb"}
WHEEL_SHA256 = "fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e"
WHEEL_SIZE = 38057515
_SECRET = re.compile(r"(?:enig_rt_|edx_)[a-z0-9]{8}_[A-Za-z0-9_-]{10,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verdicts(nb_path: Path) -> tuple[dict, list]:
    """VERDICT lines from the SAVED cell outputs (the last one per step wins, as in the run) plus the executed-cell count."""
    nb = json.loads(nb_path.read_text())
    out, executed = {}, []
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] != "code":
            continue
        if c.get("execution_count") is not None:
            executed.append(i)
        for o in c.get("outputs", []):
            for line in "".join(o.get("text", "")).splitlines() if o.get("output_type") == "stream" else []:
                m = re.match(r"VERDICT (\S+): (PASS|FAIL|INCONCLUSIVE) \| (.*)", line)
                if m:
                    out[m.group(1)] = {"status": m.group(2), "reason": m.group(3)[:240]}
    return out, executed


def markers(archive: Path, inner_suffix: str) -> tuple[list, dict]:
    """markers.jsonl rows of the attempt (read in memory) and a {member basename: bytes} map of that attempt directory."""
    z = zipfile.ZipFile(archive)
    assert z.testzip() is None, f"corrupt archive {archive.name}"
    members = {}
    for i in z.infolist():
        assert not i.filename.startswith("/") and ".." not in i.filename.split("/"), f"unsafe member {i.filename}"
        if inner_suffix in i.filename and not i.is_dir():
            members[i.filename.rsplit("/", 1)[1]] = i
    rows = [json.loads(l) for l in z.read(members["markers.jsonl"]).decode().splitlines() if l.strip()]
    return rows, {k: (z, v) for k, v in members.items()}


def first(rows, name, **match):
    return next((r for r in rows if r.get("marker") == name and all(r.get(k) == v for k, v in match.items())), None)


def build(src: Path) -> dict:
    paths = {k: src / v for k, v in FILES.items()}
    for k, p in paths.items():
        assert p.is_file(), f"missing evidence file {p}"
    sources = {k: {"file": p.name, "size": p.stat().st_size, "sha256": sha256_file(p)} for k, p in paths.items()}

    # ---- notebook 01 (experiment A: AWS-only platform baseline)
    v1, ex1 = verdicts(paths["nb01_notebook"])
    r1, _ = markers(paths["nb01_archive"], "/20261127T001031Z-29f49dac/")
    p1 = r1[0]
    surv = v1.get("aws_survival", {}).get("reason", "")
    nb01 = {"attempt": p1["attempt"], "experiment": p1["experiment"], "mode": p1["mode"], "transport": p1["transport"], "python": p1["python"],
            "gpu_device": p1["gpu_device"], "inputs": p1["inputs"], "records": len(r1), "first_utc": p1["utc"], "last_utc": r1[-1]["utc"],
            "peak_cgroup_mib": max(r.get("cgroup_current_mib", 0) for r in r1), "oom_kill_max": max(r["cgroup_events"].get("oom_kill", 0) for r in r1),
            "railway_requests": sum(1 for r in r1 if "railway" in r.get("marker", "")), "verdicts": {k: v["status"] for k, v in v1.items()},
            "aws_survival": surv, "executed_code_cells": len(ex1)}

    # ---- notebook 02 (standalone build-5 GPU baseline)
    v2, ex2 = verdicts(paths["nb02_notebook"])
    r2, m2 = markers(paths["nb02_archive"], "/20261127T004108Z-4acaca8a/")
    p2, env = r2[0], first(r2, "environment")
    z, whl = m2["stokastisk-0.1.0-5-cp313-cp313-linux_x86_64.whl"]
    h, n = hashlib.sha256(), 0
    with z.open(whl) as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b); n += len(b)
    wz = zipfile.ZipFile(io.BytesIO(z.read(whl)))
    wheel_meta = wz.read("stokastisk-0.1.0.dist-info/WHEEL").decode()
    md = wz.read("stokastisk-0.1.0.dist-info/METADATA").decode().split("\n\n")[0]
    natives = sorted(i.filename for i in wz.infolist() if i.filename.endswith(".so"))
    pipc = json.loads(z.read(m2["pip-check.json"][1]))
    log = z.read(m2["vllm-server.log"][1]).decode(errors="replace")
    cfg = {k: (re.search(rf"{k}=([A-Za-z0-9_.]+)", log) or [None, None])[1] for k in ("tensor_parallel_size", "pipeline_parallel_size", "max_seq_len",
                                                                                      "enforce_eager")}
    cfg.update(dtype="float16" if "dtype=torch.float16" in log else None, attention="TRITON_ATTN" if "TRITON_ATTN" in log else None,
               model=(re.search(r"model='([^']+)'", log) or [None, None])[1], gpu_memory_utilization=0.60,
               gpu_memory_utilization_source="notebook 02 server cell (StokastiskConfig gpu_memory_utilization=0.60)")
    inst = first(r2, "verdict", step="install") or {}
    stop = first(r2, "server_stopped") or {}
    obs = first(r2, "verdict", step="server_passive_10min") or {}
    nb02 = {"attempt": p2["attempt"], "experiment": p2["experiment"], "mode": p2["mode"], "transport": p2["transport"], "python": p2["python"],
            "gpus": env["gpu_names"], "versions": env["versions"], "records": len(r2), "first_utc": p2["utc"], "last_utc": r2[-1]["utc"],
            "peak_cgroup_mib": max(r.get("cgroup_current_mib", 0) for r in r2), "oom_kill_max": max(r["cgroup_events"].get("oom_kill", 0) for r in r2),
            "aws_requests": sum(1 for r in r2 if r.get("marker") == "remote_result"), "railway_requests": sum(1 for r in r2 if "railway" in r.get("marker", "")),
            "wheel": {"file": whl.filename.rsplit("/", 1)[1], "size": n, "sha256": h.hexdigest(), "wheel_build_tag": re.search(r"Build: (\S+)", wheel_meta)[1],
                      "tag": re.search(r"Tag: (\S+)", wheel_meta)[1], "name_version": [re.search(r"Name: (\S+)", md)[1], re.search(r"Version: (\S+)", md)[1]],
                      "pins": [l.split(": ", 1)[1] for l in md.splitlines() if l.startswith("Requires-Dist: pydantic")], "native_libraries": natives},
            "model_config": cfg,
            "install": {"replaced": inst.get("changed"), "installed_build": inst.get("installed_build"), "preexisting_conflicts": len(pipc["before"]),
                        "conflicts_after": len(pipc["after"]), "introduced_conflicts": pipc["introduced"],
                        "first_plan_stopped_for_acknowledgement": any(r.get("step") == "install_plan" and r.get("status") == "INCONCLUSIVE" for r in r2)},
            "local_inference": v2.get("local_inference", {}).get("reason"), "observation": {"observed_s": obs.get("observed_s"), "ticks": obs.get("ticks"),
                                                                                             "oom_kill_delta": obs.get("oom_kill_delta")},
            "shutdown": stop.get("report"),
            "server_log_notes": {"leaked_semaphore_warning": "leaked semaphore" in log, "process_manager_force_killed_remaining": "force killing remaining" in log,
                                 "runtime_triton_jit_warnings": log.count("Triton kernel JIT compilation during inference")},
            "verdicts": {k: v["status"] for k, v in v2.items()}, "executed_code_cells": len(ex2)}

    # ---- the previous (interrupted) notebook 03 attempt: recorded for provenance, never counted as a baseline
    v3, ex3 = verdicts(paths["nb03_previous_notebook"])
    att3 = re.search(r"ATTEMPT (\d{8}T\d{6}Z-[0-9a-f]{8})", paths["nb03_previous_notebook"].read_text())[1]
    nb03 = {"attempt": att3, "verdicts": {k: v["status"] for k, v in v3.items()}, "executed_code_cells": len(ex3),
            "outcome": "INTERRUPTED after runtime_capabilities (Railway transport); build 5 not installed"}

    acceptance = {
        "NOTEBOOK_01_AWS_ONLY_BASELINE": "PASS" if nb01["verdicts"].get("overall") == "PASS" and nb01["experiment"].endswith("expA") else "FAIL",
        "BUILD5_WHEEL_SHA256": "VERIFIED" if (nb02["wheel"]["sha256"], nb02["wheel"]["size"], nb02["wheel"]["wheel_build_tag"]) == (WHEEL_SHA256, WHEEL_SIZE, "5")
        else "FAILED",
        "BUILD5_LOCAL_GPU_INFERENCE": nb02["verdicts"].get("local_inference", "MISSING"),
        "BUILD5_SERVER_600S_STABILITY": nb02["verdicts"].get("server_passive_10min", "MISSING"),
        "BUILD5_SAFE_SHUTDOWN": nb02["verdicts"].get("server_shutdown", "MISSING"),
        "BUILD5_DEPENDENCY_CONFLICTS_INTRODUCED": len(nb02["install"]["introduced_conflicts"]),
    }
    m = {"schema": 1, "kind": "historical baseline acceptance (NOT current-session readiness)", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "sources": sources, "acceptance": acceptance, "nb01": nb01, "nb02": nb02, "nb03_previous": nb03}
    blob = json.dumps(m, sort_keys=True)
    assert not _SECRET.search(blob), "a credential-shaped string reached the manifest"
    return m


def digest(m: dict) -> str:
    """Content digest over everything except the generation timestamp and the digest itself (stable across re-derivation)."""
    core = {k: v for k, v in m.items() if k not in ("generated_utc", "manifest_sha256")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    m = build(Path(args[0]))
    m["manifest_sha256"] = digest(m)
    if "--check" in sys.argv:
        old = json.loads(OUT.read_text())
        ok = old["manifest_sha256"] == m["manifest_sha256"] == digest(old)
        print("baseline manifest", "MATCHES the evidence" if ok else "DIFFERS from the evidence")
        return 0 if ok else 1
    OUT.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    print("wrote", OUT, OUT.stat().st_size, "bytes; manifest_sha256", m["manifest_sha256"])
    print(json.dumps(m["acceptance"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
