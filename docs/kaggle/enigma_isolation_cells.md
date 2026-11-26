# Enigma × Kaggle isolation diagnostics: superseded by six independent notebooks

The earlier cell list in this file had two defects:
* it ran control **Z1** before the Stokastisk server existed, although Z1 needs that server;
* several cells only printed `passed=False` and let *Run all* continue.

The 2026-11-26 "Test A" also was **not** a CPU-only test. It ran inside a GPU session whose vLLM server was already running.

Use the generated notebooks in [`diagnostics/`](diagnostics/) instead. Each is meant for its **own fresh Kaggle session**. Every step ends with
`VERDICT <step>: PASS | FAIL | INCONCLUSIVE`; anything but PASS raises `DiagnosticStop`, so execution stops. Prerequisites are only accepted
from the current kernel session (attempt id), never from stale outputs. Regenerate with `python3.13 scripts/build_kaggle_diagnostic_notebooks.py`.

| # | Notebook | Accelerator | Tests | Never does |
|---|---|---|---|---|
| A | `enigma_kaggle_cpu_platform_baseline.ipynb` | None | 10-min idle survival, then HTTPS to public controls + Enigma health, 5-min post-network idle, optional pip **dry-run** plan | install, import GPU libs, clone, read secrets |
| B | `enigma_kaggle_cpu_connector_isolation.ipynb` | None | idle, fetch reviewed connector, Kaggle Secret only, self-exiting and SIGTERM children, sentinel, PID/PGID/SID, checkpoint, ZIP | contact Enigma, register a runtime |
| C | `stokastisk_kaggle_gpu_platform_baseline.ipynb` | T4 ×2 | inventory, 10-min idle, controlled build-5 install (dry-run plan, diff, `pip check`), 10-min post-install idle | Enigma traffic, model, server |
| D | `stokastisk_kaggle_vllm_stability.ipynb` | T4 ×2 | server start, verified local inference, 10-min passive observation, 5-min health polling | connector, credential, Railway |
| E | `enigma_kaggle_registration_without_vllm.ipynb` | T4 ×2 | idle, real-NVML offline child, post-exit observation; registration reported **UNSUPPORTED** | send a registration without a model |
| F | `enigma_stokastisk_qwen25_05b_final_integration.ipynb` | T4 ×2 | gated, ordered stages E, F, B, C, D, G, H, I with one real Playground job | run before A–D pass |

Evidence durability: markers are flushed to the cell output **and** fsynced to `markers.jsonl` under a new attempt directory. After a complete platform
termination the VM's files may be gone, but printed cell output survived in the saved notebook. Missing files do not prove that no crash occurred.
