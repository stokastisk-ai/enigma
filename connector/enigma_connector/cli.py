"""``python -m enigma_connector --mode mock|stokastisk``. Configuration comes from the environment; the token is never printed.

stokastisk mode ATTACHES to a vLLM server you already started with ``stokastisk.serving.ServerProcess`` on 127.0.0.1 (see
docs/SDK_INTEGRATION.md). Nothing here starts the server, installs packages or opens an inbound port.

Diagnostic stages (``--stage``) switch the connector's subsystems on one at a time, so a crash can be attributed to the component that was running:

    register     registration only: no background thread of any kind          (exit as soon as the runtime is registered)
    heartbeat    registration + heartbeat only
    jobs         registration + job claiming only (use a runtime with no queued jobs)
    nvml         OFFLINE: repeated local NVML sampling, no network, no token
    vllm         OFFLINE: repeated local vLLM /metrics scraping, no network, no token
    upload-once  registration + local sampling for --duration seconds, then exactly ONE telemetry upload
    telemetry    registration + heartbeat + continuous sampling and upload (no job claiming)
    full         everything (the default, identical to the previous behaviour)

Real GPU telemetry only ever comes from NVML in ``--mode stokastisk``; ``--mode mock`` is always labelled mock end to end. A stage never changes that."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time

from .config import Config, ConfigError
from .diag import NULL_DIAG, DiagLog, FaultHandler
from .gpu import NvmlSampler, NvmlUnavailable
from .http import ApiClient, ApiError, AuthError
from .identity import collect_sdk_identity
from .jobs import StokastiskLlm
from .mock import MockGpu, MockLlm, MockVllm
from .runner import FEATURES, Connector
from .spool import Spool
from .vllm_metrics import VllmSampler

# exit codes: a supervising notebook can tell WHY the connector process ended
EXIT_OK, EXIT_FATAL, EXIT_CONFIG, EXIT_REFUSED, EXIT_AUTH, EXIT_API = 0, 1, 2, 3, 4, 5

STAGES: dict[str, frozenset[str]] = {
    "register": frozenset(),
    "heartbeat": frozenset({"heartbeat"}),
    "jobs": frozenset({"jobs"}),
    "upload-once": frozenset({"gpu", "vllm"}),            # sample only; stop() performs the single final upload
    "telemetry": frozenset({"heartbeat", "gpu", "vllm", "upload"}),
    "full": FEATURES,
}
OFFLINE_STAGES = ("nvml", "vllm")


def build(cfg: Config, *, features=None, diag: DiagLog | None = None) -> Connector:
    api = ApiClient(cfg)
    spool = Spool(cfg.spool_dir or None, cfg.spool_max_bytes)
    if cfg.mode == "mock":
        mv = MockVllm()
        import os
        delay = float(os.environ.get("ENIGMA_MOCK_TOKEN_DELAY_S", "0.02"))        # slow it down to demo/test the Stop button
        return Connector(cfg, api, MockLlm(token_delay_s=delay, vllm=mv), MockGpu(), mv, spool, is_mock=True, features=features, diag=diag)
    from .gpu import local_gpu_forbidden
    if local_gpu_forbidden():
        raise SystemExit("refusing --mode stokastisk: ENIGMA_FORBID_LOCAL_GPU=1 (real GPU work runs only on an authorised remote GPU host)")
    try:
        gpu = NvmlSampler()
    except NvmlUnavailable as e:
        logging.getLogger("enigma").warning("GPU telemetry disabled: %s", e)
        gpu = None
    ident: dict = {}
    try:
        import stokastisk
        stok_version = getattr(stokastisk, "__version__", None)
    except ImportError:
        stok_version = None
    ident = collect_sdk_identity(cfg.vllm_url, stokastisk_version=stok_version)      # only verified local facts; self-reported to the API
    return Connector(cfg, api, StokastiskLlm(cfg.vllm_url, cfg.served_model, cfg.stream_read_timeout_s), gpu, VllmSampler(cfg.vllm_url), spool,
                     sdk_identity=ident, features=features, diag=diag)


def _summary(stage: str, code: int, c: Connector | None, extra: dict | None = None) -> None:
    """One JSON line on stdout, secret-free: what ran, how it ended, how much traffic it moved."""
    out = {"stage": stage, "exit_code": code, "mock": None if c is None else c.is_mock, "runtime_id": None if c is None else c.runtime_id,
           "run_id": None if c is None else c.run_id, "stats": None if c is None else dict(c.stats),
           "fatal": None if c is None or c.fatal is None else type(c.fatal).__name__, **(extra or {})}
    print(json.dumps(out), flush=True)


def offline_interval(stage: str, a: argparse.Namespace) -> float:
    """Sampling interval for an offline stage. An omitted ``--gpu-interval``/``--vllm-interval`` (argparse default None) falls back to the
    connector's normal Config default instead of reaching the probe loop as None (that raised TypeError: float - NoneType)."""
    flag, attr = (a.gpu_interval, "gpu_interval_s") if stage == "nvml" else (a.vllm_interval, "vllm_interval_s")
    value = float(Config.__dataclass_fields__[attr].default if flag is None else flag)
    if not value > 0:
        raise ValueError(f"{attr} must be > 0, got {value}")
    return value


