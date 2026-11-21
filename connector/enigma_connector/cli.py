"""``python -m enigma_connector --mode mock|stokastisk``. Configuration comes from the environment; the token is never printed.

stokastisk mode ATTACHES to a vLLM server you already started with ``stokastisk.serving.ServerProcess`` on 127.0.0.1 (see
docs/SDK_INTEGRATION.md). Nothing here starts the server, installs packages or opens an inbound port."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .config import Config, ConfigError
from .gpu import NvmlSampler, NvmlUnavailable
from .http import ApiClient
from .jobs import StokastiskLlm
from .mock import MockGpu, MockLlm, MockVllm
from .runner import Connector
from .spool import Spool
from .vllm_metrics import VllmSampler


def build(cfg: Config) -> Connector:
    api = ApiClient(cfg)
    spool = Spool(cfg.spool_dir or None, cfg.spool_max_bytes)
    if cfg.mode == "mock":
        mv = MockVllm()
        import os
        delay = float(os.environ.get("ENIGMA_MOCK_TOKEN_DELAY_S", "0.02"))        # slow it down to demo/test the Stop button
        return Connector(cfg, api, MockLlm(token_delay_s=delay, vllm=mv), MockGpu(), mv, spool, is_mock=True)
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
        ident["stokastisk_version"] = getattr(stokastisk, "__version__", None)
    except ImportError:
        pass
    return Connector(cfg, api, StokastiskLlm(cfg.vllm_url, cfg.served_model, cfg.stream_read_timeout_s), gpu, VllmSampler(cfg.vllm_url), spool, sdk_identity=ident)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="enigma_connector")
    ap.add_argument("--mode", choices=["mock", "stokastisk"], default=None)
    ap.add_argument("--model", default=None, help="served model name (stokastisk mode)")
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--max-model-len", type=int, default=2048)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    over: dict = {"tp": a.tp, "max_model_len": a.max_model_len}
    if a.mode:
        over["mode"] = a.mode
    if a.model:
        over["served_model"] = a.model
    try:
        cfg = Config.from_env(**over)
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    if cfg.mode == "mock":
        cfg.served_model = cfg.served_model or "mock-model"
    c = build(cfg)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    c.start()
    logging.getLogger("enigma").info("connected as runtime %s (%s mode); Ctrl-C to stop", c.runtime_id, cfg.mode)
    while not stop.is_set() and not c.stop_event.is_set():
        stop.wait(1.0)
    c.stop()
    if c.gpu is not None:
        c.gpu.close()
    c.spool.cleanup_if_empty()
    return 1 if c.fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