def _offline(stage: str, a: argparse.Namespace, diag: DiagLog, stop: threading.Event) -> int:
    from .probe import run_local_nvml, run_local_vllm
    from .gpu import local_gpu_forbidden
    try:
        interval = offline_interval(stage, a)
    except ValueError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        diag.emit("config_error", reason="interval")
        return EXIT_CONFIG
    if stage == "nvml":
        if local_gpu_forbidden():
            print("refusing stage nvml: ENIGMA_FORBID_LOCAL_GPU=1 (real GPU work runs only on an authorised remote GPU host)", file=sys.stderr)
            diag.emit("refused", reason="local_gpu_forbidden")
            return EXIT_REFUSED
        try:
            sampler = NvmlSampler()
        except NvmlUnavailable as e:
            print(f"stage nvml: {e}", file=sys.stderr)
            diag.emit("refused", reason="nvml_unavailable")
            return EXIT_REFUSED
        try:
            res = run_local_nvml(sampler, diag, stop, interval_s=interval, duration_s=a.duration)
        finally:
            sampler.close()
    else:
        import os
        url = os.environ.get("ENIGMA_VLLM_URL", "http://127.0.0.1:8000")
        from urllib.parse import urlparse
        if urlparse(url).hostname not in ("127.0.0.1", "localhost", "::1", "[::1]"):
            print("configuration error: the vLLM server must be on localhost: it is never exposed or tunnelled", file=sys.stderr)
            return EXIT_CONFIG
        res = run_local_vllm(VllmSampler(url), diag, stop, interval_s=interval, duration_s=a.duration)
    _summary(stage, EXIT_OK, None, {"offline": True, "network_to_enigma": False, "result": res})
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="enigma_connector")
    ap.add_argument("--mode", choices=["mock", "stokastisk"], default=None)
    ap.add_argument("--model", default=None, help="served model name (stokastisk mode)")
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--max-model-len", type=int, default=2048)
    g = ap.add_argument_group("diagnostics (all optional; the defaults behave exactly like earlier versions)")
    g.add_argument("--stage", choices=[*STAGES, *OFFLINE_STAGES], default="full", help="which subsystems to enable (see the module docstring)")
    g.add_argument("--enable", default=None, help=f"comma list overriding the stage's subsystems: {','.join(sorted(FEATURES))}")
    g.add_argument("--duration", type=float, default=0.0, help="stop by itself after this many seconds (0 = run until SIGTERM/SIGINT)")
    g.add_argument("--gpu-interval", type=float, default=None)
    g.add_argument("--vllm-interval", type=float, default=None)
    g.add_argument("--upload-interval", type=float, default=None)
    g.add_argument("--diag-log", default=None, help="append-only JSONL, fsynced per line, secrets redacted")
    g.add_argument("--faulthandler-log", default=None, help="file that receives every thread's stack on a fatal signal")
    g.add_argument("--hang-dump-s", type=float, default=0.0, help="also dump all thread stacks this often (finds hangs)")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    diag = DiagLog(a.diag_log) if a.diag_log else NULL_DIAG
    fh = FaultHandler(a.faulthandler_log, a.hang_dump_s) if a.faulthandler_log else None
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    diag.emit("process_start", stage=a.stage, mode=a.mode, duration_s=a.duration)
    code, c = EXIT_OK, None
    try:
        if a.stage in OFFLINE_STAGES:
            code = _offline(a.stage, a, diag, stop)
            return code
        features = STAGES[a.stage]
        if a.enable is not None:
            features = frozenset(x.strip() for x in a.enable.split(",") if x.strip())
            if features - FEATURES:
                print(f"configuration error: unknown --enable values {sorted(features - FEATURES)}", file=sys.stderr)
                code = EXIT_CONFIG
                return code
        over: dict = {"tp": a.tp, "max_model_len": a.max_model_len}
        if a.mode:
            over["mode"] = a.mode
        if a.model:
            over["served_model"] = a.model
        for flag, attr in ((a.gpu_interval, "gpu_interval_s"), (a.vllm_interval, "vllm_interval_s"), (a.upload_interval, "upload_interval_s")):
            if flag is not None:
                over[attr] = flag
        try:
            cfg = Config.from_env(**over)
        except ConfigError as e:
            print(f"configuration error: {e}", file=sys.stderr)
            diag.emit("config_error")
            code = EXIT_CONFIG
            return code
        if cfg.mode == "mock":
            cfg.served_model = cfg.served_model or "mock-model"
        c = build(cfg, features=features, diag=diag)
        diag.emit("built", mode=cfg.mode, is_mock=c.is_mock, features=sorted(c.features), sdk_identity_fields=sorted(c.sdk_identity))
        try:
            c.start()
        except AuthError as e:
            print(f"registration refused: HTTP {e.status} {e.code} (check the runtime key)", file=sys.stderr)
            diag.emit("register_auth_error", status=e.status, code=e.code)
            code = EXIT_AUTH
            return code
        except ApiError as e:
            hint = " (this key belongs to a MOCK runtime; create a runtime with Mock off)" if e.code == "mock_mismatch" else ""
            print(f"registration failed: HTTP {e.status} {e.code}{hint}", file=sys.stderr)
            diag.emit("register_api_error", status=e.status, code=e.code)
            code = EXIT_API
            return code
        logging.getLogger("enigma").info("connected as runtime %s (%s mode, stage %s); Ctrl-C to stop", c.runtime_id, cfg.mode, a.stage)
        if a.stage != "register":
            deadline = None if a.duration <= 0 else time.monotonic() + a.duration
            while not stop.is_set() and not c.stop_event.is_set() and (deadline is None or time.monotonic() < deadline):
                stop.wait(0.5)
        t0 = time.monotonic()
        c.stop()
        diag.emit("stopped", took_s=round(time.monotonic() - t0, 2), stats=dict(c.stats))
        if c.gpu is not None:
            c.gpu.close()
        c.spool.cleanup_if_empty()
        code = EXIT_FATAL if c.fatal else EXIT_OK
        return code
    finally:
        diag.emit("process_exit", code=code)
        if c is not None or a.stage not in OFFLINE_STAGES:
            _summary(a.stage, code, c)
        if fh is not None:
            fh.close()
        diag.close()


if __name__ == "__main__":
    raise SystemExit(main())
